# Windows installer

From PowerShell in the project root, run:

```powershell
.\packaging\build_windows.ps1
```

The script installs the build dependencies, creates the self-contained `dist\RobustNotes` application with PyInstaller, and compiles the Inno Setup installer when Inno Setup 6 (`ISCC.exe`) is on `PATH`. The installer is written to `packaging\output\RobustNotes-Setup-0.1.0.exe`.

The installer is per-user. RobustNotes keeps profile data in that Windows user's local app data. The optional OCR stack is excluded from the base installer because its transformer packages and model weights are large; install `ocr-requirements.txt` into a development environment to enable OCR there.
