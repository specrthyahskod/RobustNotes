"""Fail a Windows packaging build if the one-file archive is incomplete."""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


def main() -> int:
    executable = Path(sys.argv[1])
    try:
        archive = CArchiveReader(str(executable))
    except Exception as error:
        print(f"Could not inspect the packaged executable: {error}", file=sys.stderr)
        return 1

    entries = {str(name).replace("\\", "/").casefold() for name in archive.toc}
    python_dll = f"python{sys.version_info.major}{sys.version_info.minor}.dll".casefold()
    requirements = {
        "Python runtime": any(Path(name).name == python_dll for name in entries),
        "PySide6 QtCore module": any(name.endswith("/qtcore.pyd") for name in entries),
        "Qt Core runtime": any(name.endswith("/qt6core.dll") for name in entries),
        "RobustNotes icon asset": any(name.endswith("assets/robustnotes-icon.ico") for name in entries),
    }
    missing = [label for label, present in requirements.items() if not present]
    if missing:
        print("Windows bundle is incomplete; missing: " + ", ".join(missing), file=sys.stderr)
        print("Archive entries containing relevant names:", file=sys.stderr)
        for name in sorted(entries):
            if "python" in name or "qtcore" in name or "robustnotes-icon" in name:
                print(f"  {name}", file=sys.stderr)
        return 1

    print(f"Verified self-contained Windows bundle: {executable}")
    for label in requirements:
        print(f"  OK: {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
