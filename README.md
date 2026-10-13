# ExamGen

A local medical-school past-paper analyzer and mock exam studio. No API key or external AI service required.

## Windows download

[Download ExamGen for Windows (64-bit Windows 10/11)](https://github.com/EirFun/ExamGen/raw/refs/heads/main/downloads/ExamGen-Windows-x64.zip). Extract the ZIP and double-click **ExamGen.exe**. No Python installation is required. Keep the ExamGen console window open while using the app. This build is unsigned and may trigger a Windows publisher warning. Build provenance and validation details are included in BUILD-INFO.json inside the download. Native Windows CI tests the OCR and local vision integration pipelines and packaged executable before publishing a build. [Validation run](https://github.com/EirFun/ExamGen/actions/runs/38350474923) includes 19 application tests, packaged OCR/vision checks, and a separate real-model CPU smoke test on a synthetic diagram.

## Fully local diagrams and photographs

**Diagram lab** interprets PDF images using a downloaded Ollama vision model. No API key or cloud inference is used.

1. Install [Ollama for Windows](https://ollama.com/download/windows), then open Ollama from the Start menu.
2. Open ExamGen → **Diagram lab** → **Download vision model**. The default `qwen2.5vl:3b` download is about 3 GB; Internet is needed once for installation and model download.
3. Select a paper/page. Drag around one diagram for better detail, add an optional question, and choose **Create similar mock-exam questions**, optionally paste the original question, and click **Create similar questions**. Crop the diagram alone so the original answers are not included in the exam image. Variations reuse that exact cropped diagram and target the original concept with new stems, options, answers and explanations. **Explain the image** remains available, including analysis of up to 20 pages in a batch.
4. Compare the interpretation with the source. Mark it reviewed to include its topics in recurrence analysis and its draft questions in mock exams (enable **Include reviewed diagram questions**).

The 3B model is the default for a laptop RTX 4050, usually with 6 GB VRAM. Ollama selects the GPU automatically; the lab displays reported GPU memory after analysis. Close GPU-heavy applications and crop one diagram if memory is tight. CPU fallback is supported but slower. No GPU speed claim is made by the CPU CI check.

Interpretations can misidentify medical images or invent answers: this is a general vision model, not a validated diagnostic model. Uncertain output stays a draft until reviewed. Reanalyzing a page replaces its previous interpretation and resets review. Cancellation stops between pages; an in-progress page may finish first.

Existing library entries from older releases need the identical PDF uploaded again to restore their images, preserving the same entry. Back up the SQLite file with the app closed; it now contains original PDFs too. All page images, OCR text and inference requests stay on loopback. The transport rejects external servers, cloud model aliases and redirects. Model downloads fetch model files, not papers.

Advanced source configuration: `EXAMGEN_OLLAMA_URL` accepts only an HTTP loopback Ollama URL; `EXAMGEN_VISION_MODEL` can select another installed local vision model. The default uses a 4096-token context. Tests exercise the complete pipeline with a loopback stub; a separate Windows CI job runs the actual model on a synthetic labelled diagram using CPU, not a medical accuracy benchmark.

## Run from source

Requires Python 3.12+.

```bash
cd ExamGen
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
.venv/bin/python packaging/prepare_ocr_assets.py
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
```

For source development on Linux, install the Tesseract executable (`sudo apt-get install tesseract-ocr` on Ubuntu/Debian). The Windows executable already includes the engine and models.

Open the app on port 8000 in your normal development environment. Upload one or more text-based PDFs (20 MB, 300 pages maximum each), explore topics and source excerpts, and generate a mock exam. Use **Try practice demo** to explore without papers. Choose answers, finish the exam, and review the answer key and explanations. Results can be exported as JSON or printed/saved as PDF using the browser.

```bash
.venv/bin/python -m pytest -q
```

## How it works

- pypdf extracts text; pages with fewer than 30 letters/digits are rendered by PDFium and automatically recognized locally with Tesseract (English + Arabic). extracted pages and paper metadata persist in SQLite under ignored `data/`. Original PDFs are retained locally in SQLite for diagram analysis. `EXAMGEN_DATA_DIR` can override storage.
- Sixteen core clinical topics use explicit keyword/phrase matching. Topics rank first by distinct papers, then matching passages. Numbered questions are split into passages; unnumbered content is analyzed as a larger passage. Counts are **matching passages**, not guaranteed question counts. One passage may cover several topics. High yield refers to recurrence in the uploaded collection, not a prediction.
- Similar question passages are grouped when they share the same detected topics and at least 65% overlap in normalized unique word sets. These groups rank by distinct papers, then topic recurrence. This heuristic can miss paraphrases or group similar questions; it is not semantic duplicate detection.
- Source evidence retains paper names, page numbers and excerpts. At most eight excerpts per topic are shown. Unsupported topics are not classified, and synonym coverage is limited.
- Mock exams sample without replacement from 30 included medical MCQs and optionally reviewed diagram questions, weighted by topic recurrence. Options are shuffled. If the selected topics contain fewer questions than requested, the actual count is shown. Uploaded questions are evidence for topic selection; text-only uploads do not generate inferred answers. Reviewed visual questions are clearly labelled with their source and model. The server stores the answer key and reveals it after submission.
- Scanned/image-only pages are recognized automatically. OCR quality depends on scan clarity; blank papers are rejected; pages with visible diagrams can be uploaded even without readable text. Recognition is limited to 45 seconds per page and four minutes per upload, with a six-megapixel rendered-page cap. Split large scanned papers if they time out. Password-protected PDFs need unlocking. Duplicate papers are rejected by content hash.

This is a single-user development app. It has no login or user isolation; bind to loopback for private local use. Do not expose it as a public multi-user service without authentication, storage isolation, upload rate limits and resource-isolated PDF processing. PDFs are untrusted input; document size/page/text caps are provided but parsing is not sandboxed. Keep patient-identifying data out of uploads. Medical content is study material: verify it against your curriculum and current guidance before relying on it.

The interface works without remote assets; it uses local system-font fallbacks. No paper contents are sent to external services.

## Windows desktop executable

The committed Windows x64 distribution is `downloads/ExamGen-Windows-x64.zip`; rebuilding produces `dist/windows/ExamGen.exe`. Extract it and double-click `ExamGen.exe`. Python is bundled, so users do not need to install Python. The launcher opens the local application in the default browser, binds only to loopback on a free port, and keeps a console open until stopped. Paper data persists under `%LOCALAPPDATA%\ExamGen` across executable updates. Closing the console stops the app; closing the browser alone does not.

To rebuild on Windows, install Python 3.13 (64-bit) and the current Microsoft Visual C++ x64 Redistributable (runtime 14.43 or newer) and run `packaging/build-windows.ps1` in PowerShell. Dependencies are pinned in `requirements-windows-build.txt`. The build script downloads checksum-pinned Tesseract binaries, runtime libraries, and English/Arabic models, then bundles them inside the executable. PyInstaller packages the static interface and backend into one executable. This build is unsigned. See `packaging/WINDOWS-README.txt` for usage and diagnostics.

For a source-level launcher smoke check, run `.venv/bin/python desktop.py --no-browser --port 8766 --data-dir /tmp/examgen-desktop-smoke`. Keep test data separate from your real library.

OCR-extracted Arabic text is matched against included Arabic synonyms for the clinical topics; mock questions and explanations remain in English. Original PDFs and saved diagram interpretations are retained locally. OCR assets are generated under ignored `ocr_assets/`; `packaging/prepare_ocr_assets.py` verifies every download against a pinned SHA-256.

Windows OCR uses temporary PNG files with ASCII relative filenames and relative model paths, so Arabic and other non-ASCII Windows profile/folder names are supported. Engine failures are reported separately from unreadable scans; diagnostic details appear in the ExamGen console. OCR extracts text. Use Diagram lab for questions about photographs, equipment or diagrams.

The Windows OCR child process runs with an isolated DLL search path, one processing thread, and generic dot-product routines for CPU compatibility. Native Windows CI tests both real image scans and the packaged executable under Arabic temporary/data paths.

Windows builds select current Microsoft C++ runtime DLLs from Windows or Visual Studio redistributable locations only after Authenticode signature and minimum-version verification. Successful CI builds are saved to unique `windows-builds/<run-id>-<attempt>` branches; ordinary source remains on `main`. This allows the tested native binary to be retrieved without altering source history automatically.
