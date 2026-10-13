import hashlib
import io
import json
import os
import random
import re
import sqlite3
import uuid
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from ocr import recognize_pdf_pages, OCRError
from pydantic import BaseModel, Field
from pypdf import PdfReader
from bank import TOPICS, QUESTIONS
import vision
import vision_jobs

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('EXAMGEN_DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'examgen.sqlite3'

def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, digest TEXT UNIQUE, name TEXT, pages INTEGER, extracted TEXT, created TEXT)')
        columns={row['name'] for row in db.execute('PRAGMA table_info(papers)')}
        if 'ocr_pages' not in columns:
            db.execute('ALTER TABLE papers ADD COLUMN ocr_pages INTEGER NOT NULL DEFAULT 0')
        if 'pdf_bytes' not in columns:
            db.execute('ALTER TABLE papers ADD COLUMN pdf_bytes BLOB')
        db.execute('CREATE TABLE IF NOT EXISTS exams (id TEXT PRIMARY KEY, questions TEXT, created TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS visual_pages (paper_id TEXT, page INTEGER, model TEXT, result TEXT, reviewed INTEGER NOT NULL DEFAULT 0, created TEXT, PRIMARY KEY(paper_id,page))')

initialize_database()

app = FastAPI(title='ExamGen', version='1.0.0')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

@app.get('/')
def home():
    return FileResponse(ROOT / 'static/index.html')

@app.get('/api/health')
def health():
    with connect() as db:
        db.execute('SELECT 1').fetchone()
    return {'status': 'ok', 'question_bank_size': len(QUESTIONS)}

def normalize(text):
    return re.sub(r'[^\w]+', ' ', text.lower()).strip()

def topics_for(text):
    clean = ' ' + normalize(text) + ' '
    return [t for t, (_, words) in TOPICS.items() if any(' ' + normalize(w) + ' ' in clean for w in words)]

def passages(text):
    # Keep numbered questions together with options; retain page-level context if unnumbered.
    blocks = re.split(r'(?m)^\s*(?:Q(?:uestion)?\s*)?\d{1,3}[.)]\s+', text)
    return [b.strip() for b in blocks if len(b.strip()) > 20]

@app.post('/api/papers', status_code=201)
async def upload(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, 'Please select a PDF file.')
    payload = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(413, 'Each PDF must be 20 MB or smaller.')
    if not payload.startswith(b'%PDF-'):
        raise HTTPException(400, 'This file does not contain a valid PDF header.')
    digest = hashlib.sha256(payload).hexdigest()
    with connect() as db:
        existing = db.execute('SELECT id,name,pages,ocr_pages,pdf_bytes FROM papers WHERE digest=?', (digest,)).fetchone()
    if existing and existing['pdf_bytes'] is None:
        with connect() as db:
            db.execute('UPDATE papers SET pdf_bytes=? WHERE id=?',(payload,existing['id']))
        return {'id':existing['id'],'name':existing['name'],'pages':existing['pages'],'ocr_pages':existing['ocr_pages'],'source_restored':True}
    if existing:
        raise HTTPException(409, 'This paper is already in your library.')
    try:
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted and not reader.decrypt(''):
            raise HTTPException(400, 'Password-protected PDF: upload an unlocked copy.')
        if len(reader.pages) > 300:
            raise HTTPException(400, 'Please split papers longer than 300 pages.')
        extracted = [p.extract_text() or '' for p in reader.pages]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, 'Could not read this PDF. Try exporting a new copy.')
    try:
        extracted, ocr_count = await run_in_threadpool(recognize_pdf_pages, payload, extracted)
    except OCRError as error:
        raise HTTPException(422, str(error)) from None
    except Exception:
        raise HTTPException(400, 'Could not render this PDF for OCR. Try exporting a new copy.') from None
    if sum(len(t.strip()) for t in extracted) < 30:
        def has_picture():
            from PIL import Image
            for page in range(1,len(extracted)+1):
                image=vision.render_page(payload,page,max_side=500)
                with Image.open(io.BytesIO(image)) as picture:
                    if picture.convert('L').getextrema()[0]<230:return True
            return False
        if not await run_in_threadpool(has_picture):
            raise HTTPException(422, 'No readable text or visible diagram found. This PDF appears blank.')
    if sum(map(len, extracted)) > 2_000_000:
        raise HTTPException(400, 'PDF contains too much text. Please split it into smaller papers.')
    paper_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO papers (id,digest,name,pages,extracted,created,ocr_pages,pdf_bytes) VALUES (?,?,?,?,?,?,?,?)', (paper_id, digest, Path(file.filename).name[:180], len(extracted), json.dumps(extracted), datetime.now(timezone.utc).isoformat(), ocr_count, payload))
    return {'id': paper_id, 'name': Path(file.filename).name, 'pages': len(extracted), 'ocr_pages': ocr_count}

