import copy
import io
import json
import threading

from PIL import Image
import pytest
import app
import vision
from tests.test_app import client,pdf,upload
from tests.test_vision import ollama,finish,paper,RESULT

BOOK='The left ventricle ejects blood into the aorta and supplies the systemic circulation. Myocardial infarction damages heart muscle.'
SCOPE='Cardiovascular physiology: explain how the left ventricle supplies the systemic circulation through the aorta.'

def add(client,kind,text,name=None):
    response=client.post('/api/papers',data={'kind':kind},files={'file':(name or kind+'.pdf',pdf([text]),'application/pdf')})
    assert response.status_code==201,response.text
    return response.json()['id']

def grounded_reply(client,settings,subjective=False,syllabus=False,bad=False):
    source=client.get('/api/books/search',params={'query':'left ventricle aorta'}).json()['sources'][0]
    result=copy.deepcopy(RESULT)
    q=result['questions'][0]
    q['citations']=[{'source_id':source['id'],'quote':'Fabricated book quotation with no source support.' if bad else BOOK[:82]}]
    if subjective:
        q.pop('options');q.pop('answer');q.update(type='subjective',answer_text='The left ventricle supplies systemic circulation through the aorta.',marking_points=['Identify the left ventricle.','Explain ejection into the aorta.'])
    if syllabus:q['syllabus_quote']=SCOPE
    settings['reply']=result
    return result

def test_library_roles_reclassification_and_book_index(client):
    exam=add(client,'exam','Explain myocardial infarction and elevated troponin.')
    book=add(client,'book',BOOK)
    syllabus=add(client,'syllabus',SCOPE)
    analysis=client.get('/api/analysis').json()
    assert [p['id'] for p in analysis['papers']]==[exam]
    assert [p['id'] for p in analysis['books']]==[book] and [p['id'] for p in analysis['syllabi']]==[syllabus]
    assert all(t['paper_count']==1 for t in analysis['topics'])
    sources=client.get('/api/books/search',params={'query':'ventricle aorta'}).json()['sources']
    assert {s['book_id'] for s in sources}=={book}
    assert client.patch('/api/papers/'+book+'/kind',json={'kind':'exam'}).status_code==200
    assert not client.get('/api/books/search',params={'query':'ventricle aorta'}).json()['sources']
    assert client.patch('/api/papers/'+book+'/kind',json={'kind':'book'}).status_code==200
    assert client.get('/api/papers/'+book+'/source.pdf').content.startswith(b'%PDF')
    client.delete('/api/papers/'+book)
    assert not client.get('/api/books/search',params={'query':'ventricle aorta'}).json()['sources']

def test_syllabus_photo_and_optional_scope(client,monkeypatch):
    monkeypatch.setattr(app,'recognize_pdf_pages',lambda payload,texts:([SCOPE],1))
    image=Image.new('RGB',(250,250),'white');output=io.BytesIO();image.save(output,format='PNG');image.close()
    response=client.post('/api/papers',data={'kind':'syllabus'},files={'file':('syllabus.png',output.getvalue(),'image/png')})
    assert response.status_code==201,response.text
    assert client.get('/api/analysis').json()['syllabi'][0]['id']==response.json()['id']
    assert client.get('/api/papers/'+response.json()['id']+'/source.pdf').content.startswith(b'%PDF')
    assert client.post('/api/papers',data={'kind':'book'},files={'file':('x.png',output.getvalue(),'image/png')}).status_code==400

def test_grounded_exam_citations_style_and_grading(client,ollama):
    calls,settings=ollama
    exam_source=add(client,'exam','Explain how the left ventricle supplies the systemic circulation through the aorta.')
    book=add(client,'book',BOOK)
    grounded_reply(client,settings)
    response=client.post('/api/exams/grounded',json={'count':1})
    job=finish(client,response.json());assert job['state']=='completed',job
    q=job['draft']['questions'][0]
    assert q['book_sources'][0]['book_id']==book and q['book_sources'][0]['page']==1
    assert q['exam_sources'][0]['exam']=='exam.pdf'
    prompt=next(body for path,body in calls if path=='/api/chat')['messages'][1]
    assert 'BOOK ANSWER EVIDENCE' in prompt['content'] and 'style' in prompt['content']
    assert 'images' not in prompt
    accepted=client.post('/api/exams/grounded/'+job['id']+'/accept').json()
    assert 'answer' not in accepted['questions'][0] and 'book_sources' not in accepted['questions'][0]
    ready=accepted['questions'][0]
    scored=client.post('/api/exams/'+accepted['id']+'/submit',json={'answers':{ready['id']:ready['options'].index('Left ventricle')}}).json()
    assert scored['percent']==100 and scored['results'][0]['book_sources'][0]['quote']
    assert client.post('/api/exams/grounded/'+job['id']+'/accept').json()['id']==accepted['id']

