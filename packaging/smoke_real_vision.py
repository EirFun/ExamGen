"""Real, CPU-based local VLM smoke on a synthetic diagram, not a medical accuracy benchmark."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import vision
import books
from PIL import Image,ImageDraw,ImageFont

VERSION='v0.40.3'
STAGE='setup'

SHA256='e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5'

def main():
    global STAGE
    root=Path('build/ollama-smoke').resolve();root.mkdir(parents=True,exist_ok=True)
    STAGE='download verified Ollama archive'
    archive=root/'ollama.zip'
    url=f'https://github.com/ollama/ollama/releases/download/{VERSION}/ollama-windows-amd64.zip'
    digest=hashlib.sha256()
    with urllib.request.urlopen(url,timeout=120) as source,archive.open('wb') as output:
        while chunk:=source.read(1024*1024):output.write(chunk);digest.update(chunk)
    if digest.hexdigest()!=SHA256:raise RuntimeError('Official Ollama archive checksum mismatch')
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if any(part.lower().startswith(('cuda','rocm','vulkan')) for part in Path(member.filename).parts):continue
            package.extract(member,root)
    archive.unlink()
    exe=next(root.rglob('ollama.exe'))
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    env=os.environ.copy();env['OLLAMA_HOST']=f'127.0.0.1:{port}';env['OLLAMA_NO_CLOUD']='1';env['CUDA_VISIBLE_DEVICES']='-1';env['OLLAMA_MODELS']=str(root/'models');env['OLLAMA_CONTEXT_LENGTH']='4096'
    log=(root/'server.log').open('wb')
    server=subprocess.Popen([str(exe),'serve'],env=env,cwd=exe.parent,stdout=log,stderr=log)
    os.environ['EXAMGEN_OLLAMA_URL']=f'http://127.0.0.1:{port}'
    try:
        STAGE='start local model server'
        for _ in range(120):
            if vision.status()['connected']:break
            if server.poll() is not None:raise RuntimeError('Ollama stopped: '+(root/'server.log').read_text(errors='replace'))
            time.sleep(.5)
        else:raise RuntimeError('Ollama did not start')
        print('Downloading real local vision model...',flush=True)
        STAGE='download model'
        vision.pull_model(lambda status,percent:None,lambda:False)
        assert vision.status()['ready'],vision.status()
        image=Image.new('RGB',(900,360),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('tests/fonts/DejaVuSans.ttf',30)
        draw.rectangle((25,95,365,235),outline='black',width=4);draw.text((50,150),'LEFT VENTRICLE',font=font,fill='black')
        draw.rectangle((590,95,875,235),outline='black',width=4);draw.text((665,150),'AORTA',font=font,fill='black')
        draw.line((365,165,590,165),fill='black',width=5);draw.polygon([(590,165),(565,150),(565,180)],fill='black')
        encoded=io.BytesIO();image.save(encoded,format='JPEG',quality=95);image.close()
        print('Running real CPU vision inference...',flush=True)
        started=time.monotonic()
        STAGE='read diagram labels'
        result=vision.interpret(encoded.getvalue(),'','Read both printed labels and state the arrow direction. Do not generate practice questions for this smoke test.')
        text=json.dumps(result,ensure_ascii=False).lower()
        assert 'ventricle' in text and 'aorta' in text,result
        references=[{'id':'1','book_id':'synthetic-book','book':'Synthetic physiology source','page':1,'excerpt':'The left ventricle ejects blood into the aorta, which supplies the systemic circulation. The aorta carries oxygenated blood to the body.'}]
        STAGE='generate a book-grounded diagram variation'
        variations=vision.interpret(encoded.getvalue(),'Identify the chamber in the diagram.',mode='similar',original_question='Identify the chamber in the diagram.',question_count=1,references=references,verified_label='Left ventricle')
        assert variations['questions'],variations
        for item in variations['questions']:
            item['book_sources']=books.validate_citations(item['citations'],references)
            assert item['stem'].strip().lower()!='identify the chamber in the diagram.',{'copied_original_question':item}
        scope=[{'id':'2','book_id':'synthetic-syllabus','book':'Synthetic syllabus','page':1,'excerpt':'Cardiovascular physiology: describe left ventricular ejection into the aorta and systemic circulation.'}]
        fragments=[]
        STAGE='grounded MCQ and syllabus'
        grounded=vision.grounded_questions([{'exam':'Synthetic exam','page':1,'text':'Which chamber ejects blood into the aorta?'}],references,1,progress=lambda message,characters:fragments.append(characters),syllabus=scope)
        assert grounded['questions'],grounded
        for q in grounded['questions']:
            try:q['book_sources']=books.validate_citations(q['citations'],references);books.validate_syllabus(q,scope)
            except ValueError as error:raise RuntimeError(str(error)+' Model question: '+json.dumps(q)) from error
        STAGE='grounded subjective practical'
        practical=vision.interpret(encoded.getvalue(),'Identify the chamber and explain blood flow.',mode='similar',original_question='Identify this chamber and explain its function.',references=references,question_count=1,question_type='subjective',verified_label='Left ventricle')
        assert practical['questions'] and practical['questions'][0]['type']=='subjective',practical
        try:practical['questions'][0]['book_sources']=books.validate_citations(practical['questions'][0]['citations'],references)
        except ValueError as error:raise RuntimeError(str(error)+' Model question: '+json.dumps(practical['questions'][0])) from error
        assert practical['questions'][0]['marking_points'],'No marking points'
        assert grounded['questions'][0]['options'][grounded['questions'][0]['answer']]==grounded['questions'][0]['correct_answer']
        assert len(fragments)>2 and max(fragments)>0,{'progress_fragments':fragments}
        report={'grounded_mcq':grounded,'subjective_practical':practical,'stream_fragments':len(fragments),'variations':variations,'model':vision.model_name(),'ollama':VERSION,'hardware':'CPU, GPU not tested','seconds':round(time.monotonic()-started,1),'interpretation':result}
        Path('build/local-vision-smoke.json').write_text(json.dumps(report,indent=2))
        print('Real local vision model identified the synthetic diagram labels and returned valid structured output.',flush=True)
    except Exception as error:
        log.flush();raise
    finally:
        subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],capture_output=True);server.wait(timeout=10);log.close()

if __name__=='__main__':
    try:main()
    except Exception as error:
        detail=(STAGE+': '+type(error).__name__+': '+str(error))[:2000].replace('%','%25').replace('\r','%0D').replace('\n','%0A');print('::error title=Real local vision smoke::'+detail,flush=True);raise
