# PyInstaller build definition for the Windows desktop app.
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
ASSETS = ROOT / "assets"

a = Analysis(
    [str(ROOT / "canvas_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ASSETS), "assets")],
    hiddenimports=["PySide6.QtPdf", "PySide6.QtMultimedia"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # OCR model runtimes are optional and would make the base installer enormous.
    excludes=["torch", "transformers", "faster_whisper", "cv2", "numpy", "pandas", "scipy", "matplotlib"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RobustNotes",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Keep Qt's native DLLs untouched; altering these can break QtCore imports
    # on machines without the exact development environment used to build.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "robustnotes-icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RobustNotes")
