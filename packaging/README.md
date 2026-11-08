# Windows installer

From PowerShell in the project root, run:

```powershell
.\packaging\build_windows.ps1
```

The script installs the build dependencies, creates the self-contained `dist\RobustNotes` application with PyInstaller, and compiles the installer with Inno Setup 6 or 7. It checks `PATH` and the standard Program Files install locations for `ISCC.exe`. The current installer is written to `packaging\output\RobustNotes-Setup-0.1.4.exe`. For each release, keep `APP_VERSION` in `canvas_app.py` and `AppVersion` in `RobustNotes.iss` in sync, build the installer, and upload `RobustNotes-Setup-<version>.exe` to a stable GitHub Release on `specrthyahskod/RobustNotes-`. Installed Windows builds use that release asset for in-place updates; the Inno Setup `AppId` keeps the existing install location and user profile data. Distribute the setup executable to new users; `RobustNotes.exe` inside `dist\RobustNotes` is only the app launcher and must stay beside the complete `_internal` folder.

The installer is per-user. RobustNotes keeps profile data in that Windows user's local app data. The optional OCR stack is excluded from the base installer because its transformer packages and model weights are large; install `ocr-requirements.txt` into a development environment to enable OCR there.
