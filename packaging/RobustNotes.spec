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
    # Pillow is optional with the OCR stack. Do not silently freeze the
    # developer machine's version into the base app; OCR users install the
    # security-current version from ocr-requirements.txt.
    excludes=["torch", "transformers", "faster_whisper", "cv2", "numpy", "PIL", "pandas", "scipy", "matplotlib"],
    noarchive=False,
    optimize=1,
)
# PyInstaller can discover unrelated Windows runtime DLLs from tools installed
# on PATH (for example, libheif's ucrtbase and Poppler's ICU 78 libraries). The
# bootloader/Qt then load those ahead of the matching Windows runtime, causing
# QtCore to fail with "The specified procedure could not be found". Keep the
# serviced Universal CRT and Windows ICU on the target system; retain ICU only
# if it is explicitly supplied by the PySide6 wheel itself.
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.casefold() != "ucrtbase.dll"
    and not (
        Path(entry[0]).name.casefold().startswith("icu")
        and not any(part.casefold() == "pyside6" for part in Path(entry[1]).parts)
    )
]
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