def test_unverified_citations_and_syllabus_fail_closed(client,ollama):
    _,settings=ollama
    add(client,'exam','Explain the left ventricle and the aorta in systemic circulation.')
    add(client,'book',BOOK)
    scope=add(client,'syllabus',SCOPE)
    grounded_reply(client,settings,bad=True)
    job=finish(client,client.post('/api/exams/grounded',json={'count':1}).json())
    assert job['state']=='failed' and 'citation' in job['message']
    grounded_reply(client,settings,syllabus=True)
    job=finish(client,client.post('/api/exams/grounded',json={'count':1,'syllabus_id':scope}).json())
    assert job['state']=='completed' and job['draft']['questions'][0]['syllabus_source']['quote']==SCOPE
    settings['reply']['questions'][0]['syllabus_quote']='An invented unrelated curriculum objective.'
    job=finish(client,client.post('/api/exams/grounded',json={'count':1,'syllabus_id':scope}).json())
    assert job['state']=='failed' and 'syllabus' in job['message']
    unrelated=add(client,'syllabus','Dermatology: classify eczema skin rashes and topical treatment.',name='other.pdf')
    job=finish(client,client.post('/api/exams/grounded',json={'count':1,'syllabus_id':unrelated}).json())
    assert job['state']=='failed'

def test_subjective_diagram_exam_and_self_assessment(client,ollama):
    _,settings=ollama;source=paper(client);add(client,'book',BOOK)
    grounded_reply(client,settings,subjective=True)
    response=client.post(f'/api/papers/{source}/vision',json={'pages':[1],'mode':'similar','question_type':'subjective','use_books':True,'original_question':'Explain the left ventricle in the aorta diagram.','verified_label':'Left ventricle','region':[.1,.1,.6,.6]})
    job=finish(client,response.json());assert job['state']=='completed',job
    detail=client.get(f'/api/papers/{source}/visuals').json()['results'][0]
    assert detail['verified_label']=='Left ventricle' and detail['questions'][0]['book_sources']
    client.patch(f'/api/papers/{source}/visuals/1',json={'reviewed':True})
    exam=client.post('/api/exams',json={'question_type':'subjective','include_visual':True,'count':1}).json()
    q=exam['questions'][0];assert q['type']=='subjective' and q['image']
    assert 'answer_text' not in q and 'marking_points' not in q
    answer=client.post('/api/exams/'+exam['id']+'/submit',json={'subjective_answers':{q['id']:'The left ventricle ejects into the aorta.'}}).json()
    assert answer['percent'] is None and answer['self_assessment_pending']
    assert answer['results'][0]['answer_text'] and answer['results'][0]['marking_points']
    marked=client.post('/api/exams/'+exam['id']+'/submit',json={'self_marks':{q['id']:True}}).json()
    assert marked['percent']==100 and not marked['self_assessment_pending']
    generated=finish(client,client.post('/api/exams/grounded',json={'count':1,'question_type':'subjective'}).json())
    assert generated['state']=='completed',generated
    ready=client.post('/api/exams/grounded/'+generated['id']+'/accept').json()['questions'][0]
    assert ready['type']=='subjective' and ready['image'] and 'answer_text' not in ready

def test_streamed_progress_gpu_and_model_choice(client,ollama,monkeypatch):
    calls,settings=ollama;source=paper(client)
    settings['running']=[{'name':vision.DEFAULT_MODEL,'size':3000000000,'size_vram':3000000000}]
    status=client.get('/api/vision/status').json();assert status['processor']=='GPU' and status['gpu_percent']==100
    settings.update(streaming=True,entered=threading.Event(),proceed=threading.Event())
    job=client.post(f'/api/papers/{source}/vision',json={'pages':[1]}).json()
    assert settings['entered'].wait(2)
    live=client.get('/api/vision/jobs/'+job['id']).json()
    assert live['state']=='running' and live['response_chars']>0 and live['elapsed_seconds']>=0
    assert client.patch('/api/vision/model',json={'model':'qwen2.5vl:7b'}).status_code==409
    settings['proceed'].set();assert finish(client,job)['state']=='completed'
    detail=client.get(f'/api/papers/{source}/visuals').json()['results'][0]
    assert detail['performance']['tokens_per_second']==10
    monkeypatch.setenv('EXAMGEN_VISION_MODEL',vision.DEFAULT_MODEL)
    assert client.patch('/api/vision/model',json={'model':'qwen2.5vl:7b'}).status_code==200
    assert vision.model_name()=='qwen2.5vl:7b'

