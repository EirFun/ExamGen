"""Integration tests use a loopback Ollama stub; they do not measure model accuracy."""
import base64
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
import vision
from tests.test_app import client, pdf, upload

RESULT={'description':'A labelled cardiovascular diagram.','identified_items':['Left ventricle'],'topics':['Cardiovascular physiology'],'reasoning':'The indicated chamber ejects blood to the systemic circulation.','uncertainties':['Verify the labels against the source diagram.'],'questions':[{'stem':'Which chamber supplies the systemic circulation?','options':['Left ventricle','Right ventricle','Right atrium','Left atrium'],'answer':0,'explanation':'The left ventricle ejects blood through the aorta into the systemic circulation.'}]}

@pytest.fixture
def ollama(monkeypatch):
    calls=[];settings={'installed':True,'remote':False,'bad_json':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def answer(self,value):
            payload=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self):
            calls.append((self.path,None))
            self.answer({'models':[{'name':vision.DEFAULT_MODEL}]} if self.path=='/api/tags' and settings['installed'] else {'models':[]})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append((self.path,body))
            if self.path=='/api/show':self.answer({'capabilities':['vision','completion'],'remote_host':'https://ollama.com' if settings['remote'] else None})
            elif self.path=='/api/pull':settings['installed']=True;self.answer({'status':'success'})
            elif self.path=='/api/chat':self.answer({'message':{'content':'broken' if settings['bad_json'] else json.dumps(RESULT)}})
            else:self.answer({})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('EXAMGEN_OLLAMA_URL',f'http://127.0.0.1:{server.server_port}')
    monkeypatch.delenv('EXAMGEN_VISION_MODEL',raising=False)
    try:yield calls,settings
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def finish(client,job):
    for _ in range(100):
        result=client.get('/api/vision/jobs/'+job['id']).json()
        if result['state'] in ('completed','failed','cancelled'):return result
        time.sleep(.02)
    raise AssertionError('Vision job did not finish')


def paper(client):
    response=upload(client,'diagram.pdf',pdf(['Study question: identify the labelled diagram and explain its function.']))
    assert response.status_code==201,response.text
    return response.json()['id']


def test_local_image_analysis_review_and_visual_exam(client,ollama):
    calls,_=ollama;paper_id=paper(client)
    assert client.get('/api/analysis').json()['papers'][0]['has_images']
    source=client.get(f'/api/papers/{paper_id}/pages/1/image')
    assert source.status_code==200 and source.headers['content-type']=='image/jpeg'
    assert client.get('/api/vision/status').json()['ready']
    response=client.post(f'/api/papers/{paper_id}/vision',json={'pages':[1],'region':[.1,.1,.6,.6],'question':'Explain the diagram.'})
    assert response.status_code==200,response.text
    assert finish(client,response.json())['state']=='completed'
    chat=next(body for path,body in calls if path=='/api/chat')
    assert chat['model']==vision.DEFAULT_MODEL and chat['stream'] is False
    assert 'untrusted' in chat['messages'][0]['content']
    image=base64.b64decode(chat['messages'][1]['images'][0]);assert image.startswith(b'\xff\xd8')
    with Image.open(io.BytesIO(image)) as crop,Image.open(io.BytesIO(source.content)) as full:assert crop.width<full.width
    details=client.get(f'/api/papers/{paper_id}/visuals').json()['results']
    assert len(details)==1 and not details[0]['reviewed']
    assert details[0]['region']==[.1,.1,.6,.6]
    assert client.post('/api/exams',json={'include_visual':True}).status_code==422
    assert client.patch(f'/api/papers/{paper_id}/visuals/1',json={'reviewed':True}).status_code==200
    analysis=client.get('/api/analysis').json();assert analysis['topics'][0]['topic']=='Cardiovascular physiology'
    assert analysis['topics'][0]['sources'][0]['origin']=='local vision, reviewed'
    exam=client.post('/api/exams',json={'count':5,'topics':['Cardiovascular physiology'],'include_visual':True}).json()
    assert exam['actual']==1
    q=exam['questions'][0];assert 'answer' not in q and 'explanation' not in q
    assert client.get(q['image']).status_code==200
    graded=client.post('/api/exams/'+exam['id']+'/submit',json={'answers':{q['id']:q['options'].index('Left ventricle')}}).json()
    assert graded['percent']==100
    assert client.delete('/api/papers/'+paper_id).status_code==200
    assert not client.get('/api/analysis').json()['topics']


