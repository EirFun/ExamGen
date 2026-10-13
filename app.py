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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from ocr import recognize_pdf_pages, OCRError
from pydantic import BaseModel, Field
from pypdf import PdfReader
from bank import TOPICS, QUESTIONS
import vision
import vision_jobs
import books
from typing import Literal

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
        if 'kind' not in columns:
            db.execute("ALTER TABLE papers ADD COLUMN kind TEXT NOT NULL DEFAULT 'exam'")
        books.initialize(db)
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS exams (id TEXT PRIMARY KEY, questions TEXT, created TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS visual_pages (paper_id TEXT, page INTEGER, model TEXT, result TEXT, reviewed INTEGER NOT NULL DEFAULT 0, created TEXT, PRIMARY KEY(paper_id,page))')

initialize_database()
with connect() as db:
    saved_model=db.execute("SELECT value FROM settings WHERE key='vision_model'").fetchone()
if saved_model and not os.environ.get('EXAMGEN_VISION_MODEL'):os.environ['EXAMGEN_VISION_MODEL']=saved_model['value']

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
async def upload(file: UploadFile = File(...),kind:Literal["exam","book","syllabus"]=Form(default="exam")):
    image_upload=kind=='syllabus' and file.filename and file.filename.lower().endswith(('.png','.jpg','.jpeg','.webp'))
    if not file.filename or (not file.filename.lower().endswith('.pdf') and not image_upload):
        raise HTTPException(400, 'Please select a PDF file.')
    size_limit=(60 if kind=="book" else 20)*1024*1024
    payload = await file.read(size_limit + 1)
    await file.close()
    if len(payload) > size_limit:
        raise HTTPException(413, f'Each {kind} PDF must be {size_limit//1024//1024} MB or smaller.')
    digest=hashlib.sha256(payload).hexdigest()
    if image_upload:
        from PIL import Image,UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(payload)) as image:
                if image.width*image.height>20_000_000:raise HTTPException(413,'Syllabus image exceeds 20 megapixels.')
                converted=io.BytesIO();rgb=image.convert('RGB');rgb.save(converted,format='PDF',resolution=150);rgb.close();payload=converted.getvalue()
        except HTTPException:raise
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):raise HTTPException(400,'Could not read this syllabus image. Use PNG, JPEG or WebP.') from None
    if not payload.startswith(b'%PDF-'):
        raise HTTPException(400, 'This file does not contain a valid PDF header.')
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
        if len(reader.pages) > (1200 if kind=="book" else 300):
            raise HTTPException(400, f'Please split PDFs longer than {1200 if kind=="book" else 300} pages.')
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
        if kind=='syllabus':raise HTTPException(422,'No readable syllabus text found. Upload a clearer photo or a text PDF.')
        def has_picture():
            from PIL import Image
            for page in range(1,len(extracted)+1):
                image=vision.render_page(payload,page,max_side=500)
                with Image.open(io.BytesIO(image)) as picture:
                    if picture.convert('L').getextrema()[0]<230:return True
            return False
        if not await run_in_threadpool(has_picture):
            raise HTTPException(422, 'No readable text or visible diagram found. This PDF appears blank.')
    if sum(map(len, extracted)) > (6_000_000 if kind=="book" else 2_000_000):
        raise HTTPException(400, 'PDF contains too much text. Please split it into smaller papers.')
    paper_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO papers (id,digest,name,pages,extracted,created,ocr_pages,pdf_bytes,kind) VALUES (?,?,?,?,?,?,?,?,?)', (paper_id, digest, Path(file.filename).name[:180], len(extracted), json.dumps(extracted), datetime.now(timezone.utc).isoformat(), ocr_count, payload,kind))
        if kind in ("book","syllabus"):books.index(db,paper_id,extracted)
    return {'id': paper_id, 'name': Path(file.filename).name, 'pages': len(extracted), 'ocr_pages': ocr_count,'kind':kind}