@app.get('/api/analysis')
def analysis():
    with connect() as db:
        papers = db.execute('SELECT id,name,pages,extracted,created,ocr_pages,pdf_bytes IS NOT NULL AS has_images FROM papers ORDER BY created DESC').fetchall()
    stats = {}
    question_total = 0
    question_groups = []
    library = []
    for paper in papers:
        pages = json.loads(paper['extracted'])
        paper_topics = set()
        matches = 0
        for page_number, text in enumerate(pages, 1):
            for block in passages(text):
                topics = topics_for(block)
                if topics:
                    matches += 1
                    question_total += 1
                    tokens = set(normalize(block).split()) - {'a', 'an', 'the', 'of', 'in', 'and', 'is', 'what', 'which', 'to', 'for'}
                    group = next((g for g in question_groups if g['topics'] == topics and len(tokens & g['_tokens']) / max(1, len(tokens | g['_tokens'])) >= 0.65), None)
                    if group is None:
                        group = {'passage': block[:650], 'topics': topics, '_tokens': tokens, '_papers': set(), 'sources': [], 'occurrences': 0}
                        question_groups.append(group)
                    group['_papers'].add(paper['id'])
                    group['occurrences'] += 1
                    if len(group['sources']) < 8:
                        group['sources'].append({'paper': paper['name'], 'page': page_number})
                for topic in topics:
                    paper_topics.add(topic)
                    row = stats.setdefault(topic, {'topic': topic, 'subject': TOPICS[topic][0], 'mentions': 0, 'paper_ids': set(), 'sources': []})
                    row['mentions'] += 1
                    row['paper_ids'].add(paper['id'])
                    if len(row['sources']) < 8:
                        row['sources'].append({'paper': paper['name'], 'page': page_number, 'excerpt': block[:650]})
        library.append({'id': paper['id'], 'name': paper['name'], 'pages': paper['pages'], 'created': paper['created'], 'topics': len(paper_topics), 'matched_passages': matches, 'ocr_pages': paper['ocr_pages'], 'has_images':bool(paper['has_images'])})
    with connect() as db:
        visuals=db.execute('SELECT v.*,p.name FROM visual_pages v JOIN papers p ON p.id=v.paper_id WHERE v.reviewed=1').fetchall()
    visual_counts={}
    for item in visuals:
        detail=json.loads(item['result'])
        for topic in detail.get('topics',[]):
            topic=topic.strip()
            if not topic:continue
            row=stats.setdefault(topic,{'topic':topic,'subject':TOPICS[topic][0] if topic in TOPICS else 'Visual study topics','mentions':0,'paper_ids':set(),'sources':[]})
            row['mentions']+=1;row['paper_ids'].add(item['paper_id'])
            visual_counts[topic]=visual_counts.get(topic,0)+len(detail.get('questions',[]))
            if len(row['sources'])<8:
                row['sources'].append({'paper':item['name'],'page':item['page'],'excerpt':detail['description'][:650],'origin':'local vision, reviewed'})
    for paper in library:
        paper['topics']=sum(paper['id'] in row['paper_ids'] for row in stats.values())
    result = []
    for row in stats.values():
        count = len(row.pop('paper_ids'))
        row['paper_count'] = count
        row['coverage'] = round(count / len(papers) * 100)
        row['practice_count'] = sum(q['topic'] == row['topic'] for q in QUESTIONS)+visual_counts.get(row['topic'],0)
        result.append(row)
    result.sort(key=lambda r: (-r['paper_count'], -r['mentions'], r['topic']))
    high_yield = []
    for group in question_groups:
        group['paper_count'] = len(group.pop('_papers'))
        group.pop('_tokens')
        # Prioritize passage recurrence, then recurrence of its medical topics.
        group['topic_recurrence'] = max((r['paper_count'] for r in result if r['topic'] in group['topics']), default=0)
        high_yield.append(group)
    high_yield.sort(key=lambda g: (-g['paper_count'], -g['topic_recurrence'], -g['occurrences']))
    return {'papers': library, 'topics': result, 'high_yield_questions': high_yield[:30], 'matched_passages': question_total, 'recurring_topics': sum(t['paper_count'] > 1 for t in result), 'method': 'Topics ranked by number of distinct papers, then matching passages. Keyword matching is an estimate, not a prediction of future exam questions.'}