def test_legacy_paper_image_restore(client):
    import app
    payload=pdf(['A previously uploaded diagram paper with readable question text.'])
    added=upload(client,'old.pdf',payload).json()
    with app.connect() as db:db.execute('UPDATE papers SET pdf_bytes=NULL WHERE id=?',(added['id'],))
    assert client.get(f"/api/papers/{added['id']}/pages/1/image").status_code==409
    restored=upload(client,'same-file.pdf',payload)
    assert restored.status_code==201 and restored.json()['source_restored']
    assert restored.json()['id']==added['id']
    assert len(client.get('/api/analysis').json()['papers'])==1
    assert client.get(f"/api/papers/{added['id']}/pages/1/image").status_code==200
    assert upload(client,'same-file.pdf',payload).status_code==409


def test_local_only_guards_and_cloud_alias_rejection(client,ollama,monkeypatch):
    calls,settings=ollama
    settings['remote']=True
    assert not client.get('/api/vision/status').json()['ready']
    paper_id=paper(client)
    assert client.post(f'/api/papers/{paper_id}/vision',json={'pages':[1]}).status_code==409
    with pytest.raises(vision.VisionError):vision.interpret(b'private-image','private text')
    assert not any(path=='/api/chat' for path,_ in calls)
    monkeypatch.setenv('EXAMGEN_OLLAMA_URL','https://remote.example.com')
    assert client.get('/api/vision/status').status_code==400
    monkeypatch.setenv('EXAMGEN_OLLAMA_URL','http://127.0.0.1:11434/path')
    with pytest.raises(vision.VisionError):vision.local_url()
    monkeypatch.setenv('EXAMGEN_VISION_MODEL','some-model:cloud')
    with pytest.raises(vision.VisionError):vision.model_name()


def test_bad_pages_crops_and_bad_model_response(client,ollama):
    _,settings=ollama;paper_id=paper(client)
    for body in [{'pages':[2]},{'pages':[1],'region':[0,0,2,1]},{'pages':[1],'region':[.2,.2,0,0]},{'pages':[1],'region':[0,0,1]}]:
        assert client.post(f'/api/papers/{paper_id}/vision',json=body).status_code==400
    assert client.get(f'/api/papers/{paper_id}/pages/0/image').status_code==400
    assert client.get(f'/api/papers/{paper_id}/pages/1/image',params={'region':'[0,0,2,1]'}).status_code==400
    settings['bad_json']=True
    started=client.post(f'/api/papers/{paper_id}/vision',json={'pages':[1]}).json()
    completed=finish(client,started)
    assert completed['state']=='failed' and completed['errors']
    assert not client.get(f'/api/papers/{paper_id}/visuals').json()['results']


def test_model_download_and_connection_error(client,ollama,monkeypatch):
    _,settings=ollama;settings['installed']=False
    assert not client.get('/api/vision/status').json()['ready']
    started=client.post('/api/vision/download').json()
    assert finish(client,started)['state']=='completed'
    assert client.get('/api/vision/status').json()['ready']
    monkeypatch.setenv('EXAMGEN_OLLAMA_URL','http://127.0.0.1:1')
    status=client.get('/api/vision/status').json()
    assert not status['connected'] and 'Ollama' in status['message']


def test_cancel_between_pages_preserves_completed_result(client,ollama,monkeypatch):
    calls,_=ollama;payload=pdf(['This is a diagram and its printed question text.'],['Second diagram with its printed question text.'])
    paper_id=upload(client,'two.pdf',payload).json()['id']
    original=vision.interpret;entered=threading.Event();proceed=threading.Event()
    def delayed(*args,**kwargs):entered.set();assert proceed.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(vision,'interpret',delayed)
    started=client.post(f'/api/papers/{paper_id}/vision',json={'pages':[1,2]}).json()
    assert entered.wait(2)
    assert client.post(f'/api/papers/{paper_id}/vision',json={'pages':[1]}).status_code==409
    client.post('/api/vision/jobs/'+started['id']+'/cancel');proceed.set()
    completed=finish(client,started)
    assert completed['state']=='cancelled' and completed['completed']==1
    assert len(client.get(f'/api/papers/{paper_id}/visuals').json()['results'])==1
    assert len([path for path,_ in calls if path=='/api/chat'])==1


def test_diagram_without_readable_text_is_kept(client,monkeypatch):
    import app
    monkeypatch.setattr(app,'recognize_pdf_pages',lambda payload,texts: ([''],1))
    output=io.BytesIO();doc=__import__('reportlab.pdfgen.canvas',fromlist=['Canvas']).Canvas(output)
    doc.circle(150,450,60,fill=0);doc.line(210,450,350,450);doc.showPage();doc.save()
    response=upload(client,'diagram-only.pdf',output.getvalue())
    assert response.status_code==201,response.text
    assert client.get(f"/api/papers/{response.json()['id']}/pages/1/image").status_code==200


def test_local_transport_rejects_redirects():
    with pytest.raises(vision.VisionError):
        vision.LocalOnlyRedirect().redirect_request(None,None,307,'Redirect',{},'https://external.example')
