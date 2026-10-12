"""Download hash-pinned offline OCR assets before development or Windows packaging."""
from pathlib import Path
import hashlib
import io
import json
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent / 'ocr_assets'
SOURCES = [{'url': 'https://api.nuget.org/v3-flatcontainer/tesseractocr/5.5.2/tesseractocr.5.5.2.nupkg', 'sha256': '2d11e0a45f8f5887d91277dd61e4b2b57423be0e06aefba432484ebc33a8a794'}, {'url': 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata', 'sha256': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'}, {'url': 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/ara.traineddata', 'sha256': 'e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729'}, {'url': 'https://api.nuget.org/v3-flatcontainer/vclibs.vcruntime.140/1.0.4/vclibs.vcruntime.140.1.0.4.nupkg', 'sha256': 'ea8fad02d8de9ca1ab4e1d20c282166b3386adf91a9b143019d59507229ea375'}]

def main():
    ROOT.mkdir(exist_ok=True)
    for source in SOURCES:
        with urllib.request.urlopen(source['url'], timeout=90) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise RuntimeError('OCR asset checksum verification failed: ' + source['url'])
        if source['url'].endswith('.traineddata'):
            target = ROOT / 'tessdata' / source['url'].rsplit('/', 1)[1]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for name in archive.namelist():
                    if (name.startswith('x64/') or name.startswith('runtimes/win-x64/native/')) and name.endswith(('.dll', '.exe')):
                        target = ROOT / 'windows' / Path(name).name
                        target.parent.mkdir(exist_ok=True)
                        target.write_bytes(archive.read(name))
    (ROOT / 'SOURCES.json').write_text(json.dumps(SOURCES, indent=2))
    print('Offline English + Arabic OCR assets verified and prepared.')

if __name__ == '__main__':
    main()