@app.delete('/api/papers/{paper_id}')
def delete(paper_id: str):
    with connect() as db:
        db.execute('DELETE FROM visual_pages WHERE paper_id=?',(paper_id,))
        cursor = db.execute('DELETE FROM papers WHERE id=?', (paper_id,))
    if not cursor.rowcount:
        raise HTTPException(404, 'Paper not found.')
    return {'deleted': True}

class ExamRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=30)
    topics: list[str] = Field(default_factory=list)
    demo: bool = False
    include_visual: bool = False

@app.post('/api/exams')
def generate(request: ExamRequest):
    ranked = analysis()['topics']
    available = {t['topic']: t['paper_count'] * 3 + t['mentions'] for t in ranked}
    if request.demo:
        available = {t: 1 for t in TOPICS}
    if request.topics:
        if any(t not in available for t in request.topics):
            raise HTTPException(400, 'Select topics present in the current analysis.')
        available = {t: available[t] for t in request.topics}
    pool = [q for q in QUESTIONS if q['topic'] in available]
    if request.include_visual and not request.demo:
        with connect() as db:
            visuals=db.execute('SELECT v.*,p.name FROM visual_pages v JOIN papers p ON p.id=v.paper_id WHERE v.reviewed=1').fetchall()
        for item in visuals:
            detail=json.loads(item['result'])
            if request.topics and not set(detail.get('topics',[])).intersection(request.topics):
                continue
            for index,q in enumerate(detail.get('questions',[])):
                image_url=f"/api/papers/{item['paper_id']}/pages/{item['page']}/image"
                if detail.get('region'):
                    image_url+='?region='+urllib.parse.quote(json.dumps(detail['region']))
                pool.append({**q,'id':f"visual-{item['paper_id']}-{item['page']}-{index}",'topic':detail.get('topics',['Visual practice'])[0] if detail.get('topics') else 'Visual practice','image':image_url,'source':f"{item['name']} · page {item['page']}",'origin':'local vision, reviewed'})
                available[pool[-1]['topic']]=max(1,available.get(pool[-1]['topic'],1))
    if not pool:
        raise HTTPException(422, 'No supported topics found. Upload medical past papers or try the practice demo.')
    # Weighted sampling without replacement; recurring topics are more likely.
    rng = random.SystemRandom()
    pool.sort(key=lambda q: rng.random() ** (1 / available[q['topic']]), reverse=True)
    chosen = []
    for q in pool[:request.count]:
        options = list(enumerate(q['options']))
        rng.shuffle(options)
        chosen.append({**q, 'options': [o for _, o in options], 'answer': next(i for i, (old, _) in enumerate(options) if old == q['answer'])})
    exam_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO exams VALUES (?,?,?)', (exam_id, json.dumps(chosen), datetime.now(timezone.utc).isoformat()))
    return {'id': exam_id, 'questions': [{k: v for k, v in q.items() if k not in ('answer', 'explanation')} for q in chosen], 'requested': request.count, 'actual': len(chosen), 'demo': request.demo, 'note': 'Practice questions from the included medical bank and, when enabled, reviewed local vision drafts. Check visual answers against the source. No duplicate questions within an exam.'}