@app.get('/api/analysis')
def analysis():
    with connect() as db:
        papers = db.execute("SELECT id,name,pages,extracted,created,ocr_pages,pdf_bytes IS NOT NULL AS has_images FROM papers WHERE kind='exam' ORDER BY created DESC").fetchall()
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
        visuals=db.execute('SELECT v.*,p.name FROM visual_pages v JOIN papers p ON p.id=v.paper_id WHERE v.reviewed=1 AND p.kind="exam"').fetchall()
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
    with connect() as db:
        book_list=[dict(r) for r in db.execute("SELECT id,name,pages,created,ocr_pages,pdf_bytes IS NOT NULL AS has_images,kind FROM papers WHERE kind='book' ORDER BY created DESC")]
        syllabi=[dict(r) for r in db.execute("SELECT id,name,pages,created,ocr_pages,pdf_bytes IS NOT NULL AS has_images,kind FROM papers WHERE kind='syllabus' ORDER BY created DESC")]
    return {'syllabi':syllabi,'books':book_list,'papers': library, 'topics': result, 'high_yield_questions': high_yield[:30], 'matched_passages': question_total, 'recurring_topics': sum(t['paper_count'] > 1 for t in result), 'method': 'Topics ranked by number of distinct papers, then matching passages. Keyword matching is an estimate, not a prediction of future exam questions.'}

class LibraryKind(BaseModel):
    kind:Literal['exam','book','syllabus']

@app.patch('/api/papers/{paper_id}/kind')
def classify(paper_id:str,request:LibraryKind):
    with connect() as db:
        row=db.execute('SELECT extracted FROM papers WHERE id=?',(paper_id,)).fetchone()
        if not row:raise HTTPException(404,'PDF not found.')
        db.execute('UPDATE papers SET kind=? WHERE id=?',(request.kind,paper_id))
        db.execute('DELETE FROM book_chunks WHERE book_id=?',(paper_id,))
        if request.kind in ('book','syllabus'):books.index(db,paper_id,json.loads(row['extracted']))
    return {'kind':request.kind}

@app.get('/api/books/search')
def book_search(query:str=Query(min_length=3,max_length=1200)):
    with connect() as db:return {'sources':books.retrieve(db,query)}

@app.delete('/api/papers/{paper_id}')
def delete(paper_id: str):
    with connect() as db:
        db.execute('DELETE FROM book_chunks WHERE book_id=?',(paper_id,))
        db.execute('DELETE FROM visual_pages WHERE paper_id=?',(paper_id,))
        cursor = db.execute('DELETE FROM papers WHERE id=?', (paper_id,))
    if not cursor.rowcount:
        raise HTTPException(404, 'Paper not found.')
    return {'deleted': True}

class ExamRequest(BaseModel):
    syllabus_id:str|None=Field(default=None,max_length=40)
    count: int = Field(default=10, ge=1, le=30)
    topics: list[str] = Field(default_factory=list)
    demo: bool = False
    include_visual: bool = False
    question_type:Literal["mcq","subjective"]="mcq"

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
    pool = [q for q in QUESTIONS if q['topic'] in available] if request.question_type=='mcq' else []
    if request.include_visual and not request.demo:
        with connect() as db:
            visuals=db.execute('SELECT v.*,p.name FROM visual_pages v JOIN papers p ON p.id=v.paper_id WHERE v.reviewed=1 AND p.kind IN ("exam","book")').fetchall()
        for item in visuals:
            detail=json.loads(item['result'])
            if request.topics and not set(detail.get('topics',[])).intersection(request.topics):
                continue
            for index,q in enumerate(detail.get('questions',[])):
                if q.get('type','mcq')!=request.question_type:continue
                image_url=f"/api/papers/{item['paper_id']}/pages/{item['page']}/image"
                if detail.get('region'):
                    image_url+='?region='+urllib.parse.quote(json.dumps(detail['region']))
                pool.append({**q,'id':f"visual-{item['paper_id']}-{item['page']}-{index}",'topic':detail.get('topics',['Visual practice'])[0] if detail.get('topics') else 'Visual practice','image':image_url,'source':f"{item['name']} · page {item['page']}",'origin':'local vision, reviewed'})
                available[pool[-1]['topic']]=max(1,available.get(pool[-1]['topic'],1))
    if request.syllabus_id:
        with connect() as db:
            if not db.execute("SELECT 1 FROM papers WHERE id=? AND kind='syllabus'",(request.syllabus_id,)).fetchone():raise HTTPException(400,'Select a valid syllabus.')
            pool=[q for q in pool if books.retrieve(db,q['topic']+' '+q['stem'],kind='syllabus',document_id=request.syllabus_id,limit=1)]
    if not pool:
        raise HTTPException(422, 'No reviewed subjective questions found. Create practical diagram questions in Diagram lab or generate from books & exam style.' if request.question_type=='subjective' else 'No supported topics found. Upload medical past papers or try the practice demo.')
    # Weighted sampling without replacement; recurring topics are more likely.
    rng = random.SystemRandom()
    pool.sort(key=lambda q: rng.random() ** (1 / available[q['topic']]), reverse=True)
    chosen = []
    for q in pool[:request.count]:
        if q.get('type')=='subjective':
            chosen.append(q);continue
        options = list(enumerate(q['options']))
        rng.shuffle(options)
        chosen.append({**q, 'options': [o for _, o in options], 'answer': next(i for i, (old, _) in enumerate(options) if old == q['answer'])})
    exam_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO exams VALUES (?,?,?)', (exam_id, json.dumps(chosen), datetime.now(timezone.utc).isoformat()))
    return {'id': exam_id, 'questions': [{k: v for k, v in q.items() if k not in ('answer','answer_text','marking_points','explanation','book_sources','citations')} for q in chosen], 'requested': request.count, 'actual': len(chosen), 'demo': request.demo, 'note': 'Practice questions from the included medical bank and, when enabled, reviewed local vision drafts. Check visual answers against the source. No duplicate questions within an exam.'}

