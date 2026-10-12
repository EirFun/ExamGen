"""Local page-level OCR fallback. No documents are sent to external services."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import closing

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'ocr_assets'
_LOCK = threading.Lock()

class OCRError(Exception):
    pass


def recognize_pdf_pages(payload: bytes, texts: list[str]) -> tuple[list[str], int]:
    candidates = [i for i, text in enumerate(texts) if len(re.sub(r'\W', '', text)) < 30]
    if not candidates:
        return texts, 0
    if not _LOCK.acquire(blocking=False):
        raise OCRError('Another scanned paper is being processed. Please retry when it finishes.')
    try:
        return _recognize(payload, texts, candidates)
    finally:
        _LOCK.release()


def _recognize(payload, texts, candidates):
    import pypdfium2 as pdfium
    engine = ASSETS / 'windows' / 'tesseract.exe' if os.name == 'nt' else shutil.which('tesseract')
    output = list(texts)
    recognized = 0
    started = time.monotonic()
    with pdfium.PdfDocument(payload) as document, tempfile.TemporaryDirectory(prefix='examgen-ocr-') as temporary:
        image_path = Path(temporary) / 'page.png'
        for index in candidates:
            with closing(document[index]) as page:
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    continue
                scale = min(200 / 72, 2500 / max(width, height), (6_000_000 / (width * height)) ** 0.5)
                with closing(page.render(scale=scale)) as bitmap:
                    image = bitmap.to_pil().convert('L')
                    try:
                        if image.getextrema()[0] > 245:  # Blank pages do not need an OCR process.
                            continue
                        image.save(image_path)
                    finally:
                        image.close()
            if not engine or not Path(engine).is_file():
                raise OCRError('OCR engine is missing. Install Tesseract or download the current Windows OCR build.')
            if not all((ASSETS / 'tessdata' / f'{language}.traineddata').is_file() for language in ('eng', 'ara')):
                raise OCRError('OCR language files are missing. Run packaging/prepare_ocr_assets.py or use the current Windows build.')
            remaining = 240 - (time.monotonic() - started)
            if remaining <= 0:
                raise OCRError('OCR took too long. Split this paper into smaller PDFs and retry.')
            try:
                env = os.environ.copy()
                env['OMP_THREAD_LIMIT'] = '2'
                process = subprocess.run(
                    [str(engine), str(image_path), 'stdout', '--tessdata-dir', str(ASSETS / 'tessdata'), '-l', 'eng+ara', '--psm', '3'],
                    capture_output=True, timeout=min(45, remaining), env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
            except subprocess.TimeoutExpired:
                raise OCRError(f'OCR timed out on page {index + 1}. Try a clearer scan or split the PDF.') from None
            except OSError:
                raise OCRError('Could not start the OCR engine. Use the latest Windows build or check your Tesseract installation.') from None
            if process.returncode:
                raise OCRError(f'OCR could not read page {index + 1}. Try a clearer scan or a newly exported PDF.')
            text = process.stdout.decode('utf-8', errors='replace').strip()
            if len(re.sub(r'\W', '', text)) > len(re.sub(r'\W', '', texts[index])):
                output[index] = text
                recognized += 1
    return output, recognized
