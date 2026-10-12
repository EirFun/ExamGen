"""Regression: a non-ASCII user folder must not crash a limited console."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request


def test_unicode_data_folder_with_ascii_console(tmp_path):
    folder=tmp_path/'مجلد عربي'
    env=os.environ.copy()
    env['PYTHONIOENCODING']='ascii'
    process=subprocess.Popen([sys.executable,'desktop.py','--no-browser','--data-dir',str(folder)],cwd=Path(__file__).resolve().parents[1],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        address=None
        for _ in range(8):
            line=process.stdout.readline().decode('ascii')
            match=re.search(r'http://127\.0\.0\.1:\d+',line)
            if match:
                address=match.group();break
            if not line:
                raise AssertionError(process.stderr.read().decode('ascii',errors='replace'))
        assert address
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(30):
            try:
                response=json.load(client.open(address+'/api/health',timeout=2))
                break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError('Launcher did not start the app')
        assert response['status']=='ok'
        assert (folder/'examgen.sqlite3').exists()
    finally:
        process.terminate()
        process.communicate(timeout=5)