class GroundedRequest(BaseModel):
    syllabus_id:str|None=Field(default=None,max_length=40)
    count:int=Field(default=5,ge=1,le=30)
    topics:list[str]=Field(default_factory=list,max_length=30)
    question_type:Literal["mcq","subjective"]="mcq"

@app.post('/api/exams/grounded')
def generate_grounded(request:GroundedRequest):
    with connect() as db:
        if request.syllabus_id and not db.execute("SELECT 1 FROM papers WHERE id=? AND kind='syllabus'",(request.syllabus_id,)).fetchone():raise HTTPException(400,"Choose a valid syllabus.")
        if not db.execute("SELECT 1 FROM papers WHERE kind='book'").fetchone():raise HTTPException(409,'Add a PDF book for answer references.')
        rows=db.execute("SELECT name,extracted FROM papers WHERE kind='exam' ORDER BY created DESC").fetchall()
    examples=[]
    for paper in rows:
        for page,text in enumerate(json.loads(paper['extracted']),1):
            for block in passages(text):
                if request.topics and not set(topics_for(block)).intersection(request.topics):continue
                examples.append({'exam':paper['name'],'page':page,'text':block[:900]})
                if len(examples)>=60:break
            if len(examples)>=60:break
        if len(examples)>=60:break
    if request.question_type=='subjective':
        with connect() as db:
            diagrams=db.execute("SELECT v.*,p.name,p.extracted,p.pdf_bytes FROM visual_pages v JOIN papers p ON p.id=v.paper_id WHERE p.kind='exam'").fetchall()
        examples=[{'exam':r['name'],'page':r['page'],'text':json.loads(r['result']).get('original_question','')+' '+json.loads(r['result'])['description'],'paper_id':r['paper_id'],'region':json.loads(r['result']).get('region'),'verified_label':json.loads(r['result']).get('verified_label','')} for r in diagrams if json.loads(r['result']).get('region') and (not request.topics or set(json.loads(r['result']).get('topics',[])).intersection(request.topics))]
    if not examples:raise HTTPException(409,'Add readable exam questions. For subjective practical exams, first crop and analyze an exam diagram in Diagram lab.')
    def worker(job_id):
        drafts=[];errors=[];seen=set();vision_jobs.update(job_id,total=request.count)
        for offset in range(0,request.count,3):
            if vision_jobs.get(job_id)['cancelled']:return
            example=examples[(offset//3)%len(examples)]
            vision_jobs.update(job_id,message='Finding book passages for exam examples…',response_chars=0)
            query=example['text']+' '+' '.join(request.topics)
            with connect() as db:references=books.retrieve(db,query,limit=2)
            try:
                with connect() as db:scope=books.syllabus_sources(db,request.syllabus_id,query)
            except ValueError as error:errors.append(str(error));continue
            if not references:
                errors.append('No book evidence for '+example['exam']+' page '+str(example['page']));continue
            try:
                progress=lambda message,chars:vision_jobs.update(job_id,message=message,response_chars=chars)
                if request.question_type=='subjective':
                    source=source_paper(example['paper_id'])
                    image=vision.render_page(source['pdf_bytes'],example['page'],example['region'],max_side=768)
                    batch=vision.interpret(image,'',mode='similar',original_question=example['text'],references=references,question_count=min(3,request.count-offset),question_type='subjective',syllabus=scope,verified_label=example.get('verified_label',''),progress=progress)
                else:batch=vision.grounded_questions([example],references,min(3,request.count-offset),progress=progress,syllabus=scope)
                for q in batch['questions'][:request.count-len(drafts)]:
                    if q.get('type','mcq')!=request.question_type:continue
                    if request.question_type=='subjective':
                        q['image']=f"/api/papers/{example['paper_id']}/pages/{example['page']}/image?region="+urllib.parse.quote(json.dumps(example['region']))
                        q['source']=f"{example['exam']} · page {example['page']}"
                    stem=normalize(q['stem'])
                    if stem in seen or stem==normalize(example['text']):continue
                    books.validate_syllabus(q,scope)
                    q['book_sources']=books.validate_citations(q.pop('citations',[]),references)
                    q['exam_sources']=[{'exam':example['exam'],'page':example['page']}]
                    q['topic']=request.topics[0] if request.topics else (batch['topics'][0] if batch['topics'] else 'Book-grounded practice')
                    q['origin']='local model, book-grounded draft';q['id']=uuid.uuid4().hex
                    seen.add(stem);drafts.append(q)
                vision_jobs.update(job_id,completed=len(drafts),errors=list(errors),performance=batch.get('performance'))
            except Exception as error:errors.append(str(error)[:400]);vision_jobs.update(job_id,errors=list(errors))
        if not drafts:raise vision.VisionError('No supported questions generated. '+(' '.join(errors)[:450] or 'Try a more specific topic and relevant book section.'))
        vision_jobs.update(job_id,draft={'questions':drafts,'requested':request.count},message='Draft ready. Review the answers and book quotations before using it.')
    try:return vision_jobs.start('exam',worker)
    except RuntimeError as error:raise HTTPException(409,str(error)) from None

@app.post('/api/exams/grounded/{job_id}/accept')
def accept_grounded(job_id:str):
    job=vision_jobs.get(job_id)
    if not job or job['state']!='completed' or not job.get('draft'):raise HTTPException(409,'Wait for a completed book-grounded draft.')
    if job.get('accepted'):return job['accepted']
    chosen=[];rng=random.SystemRandom()
    for q in job['draft']['questions']:
        if q.get('type')=='subjective':
            chosen.append({**q,'origin':'local model, book-grounded, reviewed'});continue
        options=list(enumerate(q['options']));rng.shuffle(options)
        chosen.append({**q,'options':[v for _,v in options],'answer':next(i for i,(old,_) in enumerate(options) if old==q['answer']),'origin':'local model, book-grounded, reviewed'})
    exam_id=uuid.uuid4().hex
    with connect() as db:db.execute('INSERT INTO exams VALUES (?,?,?)',(exam_id,json.dumps(chosen),datetime.now(timezone.utc).isoformat()))
    result={'id':exam_id,'questions':[{k:v for k,v in q.items() if k not in ('answer','answer_text','marking_points','explanation','book_sources','citations')} for q in chosen],'requested':job['draft']['requested'],'actual':len(chosen),'demo':False,'note':'Questions drafted from exam style examples; answers use reviewed quotations from your books. No external service was used.'}
    vision_jobs.update(job_id,accepted=result)
    return result

class Submission(BaseModel):
    answers: dict[str, int] = Field(default_factory=dict)
    subjective_answers:dict[str,str]=Field(default_factory=dict)
    self_marks:dict[str,bool]=Field(default_factory=dict)

@app.post('/api/exams/{exam_id}/submit')
def submit(exam_id: str, submission: Submission):
    with connect() as db:
        row = db.execute('SELECT questions FROM exams WHERE id=?', (exam_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Exam not found. Generate a new exam.')
    questions = json.loads(row['questions'])
    ids = {q['id'] for q in questions if q.get('type')!='subjective'}
    subjective_ids={q['id'] for q in questions if q.get('type')=='subjective'}
    if any(k not in subjective_ids or len(v)>10000 for k,v in submission.subjective_answers.items()) or any(k not in subjective_ids for k in submission.self_marks):raise HTTPException(400,'Invalid subjective response.')
    if any(k not in ids or v not in range(4) for k, v in submission.answers.items()):
        raise HTTPException(400, 'Invalid question or answer option.')
    results=[{**q,'response':submission.subjective_answers.get(q['id'],''),'correct':submission.self_marks.get(q['id']),'selected':None} if q.get('type')=='subjective' else {**q,'selected':submission.answers.get(q['id']),'correct':submission.answers.get(q['id'])==q['answer']} for q in questions]
    scored=[q for q in results if q['correct'] is not None];score=sum(q['correct'] for q in scored)
    return {'score':score,'total':len(results),'percent':round(score/len(scored)*100) if scored and len(scored)==len(results) else None,'self_assessment_pending':any(q['correct'] is None for q in results),'results':results}


def get_paper(paper_id):
    with connect() as db:
        row=db.execute('SELECT * FROM papers WHERE id=?',(paper_id,)).fetchone()
    if not row:raise HTTPException(404,'Paper not found.')
    return row


def source_paper(paper_id):
    row=get_paper(paper_id)
    if row['pdf_bytes'] is None:raise HTTPException(409,'Upload this same PDF once more to restore its page images. Your saved paper history is preserved.')
    return row

@app.get('/api/papers/{paper_id}/source.pdf')
def source_pdf(paper_id:str):
    return Response(source_paper(paper_id)['pdf_bytes'],media_type='application/pdf',headers={'Cache-Control':'private, no-store'})

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

class ModelChoice(BaseModel):
    model:Literal['qwen2.5vl:3b','qwen2.5vl:7b']

@app.patch('/api/vision/model')
def choose_model(request:ModelChoice):
    def change():
        with connect() as db:db.execute("INSERT INTO settings VALUES ('vision_model',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(request.model,))
        os.environ['EXAMGEN_VISION_MODEL']=request.model
        return {'model':request.model}
    try:return vision_jobs.configure(change)
    except RuntimeError as error:raise HTTPException(409,str(error)) from None

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

class VisionRequest(BaseModel):
    syllabus_id:str|None=Field(default=None,max_length=40)
    mode:Literal["interpret","similar"]="interpret"
    original_question:str=Field(default="",max_length=1200)
    use_books:bool=False
    question_count:int=Field(default=1,ge=1,le=3)
    speed:Literal["fast","detail"]="fast"
    verified_label:str=Field(default="",max_length=160)
    question_type:Literal["mcq","subjective"]="mcq"
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
    if request.syllabus_id:
        with connect() as db:
            if not db.execute("SELECT 1 FROM papers WHERE id=? AND kind='syllabus'",(request.syllabus_id,)).fetchone():raise HTTPException(400,'Choose a valid syllabus.')
    if request.use_books:
        with connect() as db:
            if not db.execute("SELECT 1 FROM papers WHERE kind='book'").fetchone():raise HTTPException(409,'Add a PDF book before enabling book-grounded answers.')
    texts=json.loads(paper['extracted'])
    def worker(job_id):
        vision_jobs.update(job_id,total=len(pages))
        errors=[]
        for i,page in enumerate(pages):
            if vision_jobs.get(job_id)['cancelled']:return
            vision_jobs.update(job_id,message=f'Interpreting page {page} locally…')
            try:
                image=vision.render_page(paper['pdf_bytes'],page,request.region,max_side=768 if request.speed=="fast" else 1120)
                references=[]
                with connect() as db:scope=books.syllabus_sources(db,request.syllabus_id,request.original_question+' '+request.question+' '+texts[page-1][:1600])
                if request.use_books:
                    with connect() as db:references=books.retrieve(db,request.original_question+' '+request.question+' '+texts[page-1][:1600],limit=2)
                    if not references:raise vision.VisionError('No relevant book passages found. Enter the topic or paste the original question.')
                detail=vision.interpret(image,texts[page-1],request.question,mode=request.mode,original_question=request.original_question,references=references,question_count=request.question_count,question_type=request.question_type,syllabus=scope,verified_label=request.verified_label,progress=lambda message,chars:vision_jobs.update(job_id,message=f'Page {page}: {message}',response_chars=chars))
                for q in detail['questions']:books.validate_syllabus(q,scope)
                if request.use_books:
                    for q in detail['questions']:q['book_sources']=books.validate_citations(q.pop('citations',[]),references)
                detail['book_grounded']=request.use_books
                if request.mode=='similar' and not detail['questions']:
                    raise vision.VisionError('The model could not create reliable variations. Paste the original question and choose a clearer diagram crop. '+ '; '.join(detail['uncertainties'])[:200])
                detail['verified_label']=request.verified_label
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
