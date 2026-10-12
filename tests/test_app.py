import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from pypdf import PdfWriter
import app as module

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'DB', tmp_path / 'test.sqlite3')
    with module.connect() as db:
        db.execute('CREATE TABLE papers (id TEXT PRIMARY KEY, digest TEXT UNIQUE, name TEXT, pages INTEGER, extracted TEXT, created TEXT)')
        db.execute('CREATE TABLE exams (id TEXT PRIMARY KEY, questions TEXT, created TEXT)')
    with TestClient(module.app) as client:
        yield client

def pdf(*pages):
    output = io.BytesIO()
    document = canvas.Canvas(output)
    for lines in pages:
        y = 750
        for line in lines:
            document.drawString(40, y, line)
            y -= 20
        document.showPage()
    document.save()
    return output.getvalue()

def upload(client, name, payload):
    return client.post('/api/papers', files={'file': (name, payload, 'application/pdf')})

def test_pdf_to_recurring_analysis_and_exam(client):
    first = pdf(['1. Explain diabetic ketoacidosis and insulin treatment.', '2. What causes myocardial infarction and elevated troponin?'], ['3. Discuss asthma and bronchodilator therapy.'])
    second = pdf(['1. Explain the diagnosis of diabetes mellitus and insulin deficiency.', '2. Describe anemia and iron deficiency.'])
    a = upload(client, 'medicine-2023.pdf', first)
    assert a.status_code == 201
    assert a.json()['pages'] == 2
    assert upload(client, 'medicine-2024.pdf', second).status_code == 201
    analysis = client.get('/api/analysis').json()
    assert len(analysis['papers']) == 2
    assert analysis['recurring_topics'] == 1
    assert analysis['matched_passages'] == 5
    diabetes = analysis['topics'][0]
    assert diabetes['topic'] == 'Diabetes mellitus'
    assert diabetes['paper_count'] == 2
    assert diabetes['coverage'] == 100
    asthma = next(t for t in analysis['topics'] if t['topic'] == 'Asthma')
    assert asthma['sources'][0]['page'] == 2
    assert 'bronchodilator' in asthma['sources'][0]['excerpt']
    response = client.post('/api/exams', json={'count': 10, 'topics': ['Diabetes mellitus']})
    assert response.status_code == 200
    exam = response.json()
    assert exam['actual'] == 2 and exam['requested'] == 10
    assert len({q['id'] for q in exam['questions']}) == 2
    assert all('answer' not in q and 'explanation' not in q for q in exam['questions'])
    with module.connect() as db:
        key = json.loads(db.execute('SELECT questions FROM exams WHERE id=?', (exam['id'],)).fetchone()[0])
    answers = {q['id']: q['answer'] for q in key}
    graded = client.post(f"/api/exams/{exam['id']}/submit", json={'answers': answers}).json()
    assert graded['percent'] == 100
    assert all(q['explanation'] and q['correct'] for q in graded['results'])
    assert client.delete('/api/papers/' + a.json()['id']).status_code == 200
    after = client.get('/api/analysis').json()
    assert len(after['papers']) == 1 and after['recurring_topics'] == 0
    assert not any(t['topic'] == 'Asthma' for t in after['topics'])

def test_duplicates_invalid_and_scanned_pdfs(client):
    document = pdf(['1. Explain asthma diagnosis and treatment in detail.'])
    assert upload(client, 'paper.pdf', document).status_code == 201
    assert upload(client, 'renamed.pdf', document).status_code == 409
    assert upload(client, 'bad.txt', document).status_code == 400
    assert upload(client, 'bad.pdf', b'not pdf').status_code == 400
    assert upload(client, 'broken.pdf', b'%PDF-1.7\ngarbage').status_code == 400
    assert upload(client, 'scanned.pdf', pdf([])).status_code == 422
    assert upload(client, 'huge.pdf', b'%PDF-' + b'0' * (20 * 1024 * 1024)).status_code == 413
    writer = PdfWriter()
    writer.add_blank_page(width=100,height=100)
    writer.encrypt('password')
    output = io.BytesIO(); writer.write(output)
    assert upload(client, 'locked.pdf', output.getvalue()).status_code == 400

def test_no_topics_demo_and_validation(client):
    assert client.post('/api/exams', json={'count': 5}).status_code == 422
    assert upload(client, 'unrelated.pdf', pdf(['This is a history examination about medieval European civilization.'])).status_code == 201
    assert client.get('/api/analysis').json()['topics'] == []
    assert client.post('/api/exams', json={'count': 5}).status_code == 422
    assert client.post('/api/exams', json={'topics':['invented'], 'demo':True}).status_code == 400
    assert client.post('/api/exams', json={'count':31, 'demo':True}).status_code == 422
    exam = client.post('/api/exams', json={'count':30, 'demo':True}).json()
    assert exam['actual'] == 30
    assert len({q['id'] for q in exam['questions']}) == 30
    graded = client.post(f"/api/exams/{exam['id']}/submit", json={'answers':{}}).json()
    assert graded['score'] == 0 and graded['total'] == 30
    assert all(q['selected'] is None for q in graded['results'])
    assert client.post(f"/api/exams/{exam['id']}/submit", json={'answers':{'not-a-question':0}}).status_code == 400
    assert client.post(f"/api/exams/{exam['id']}/submit", json={'answers':{exam['questions'][0]['id']:4}}).status_code == 400
    assert client.post('/api/exams/missing/submit', json={}).status_code == 404
    assert client.delete('/api/papers/missing').status_code == 404

def test_static_and_health(client):
    assert client.get('/').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    assert client.get('/api/health').json() == {'status':'ok', 'question_bank_size':30}

def test_recurring_question_passages_count_distinct_papers(client):
    repeated = 'Explain the clinical diagnosis of diabetic ketoacidosis and insulin treatment.'
    assert upload(client, 'a.pdf', pdf(['1. ' + repeated, '2. ' + repeated])).status_code == 201
    assert upload(client, 'b.pdf', pdf(['1. ' + repeated, '2. Discuss asthma and bronchodilator treatment.'])).status_code == 201
    groups = client.get('/api/analysis').json()['high_yield_questions']
    assert groups[0]['paper_count'] == 2
    assert groups[0]['occurrences'] == 3
    assert groups[0]['topics'] == ['Diabetes mellitus']
    assert len(groups[0]['sources']) == 3