class Submission(BaseModel):
    answers: dict[str, int] = Field(default_factory=dict)

@app.post('/api/exams/{exam_id}/submit')
def submit(exam_id: str, submission: Submission):
    with connect() as db:
        row = db.execute('SELECT questions FROM exams WHERE id=?', (exam_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Exam not found. Generate a new exam.')
    questions = json.loads(row['questions'])
    ids = {q['id'] for q in questions}
    if any(k not in ids or v not in range(4) for k, v in submission.answers.items()):
        raise HTTPException(400, 'Invalid question or answer option.')
    results = [{**q, 'selected': submission.answers.get(q['id']), 'correct': submission.answers.get(q['id']) == q['answer']} for q in questions]
    score = sum(q['correct'] for q in results)
    return {'score': score, 'total': len(results), 'percent': round(score / len(results) * 100), 'results': results}


def get_paper(paper_id):
    with connect() as db:
        row=db.execute('SELECT * FROM papers WHERE id=?',(paper_id,)).fetchone()
    if not row:raise HTTPException(404,'Paper not found.')
    return row


def source_paper(paper_id):
    row=get_paper(paper_id)
    if row['pdf_bytes'] is None:raise HTTPException(409,'Upload this same PDF once more to restore its page images. Your saved paper history is preserved.')
    return row

@app.get('/api/papers/{paper_id}/pages/{page}/image')
def page_image(paper_id:str,page:int,region:str|None=Query(default=None,max_length=200)):
    row=source_paper(paper_id)
    crop=None
    if region is not None:
        try:
            crop=json.loads(region)
            x,y,w,h=crop
            if any(type(v) not in (int,float) for v in crop) or not all(0<=v<=1 for v in crop) or w<.02 or h<.02 or x+w>1.00001 or y+h>1.00001:raise ValueError()
        except (ValueError,TypeError):raise HTTPException(400,'Invalid source image crop.') from None
    try:image=vision.render_page(row['pdf_bytes'],page,crop)
    except vision.VisionError as error:raise HTTPException(400,str(error)) from None
    return Response(image,media_type='image/jpeg',headers={'Cache-Control':'private, no-store'})

@app.get('/api/papers/{paper_id}/visuals')
def visual_results(paper_id:str):
    paper=get_paper(paper_id)
    with connect() as db:
        rows=db.execute('SELECT * FROM visual_pages WHERE paper_id=? ORDER BY page',(paper_id,)).fetchall()
    return {'id':paper_id,'name':paper['name'],'pages':paper['pages'],'has_images':paper['pdf_bytes'] is not None,'results':[{'page':r['page'],'model':r['model'],'reviewed':bool(r['reviewed']),'created':r['created'],**json.loads(r['result'])} for r in rows]}

@app.get('/api/vision/status')
def vision_status():
    try:return vision.status()
    except vision.VisionError as error:raise HTTPException(400,str(error)) from None

@app.post('/api/vision/download')
def download_model():
    try:
        vision.local_url();vision.model_name()
        def worker(job_id):
            vision.pull_model(lambda message,percent:vision_jobs.update(job_id,message=message,percent=percent),lambda:vision_jobs.get(job_id)['cancelled'])
            if not vision_jobs.get(job_id)['cancelled']:
                ready=vision.status()
                if not ready['ready']:raise vision.VisionError(ready['message'])
                vision_jobs.update(job_id,message='Vision model is ready on your PC.',percent=100)
        return vision_jobs.start('download',worker)
    except (RuntimeError,vision.VisionError) as error:raise HTTPException(409,str(error)) from None

from typing import Literal

class VisionRequest(BaseModel):
    mode:Literal["interpret","similar"]="interpret"
    original_question:str=Field(default="",max_length=1200)
    pages:list[int]=Field(min_length=1,max_length=20)
    question:str=Field(default='',max_length=500)
    region:list[float]|None=None

@app.post('/api/papers/{paper_id}/vision')
def analyze_visuals(paper_id:str,request:VisionRequest):
    paper=source_paper(paper_id)
    pages=sorted(set(request.pages))
    if any(not 1<=p<=paper['pages'] for p in pages):raise HTTPException(400,'Select valid page numbers from this paper.')
    if request.region is not None:
        if len(pages)!=1 or len(request.region)!=4:raise HTTPException(400,'A diagram crop can only be used with one page.')
        x,y,w,h=request.region
        if not all(0<=v<=1 for v in request.region) or w<.02 or h<.02 or x+w>1.00001 or y+h>1.00001:raise HTTPException(400,'Select a valid image crop.')
    if request.mode=='similar' and (len(pages)!=1 or request.region is None):
        raise HTTPException(400,'Crop the diagram alone on one page before generating similar questions, so the original question and answer key are not shown in the mock exam.')
    ready=vision.status()
    if not ready['ready']:raise HTTPException(409,ready['message'])
    texts=json.loads(paper['extracted'])
    def worker(job_id):
        vision_jobs.update(job_id,total=len(pages))
        errors=[]
        for i,page in enumerate(pages):
            if vision_jobs.get(job_id)['cancelled']:return
            vision_jobs.update(job_id,message=f'Interpreting page {page} locally…')
            try:
                image=vision.render_page(paper['pdf_bytes'],page,request.region,max_side=1120)
                detail=vision.interpret(image,texts[page-1],request.question,mode='similar',original_question=request.original_question) if request.mode=='similar' else vision.interpret(image,texts[page-1],request.question)
                if request.mode=='similar' and not detail['questions']:
                    raise vision.VisionError('The model could not create reliable variations. Paste the original question and choose a clearer diagram crop. '+ '; '.join(detail['uncertainties'])[:200])
                detail['mode']=request.mode
                detail['original_question']=request.original_question
                detail['region']=request.region
                detail['student_question']=request.question
                with connect() as db:
                    if not db.execute('SELECT 1 FROM papers WHERE id=?',(paper_id,)).fetchone():return
                    db.execute('INSERT INTO visual_pages VALUES (?,?,?,?,?,?) ON CONFLICT(paper_id,page) DO UPDATE SET model=excluded.model,result=excluded.result,reviewed=0,created=excluded.created',(paper_id,page,vision.model_name(),json.dumps(detail),0,datetime.now(timezone.utc).isoformat()))
            except Exception as error:errors.append(f'Page {page}: {str(error)[:400]}')
            vision_jobs.update(job_id,completed=i+1,errors=list(errors))
        vision_jobs.update(job_id,message='Local analysis complete.' if not errors else 'Finished with page errors. Other results were saved.')
        if len(errors)==len(pages):raise vision.VisionError(errors[0])
    try:return vision_jobs.start('analysis',worker)
    except RuntimeError as error:raise HTTPException(409,str(error)) from None

@app.get('/api/vision/jobs/{job_id}')
def vision_job(job_id:str):
    job=vision_jobs.get(job_id)
    if not job:raise HTTPException(404,'This job is no longer active. Saved interpretations remain in the library.')
    return job

@app.post('/api/vision/jobs/{job_id}/cancel')
def cancel_vision_job(job_id:str):
    job=vision_jobs.cancel(job_id)
    if not job:raise HTTPException(404,'Job not found.')
    return job

class Review(BaseModel):
    reviewed:bool

@app.patch('/api/papers/{paper_id}/visuals/{page}')
def review_visual(paper_id:str,page:int,review:Review):
    with connect() as db:
        cursor=db.execute('UPDATE visual_pages SET reviewed=? WHERE paper_id=? AND page=?',(int(review.reviewed),paper_id,page))
    if not cursor.rowcount:raise HTTPException(404,'Interpretation not found.')
    return {'reviewed':review.reviewed}
