# Windows installer

From PowerShell in the project root, run:

```powershell
.\packaging\build_windows.ps1
```

The script installs the build dependencies, creates a self-contained **single-file** `dist\RobustNotes.exe`, verifies its embedded Python and Qt runtime, then compiles the installer with Inno Setup 6 or 7. It checks `PATH` and standard Program Files locations for `ISCC.exe`. The 1.0.6 installer is written to `packaging\output\RobustNotes-Setup-1.0.6.exe`. For each release, keep `APP_VERSION` in `canvas_app.py` and `AppVersion` in `RobustNotes.iss` in sync, build the installer, and upload `RobustNotes-Setup-<version>.exe` to a stable GitHub Release on `specrthyahskod/RobustNotes`. Installed Windows builds use that release asset for in-place updates; the Inno Setup `AppId` keeps the existing install location and user profile data. Distribute the setup executable to new users. The generated single-file executable can also run from Downloads without a neighboring `_internal` folder.

The installer is per-user. RobustNotes keeps profile data in that Windows user's local app data. The optional OCR stack is excluded from the base installer because its transformer packages and model weights are large; install `ocr-requirements.txt` into a development environment to enable OCR there.
