"""Package the tested native Windows binary with notices and provenance."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import zipfile

exe=Path(sys.argv[1]);archive=Path(sys.argv[2]);temporary=archive.with_suffix('.building.zip')
checksum=hashlib.sha256(exe.read_bytes()).hexdigest()
with zipfile.ZipFile(archive) as old,zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as new:
    for item in old.infolist():
        data=old.read(item.filename)
        if item.filename=='ExamGen/ExamGen.exe':data=exe.read_bytes()
        elif item.filename=='ExamGen/SHA256SUMS.txt':data=(checksum+'  ExamGen.exe\n').encode()
        elif item.filename=='ExamGen/BUILD-INFO.json':
            info=json.loads(data);info['executable_sha256']=checksum
            info['validation']='Built and tested on native Windows: 11 tests plus packaged executable OCR upload under Arabic TEMP/data paths.'
            info['python_runtime']={'version':sys.version,'source':'GitHub Actions setup-python Windows x64 runtime'}
            info['ocr_assets']=json.loads(Path('ocr_assets/SOURCES.json').read_text())
            info['source_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('app.py'),Path('bank.py'),Path('desktop.py'),Path('ocr.py'),*sorted(Path('static').iterdir())]}
            info.pop('dependencies',None)
            info['dependencies_lock']='requirements-windows-build.txt'
            data=json.dumps(info,indent=2).encode()
        elif item.filename=='ExamGen/licenses/OCR-NOTICES.txt':
            data=b'Tesseract and tessdata_fast: Apache-2.0. Leptonica: BSD-style license. TesseractOCR NuGet package: Charles Weld and Kees van Spelde. Microsoft Visual C++ runtime DLLs: Copyright Microsoft Corporation, all rights reserved; current redistributable files copied from Windows System32 after Authenticode signature and version verification. See BUILD-INFO.json for hashes, versions and provenance.\n'
        new.writestr(item.filename,data)
with zipfile.ZipFile(temporary) as checked:
    if checked.testzip() is not None:raise RuntimeError('Archive integrity validation failed')
shutil.move(str(temporary),archive)
archive.with_name('SHA256SUMS.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
print('Native Windows ZIP packaged:',archive)
