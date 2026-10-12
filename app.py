import hashlib
import io
import json
import os
import random
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader
from bank import TOPICS, QUESTIONS

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('EXAMGEN_DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'examgen.sqlite3'

def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

with connect() as db:
    db.execute('CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, digest TEXT UNIQUE, name TEXT, pages INTEGER, extracted TEXT, created TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS exams (id TEXT PRIMARY KEY, questions TEXT, created TEXT)')

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
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()

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
        existing = db.execute('SELECT id FROM papers WHERE digest=?', (digest,)).fetchone()
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
    if sum(len(t.strip()) for t in extracted) < 30:
        raise HTTPException(422, 'No readable text found. This may be a scanned PDF; run OCR before uploading.')
    if sum(map(len, extracted)) > 2_000_000:
        raise HTTPException(400, 'PDF contains too much text. Please split it into smaller papers.')
    paper_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?)', (paper_id, digest, Path(file.filename).name[:180], len(extracted), json.dumps(extracted), datetime.now(timezone.utc).isoformat()))
    return {'id': paper_id, 'name': Path(file.filename).name, 'pages': len(extracted)}

@app.get('/api/analysis')
def analysis():
    with connect() as db:
        papers = db.execute('SELECT * FROM papers ORDER BY created DESC').fetchall()
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
        library.append({'id': paper['id'], 'name': paper['name'], 'pages': paper['pages'], 'created': paper['created'], 'topics': len(paper_topics), 'matched_passages': matches})
    result = []
    for row in stats.values():
        count = len(row.pop('paper_ids'))
        row['paper_count'] = count
        row['coverage'] = round(count / len(papers) * 100)
        row['practice_count'] = sum(q['topic'] == row['topic'] for q in QUESTIONS)
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
        cursor = db.execute('DELETE FROM papers WHERE id=?', (paper_id,))
    if not cursor.rowcount:
        raise HTTPException(404, 'Paper not found.')
    return {'deleted': True}

class ExamRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=30)
    topics: list[str] = Field(default_factory=list)
    demo: bool = False

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
    return {'id': exam_id, 'questions': [{k: v for k, v in q.items() if k not in ('answer', 'explanation')} for q in chosen], 'requested': request.count, 'actual': len(chosen), 'demo': request.demo, 'note': 'Practice questions from the included medical bank, selected by topic; not extracted answer keys. No duplicate questions within an exam.'}

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
