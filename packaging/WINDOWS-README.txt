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

Your library is saved under %LOCALAPPDATA%\ExamGen\examgen.sqlite3, not beside
this executable. Back up that file when the app is closed. Updating or moving
ExamGen.exe does not remove your saved papers. Original PDFs are not retained.
The app runs only on your own computer's loopback interface; it does not send
papers to external services. No internet connection or API key is required.

This build is unsigned; Windows may show a publisher/SmartScreen warning.
Verify where you obtained the file before deciding whether to run it.
The included SHA256SUMS.txt can be used to verify file integrity:
  PowerShell: Get-FileHash .\ExamGen.exe -Algorithm SHA256

Educational scope: 16 clinical topics and 30 practice MCQs. Topic and question
recurrence uses keyword matching and word overlap. Explanations should be
checked against your medical curriculum and current guidance.

Diagnostics: if startup fails, see %LOCALAPPDATA%\ExamGen\startup-error.log.
Advanced: ExamGen.exe --no-browser --port 8000
