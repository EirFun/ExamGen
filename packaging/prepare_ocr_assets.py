"""Download hash-pinned offline OCR assets before development or Windows packaging."""
from pathlib import Path
import hashlib
import io
import json
import urllib.request
import zipfile
import os
import subprocess
import shutil

ROOT = Path(__file__).resolve().parent.parent / 'ocr_assets'
SOURCES = [{'url': 'https://api.nuget.org/v3-flatcontainer/tesseractocr/5.5.2/tesseractocr.5.5.2.nupkg', 'sha256': '2d11e0a45f8f5887d91277dd61e4b2b57423be0e06aefba432484ebc33a8a794'}, {'url': 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata', 'sha256': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'}, {'url': 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/ara.traineddata', 'sha256': 'e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729'}]

def main():
    ROOT.mkdir(exist_ok=True)
    windows = ROOT / 'windows'
    windows.mkdir(exist_ok=True)
    # Remove the obsolete generated runtime (14.29); never package it again.
    for pattern in ('msvcp140*.dll', 'vcruntime140*.dll', 'concrt140.dll'):
        for old in windows.glob(pattern):
            old.unlink()
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
    provenance = list(SOURCES)
    if os.name == 'nt':
        shell = shutil.which('pwsh.exe') or shutil.which('powershell.exe')
        if not shell:
            raise RuntimeError('PowerShell is required to verify Microsoft runtime signatures.')
        shell_env = os.environ.copy()
        if Path(shell).name.lower() == 'powershell.exe':
            shell_env['PSModulePath'] = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules')
        subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('prepare_msvc_runtime.ps1')), '-Destination', str(windows)], check=True, env=shell_env)
        provenance.append({'microsoft_runtime': json.loads((windows / 'RUNTIME-SOURCES.json').read_text(encoding='utf-8-sig'))})
    (ROOT / 'SOURCES.json').write_text(json.dumps(provenance, indent=2))
    print('Offline English + Arabic OCR assets verified and prepared.')

if __name__ == '__main__':
    main()
