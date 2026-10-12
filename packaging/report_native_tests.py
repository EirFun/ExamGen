"""Expose native Windows test failures as GitHub annotations."""
from pathlib import Path
import xml.etree.ElementTree as ET
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

path=Path('build/native-results.xml')
if path.exists():
    root=ET.parse(path).getroot()
    for case in root.iter('testcase'):
        for failure in list(case.findall('failure')) + list(case.findall('error')):
            message=((failure.text or failure.get('message',''))[:6000]).replace('%','%25').replace('\r','%0D').replace('\n','%0A')
            print(f"::error title={case.get('name','Native Windows test')}::{message}")
