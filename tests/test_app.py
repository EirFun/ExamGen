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
        db.execute('CREATE TABLE papers (id TEXT PRIMARY KEY, digest TEXT UNIQUE, name TEXT, pages INTEGER, extracted TEXT, created TEXT, ocr_pages INTEGER NOT NULL DEFAULT 0)')
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

def scan_pdf(lines, native_first=False):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    image = Image.new('RGB', (1600, 1800), 'white')
    font_path = str(__import__('pathlib').Path(__file__).with_name('fonts') / 'DejaVuSans.ttf')
    if not __import__('pathlib').Path(font_path).exists():
        pytest.skip('OCR fixture requires DejaVuSans for English and Arabic rendering')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 38)
    for i, line in enumerate(lines):
        if any('\u0600' <= c <= '\u06ff' for c in line):
            draw.text((1500, 150 + i * 80), line, font=font, fill='black', anchor='ra', direction='rtl', language='ar')
        else:
            draw.text((70, 150 + i * 80), line, font=font, fill='black')
    output=io.BytesIO(); document=canvas.Canvas(output, pagesize=(600,700))
    if native_first:
        document.drawString(40,600,'1. Explain myocardial infarction and troponin in detail.')
        document.showPage()
    document.drawImage(ImageReader(image),0,0,width=600,height=700)
    document.showPage();document.save();image.close()
    return output.getvalue()


def test_actual_scanned_and_mixed_pdf_ocr(client):
    document=scan_pdf(['1. Explain diabetic ketoacidosis and insulin treatment.', '2. Describe asthma and bronchodilator treatment.'], native_first=True)
    response=upload(client,'mixed-scan.pdf',document)
    assert response.status_code==201, response.text
    assert response.json()['ocr_pages']==1
    analysis=client.get('/api/analysis').json()
    assert analysis['papers'][0]['ocr_pages']==1
    assert {'Diabetes mellitus','Asthma','Acute coronary syndrome'} <= {t['topic'] for t in analysis['topics']}
    diabetes=next(t for t in analysis['topics'] if t['topic']=='Diabetes mellitus')
    assert diabetes['sources'][0]['page']==2
    assert 'insulin' in diabetes['sources'][0]['excerpt'].lower()
    exam=client.post('/api/exams',json={'topics':['Diabetes mellitus'],'count':5}).json()
    assert exam['actual']==2


def test_actual_arabic_ocr_and_topic_matching(client):
    document=scan_pdf(['السؤال الأول: اشرح مرض السكري وعلاج الأنسولين', 'السؤال الثاني: تشخيص الربو وعلاجه'])
    response=upload(client,'arabic-scan.pdf',document)
    assert response.status_code==201, response.text
    assert response.json()['ocr_pages']==1
    topics={t['topic'] for t in client.get('/api/analysis').json()['topics']}
    assert 'Diabetes mellitus' in topics
    assert 'Asthma' in topics


def test_ocr_missing_assets_and_timeout(client,monkeypatch,tmp_path):
    import ocr
    document=scan_pdf(['Explain diabetic ketoacidosis and insulin treatment.'])
    monkeypatch.setattr(ocr,'ASSETS',tmp_path)
    response=upload(client,'scan.pdf',document)
    assert response.status_code==422 and 'language files are missing' in response.json()['detail']
    assert not client.get('/api/analysis').json()['papers']
    monkeypatch.setattr(ocr,'ASSETS',ocr.ROOT/'ocr_assets')
    def timeout(*args,**kwargs):
        raise __import__('subprocess').TimeoutExpired('tesseract',45)
    monkeypatch.setattr(ocr,'run_engine',timeout)
    response=upload(client,'scan.pdf',document)
    assert response.status_code==422 and 'timed out on page 1' in response.json()['detail']
    assert not client.get('/api/analysis').json()['papers']

def test_ocr_unicode_asset_directory(client,monkeypatch,tmp_path):
    import ocr,shutil
    assets=tmp_path/'مجلد عربي'/'ocr_assets'
    shutil.copytree(ocr.ASSETS,assets)
    monkeypatch.setattr(ocr,'ASSETS',assets)
    document=scan_pdf(['Explain diabetic ketoacidosis and insulin treatment.'])
    response=upload(client,'scan.pdf',document)
    assert response.status_code==201,response.text
    assert response.json()['ocr_pages']==1
    assert client.get('/api/analysis').json()['topics'][0]['topic']=='Diabetes mellitus'


def test_ocr_engine_language_error_is_not_reported_as_bad_scan(client,monkeypatch):
    import ocr,subprocess
    def failed_engine(*args,**kwargs):
        return subprocess.CompletedProcess(args[0],1,b'',b"Failed loading language 'eng'\nCould not initialize tesseract.")
    monkeypatch.setattr(ocr,'run_engine',failed_engine)
    response=upload(client,'scan.pdf',scan_pdf(['Explain diabetic ketoacidosis and insulin treatment.']))
    assert response.status_code==422
    assert 'language files' in response.json()['detail']
    assert 'clearer scan' not in response.json()['detail']
    assert not client.get('/api/analysis').json()['papers']