def test_model_option_prefixes_removed_before_shuffling(client,ollama):
    _,settings=ollama;source=paper(client);reply=copy.deepcopy(RESULT)
    reply['questions'][0]['options']=[f'{letter}. {option}' for letter,option in zip('ABCD',reply['questions'][0]['options'])]
    reply['questions'][0]['correct_answer']='The left ventricle.'
    settings['reply']=reply
    assert finish(client,client.post(f'/api/papers/{source}/vision',json={'pages':[1]}).json())['state']=='completed'
    q=client.get(f'/api/papers/{source}/visuals').json()['results'][0]['questions'][0]
    assert q['options']==RESULT['questions'][0]['options'] and q['answer']==0

def test_confirmed_label_drives_book_retrieval(client,ollama):
    _,settings=ollama;source=paper(client);add(client,'book',BOOK);grounded_reply(client,settings)
    response=client.post(f'/api/papers/{source}/vision',json={'pages':[1],'mode':'similar','region':[.1,.1,.5,.5],'original_question':'Identify the pointed structure.','verified_label':'Left ventricle','use_books':True})
    job=finish(client,response.json());assert job['state']=='completed',job
    assert client.get(f'/api/papers/{source}/visuals').json()['results'][0]['questions'][0]['book_sources']

def test_source_ids_and_correct_option_replace_model_index(client,ollama):
    _,settings=ollama;source=paper(client);book=add(client,'book',BOOK);scope=add(client,'syllabus',SCOPE)
    result=grounded_reply(client,settings)
    book_source=client.get('/api/books/search',params={'query':'left ventricle'}).json()['sources'][0]
    with app.connect() as db:scope_id=str(db.execute('SELECT rowid FROM book_chunks WHERE book_id=?',(scope,)).fetchone()[0])
    q=result['questions'][0];q['correct_answer']='Left ventricle';q['answer']=1;q['citations']=[{'source_id':book_source['id']}];q['syllabus_source_id']=scope_id
    response=client.post(f'/api/papers/{source}/vision',json={'pages':[1],'mode':'similar','region':[.1,.1,.5,.5],'original_question':'Explain left ventricle ejection into the aorta.','use_books':True,'syllabus_id':scope})
    job=finish(client,response.json());assert job['state']=='completed',job
    question=client.get(f'/api/papers/{source}/visuals').json()['results'][0]['questions'][0]
    assert question['answer']==0 and question['book_sources'][0]['quote']==BOOK
    assert question['syllabus_source']['quote']==SCOPE
    client.patch(f'/api/papers/{source}/visuals/1',json={'reviewed':True})
    exam=client.post('/api/exams',json={'include_visual':True,'count':1}).json()
    assert 'correct_answer' not in exam['questions'][0] and 'syllabus_source' not in exam['questions'][0]
    q['correct_answer']='A nonexistent answer'
    job=finish(client,client.post(f'/api/papers/{source}/vision',json={'pages':[1]}).json())
    assert job['state']=='failed' and 'does not match' in job['message']


def test_missing_correct_answer_is_rejected(client,ollama):
    _,settings=ollama;source=paper(client);result=copy.deepcopy(RESULT)
    result['questions'][0].pop('correct_answer');settings['reply']=result
    job=finish(client,client.post(f'/api/papers/{source}/vision',json={'pages':[1]}).json())
    assert job['state']=='failed' and 'omitted the correct option' in job['message']

def test_copied_original_question_is_rewritten_once_then_rejected(client,ollama):
    calls,settings=ollama;source=paper(client);result=copy.deepcopy(RESULT)
    original='Identify the chamber in the diagram.';result['questions'][0]['stem']=original;settings['reply']=result
    response=client.post(f'/api/papers/{source}/vision',json={'pages':[1],'mode':'similar','region':[.1,.1,.5,.5],'original_question':original})
    job=finish(client,response.json());assert job['state']=='failed'
    assert len([body for path,body in calls if path=='/api/chat'])==2
    assert not client.get(f'/api/papers/{source}/visuals').json()['results']

def test_grounded_theory_uses_reviewed_visual_topics_for_focus(client,ollama):
    _,settings=ollama;source=paper(client);add(client,'book',BOOK);grounded_reply(client,settings)
    job=finish(client,client.post(f'/api/papers/{source}/vision',json={'pages':[1],'verified_label':'Left ventricle'}).json())
    assert job['state']=='completed'
    client.patch(f'/api/papers/{source}/visuals/1',json={'reviewed':True})
    response=client.post('/api/exams/grounded',json={'count':1,'topics':['Cardiovascular physiology']})
    assert response.status_code==200,response.text
    assert finish(client,response.json())['state']=='completed'
