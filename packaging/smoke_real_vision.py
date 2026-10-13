"""Real, CPU-based local VLM smoke on a synthetic diagram, not a medical accuracy benchmark."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import vision
from PIL import Image,ImageDraw,ImageFont

VERSION='v0.40.3'
SHA256='e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5'

def main():
    root=Path('build/ollama-smoke').resolve();root.mkdir(parents=True,exist_ok=True)
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
    env=os.environ.copy();env['OLLAMA_HOST']='127.0.0.1:11434';env['OLLAMA_NO_CLOUD']='1';env['CUDA_VISIBLE_DEVICES']='-1';env['OLLAMA_MODELS']=str(root/'models');env['OLLAMA_CONTEXT_LENGTH']='4096'
    log=(root/'server.log').open('wb')
    server=subprocess.Popen([str(exe),'serve'],env=env,cwd=exe.parent,stdout=log,stderr=log)
    os.environ['EXAMGEN_OLLAMA_URL']='http://127.0.0.1:11434'
    try:
        for _ in range(120):
            if vision.status()['connected']:break
            if server.poll() is not None:raise RuntimeError('Ollama stopped: '+(root/'server.log').read_text(errors='replace'))
            time.sleep(.5)
        else:raise RuntimeError('Ollama did not start')
        print('Downloading real local vision model...',flush=True)
        vision.pull_model(lambda status,percent:None,lambda:False)
        assert vision.status()['ready']
        image=Image.new('RGB',(900,360),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('tests/fonts/DejaVuSans.ttf',30)
        draw.rectangle((25,95,365,235),outline='black',width=4);draw.text((50,150),'LEFT VENTRICLE',font=font,fill='black')
        draw.rectangle((590,95,875,235),outline='black',width=4);draw.text((665,150),'AORTA',font=font,fill='black')
        draw.line((365,165,590,165),fill='black',width=5);draw.polygon([(590,165),(565,150),(565,180)],fill='black')
        encoded=io.BytesIO();image.save(encoded,format='JPEG',quality=95);image.close()
        print('Running real CPU vision inference...',flush=True)
        started=time.monotonic()
        result=vision.interpret(encoded.getvalue(),'','Read both printed labels and state the arrow direction. Do not generate practice questions for this smoke test.')
        text=' '.join([result['description'],*result['identified_items'],result['reasoning']]).lower()
        assert 'ventricle' in text and 'aorta' in text,result
        report={'model':vision.model_name(),'ollama':VERSION,'hardware':'CPU, GPU not tested','seconds':round(time.monotonic()-started,1),'interpretation':result}
        Path('build/local-vision-smoke.json').write_text(json.dumps(report,indent=2))
        print('Real local vision model identified the synthetic diagram labels and returned valid structured output.',flush=True)
    except Exception as error:
        log.flush();detail=str(error)[:2000].replace('%','%25').replace('\r','%0D').replace('\n','%0A');print('::error title=Real local vision smoke::'+detail,flush=True);raise
    finally:
        subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],capture_output=True);server.wait(timeout=10);log.close()

if __name__=='__main__':main()
