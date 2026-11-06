# Windows installer

From PowerShell in the project root, run:

```powershell
.\packaging\build_windows.ps1
```

The script installs the build dependencies, creates the self-contained `dist\RobustNotes` application with PyInstaller, and compiles the installer with Inno Setup 6 or 7. It checks `PATH` and the standard Program Files install locations for `ISCC.exe`. The installer is written to `packaging\output\RobustNotes-Setup-0.1.0.exe`. Distribute this setup executable to users who want an installer; `RobustNotes.exe` inside `dist\RobustNotes` is only the app launcher and must stay beside the complete `_internal` folder.

The installer is per-user. RobustNotes keeps profile data in that Windows user's local app data. The optional OCR stack is excluded from the base installer because its transformer packages and model weights are large; install `ocr-requirements.txt` into a development environment to enable OCR there.
