ExamGen for Windows (64-bit Windows 10 / 11)

1. Extract the ZIP to a folder.
2. Double-click ExamGen.exe. No Python installation is needed.
3. Your default browser opens the app automatically. If it does not, copy the
   local address shown in the ExamGen window into your browser.
4. Keep the ExamGen window open while you study. Close it, or press Ctrl+C,
   when finished. Closing the browser alone does not stop the server.

Upload text-based past-paper PDFs, explore recurring topics and question
passages, and generate mock exams with answers and explanations. Try practice
demo without uploading papers. Scanned pages are recognized automatically in English and Arabic. OCR can
take a few minutes; split long papers if recognition times out.

Diagram and photo understanding (fully local, optional):
1. Install Ollama from https://ollama.com/download/windows and open it.
2. In ExamGen, open Diagram lab and click Download vision model (about 3 GB).
3. Choose your paper/page, drag around one diagram, and click Analyze page.
4. Check the result against the source, then mark it reviewed. Enable reviewed
   diagram questions when generating a mock exam to include its draft MCQs.

Default model: qwen2.5vl:3b, suitable for an RTX 4050 laptop. Ollama automatically
uses supported NVIDIA GPUs. Diagram lab reports GPU memory after analysis;
CPU fallback is slower. Close other GPU-heavy apps if memory is tight.
The model can misidentify medical images; verify interpretations and answers.
Old library entries need the same PDF uploaded again to restore source images.
Reanalyzing a page replaces its interpretation and resets its review status.

Your library is saved under %LOCALAPPDATA%\ExamGen\examgen.sqlite3, not beside
this executable. Back up that file when the app is closed. Updating or moving
ExamGen.exe does not remove your saved papers. Original PDFs and diagram interpretations are retained in this local database.
The app runs only on your own computer's loopback interface; it does not send
papers to external services. No API key is required. Diagram analysis needs a one-time model download;
after that, it runs offline. Text analysis and OCR need no model download.

This build is unsigned; Windows may show a publisher/SmartScreen warning.
Verify where you obtained the file before deciding whether to run it.
The included SHA256SUMS.txt can be used to verify file integrity:
  PowerShell: Get-FileHash .\ExamGen.exe -Algorithm SHA256

Educational scope: 16 clinical topics and 30 practice MCQs. Topic and question
recurrence uses keyword matching and word overlap. Explanations should be
checked against your medical curriculum and current guidance.

Diagnostics: if startup fails, see %LOCALAPPDATA%\ExamGen\startup-error.log.
Advanced: ExamGen.exe --no-browser --port 8000
