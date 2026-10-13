"""Exercise the packaged executable on native Windows using a synthetic scan."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def main():
    exe=Path(sys.argv[1]).resolve()
    font=Path(__file__).resolve().parents[1]/'tests/fonts/DejaVuSans.ttf'
    result={'description':'Synthetic diagram for packaging smoke.','identified_items':['Heart'],
            'topics':['Cardiovascular physiology'],'reasoning':'Packaging integration fixture.',
            'uncertainties':[],'questions':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.respond({'models':[{'name':'qwen2.5vl:3b'}]})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path=='/api/show':self.respond({'capabilities':['vision']})
            else:
                assert body['messages'][1]['images'][0]
                self.respond({'message':{'content':json.dumps(result)}})
        def respond(self,value):
            payload=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    ollama=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=ollama.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='examgen-compat-') as temporary:
        directory=Path(temporary)/'مجلد عربي'
        directory.mkdir()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        env=os.environ.copy();env['TEMP']=str(directory);env['TMP']=str(directory);env['EXAMGEN_OLLAMA_URL']=f'http://127.0.0.1:{ollama.server_port}'
        log=(directory/'console.log').open('wb')
        process=subprocess.Popen([str(exe),'--no-browser','--port',str(port),'--data-dir',str(directory/'data')],env=env,stdout=log,stderr=log)
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path,body=None,headers={},method=None):
            return client.open(urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=body,headers=headers,method=method),timeout=90).read()
        try:
            for _ in range(150):
                if process.poll() is not None:raise RuntimeError('Packaged executable exited: '+(directory/'console.log').read_text(errors='replace'))
                try:
                    assert json.loads(request('/api/health'))['status']=='ok';break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('Packaged app did not start')
            image=Image.new('RGB',(1600,1800),'white')
            draw=ImageDraw.Draw(image);draw.text((70,150),'Explain diabetic ketoacidosis and insulin treatment.',font=ImageFont.truetype(str(font),38),fill='black')
            output=io.BytesIO();doc=canvas.Canvas(output,pagesize=(600,700));doc.drawImage(ImageReader(image),0,0,width=600,height=700);doc.showPage();doc.save();image.close()
            boundary='NativeWindowsSmoke'
            body=f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="scan.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+output.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
            added=json.loads(request('/api/papers',body,{'Content-Type':f'multipart/form-data; boundary={boundary}'},'POST'))
            assert added['ocr_pages']==1,added
            analysis=json.loads(request('/api/analysis'));assert analysis['topics'][0]['topic']=='Diabetes mellitus'
            assert json.loads(request('/api/vision/status'))['ready']
            paper=added['id'];source=request(f'/api/papers/{paper}/pages/1/image');assert source.startswith(b'\xff\xd8')
            job=json.loads(request(f'/api/papers/{paper}/vision',json.dumps({'pages':[1]}).encode(),{'Content-Type':'application/json'},'POST'))
            for _ in range(100):
                status=json.loads(request('/api/vision/jobs/'+job['id']))
                if status['state'] in ('completed','failed'):break
                time.sleep(.1)
            assert status['state']=='completed',status
            saved=json.loads(request(f'/api/papers/{paper}/visuals'))['results'];assert saved[0]['description']==result['description']
            book_pdf=io.BytesIO();book_doc=canvas.Canvas(book_pdf);book_doc.drawString(30,750,'The left ventricle ejects blood into the aorta and supplies systemic circulation.');book_doc.save()
            book_body=f'--{boundary}\r\nContent-Disposition: form-data; name="kind"\r\n\r\nbook\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="book.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+book_pdf.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
            book_added=json.loads(request('/api/papers',book_body,{'Content-Type':f'multipart/form-data; boundary={boundary}'},'POST'))
            assert book_added['kind']=='book'
            assert json.loads(request('/api/books/search?query=ventricle'))['sources'][0]['book_id']==book_added['id']
            assert len(json.loads(request('/api/analysis'))['books'])==1
            syllabus_image=Image.new('RGB',(1200,350),'white');syllabus_draw=ImageDraw.Draw(syllabus_image);syllabus_font=ImageFont.truetype(str(font),32)
            syllabus_draw.text((35,40),'Syllabus: cardiovascular physiology.',font=syllabus_font,fill='black');syllabus_draw.text((35,110),'Explain left ventricle ejection into the aorta.',font=syllabus_font,fill='black')
            syllabus_png=io.BytesIO();syllabus_image.save(syllabus_png,format='PNG');syllabus_image.close()
            syllabus_body=f'--{boundary}\r\nContent-Disposition: form-data; name="kind"\r\n\r\nsyllabus\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="syllabus.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+syllabus_png.getvalue()+f'\r\n--{boundary}--\r\n'.encode()
            syllabus_added=json.loads(request('/api/papers',syllabus_body,{'Content-Type':f'multipart/form-data; boundary={boundary}'},'POST'))
            assert syllabus_added['ocr_pages']==1 and len(json.loads(request('/api/analysis'))['syllabi'])==1
            print('Packaged Windows app passed native OCR and local vision integration under Arabic TEMP/data paths. Vision used a loopback stub.')
        except Exception:
            log.flush();print((directory/'console.log').read_text(errors='replace'),file=sys.stderr);raise
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else:
                process.terminate()
            process.wait(timeout=10);log.close();ollama.shutdown();ollama.server_close()

if __name__=='__main__':main()
