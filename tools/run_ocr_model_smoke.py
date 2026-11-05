"""Run the real local TrOCR checkpoint against a generated handwriting sample."""

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import canvas_app


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("RobustNotes Studio")

    image = Image.new("RGB", (2200, 340), "white")
    draw = ImageDraw.Draw(image)
    font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segoesc.ttf")
    font = ImageFont.truetype(font_path, 70) if os.path.isfile(font_path) else ImageFont.load_default()
    draw.text((55, 85), "Photosynthesis uses light to create energy.", font=font, fill="#172b36")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")

    worker = canvas_app.HandwritingOCRWorker(encoded.getvalue())
    result, errors = [], []
    worker.progress.connect(lambda message: print(message, flush=True))
    worker.recognized.connect(result.append)
    worker.failed.connect(errors.append)
    worker.run()
    if errors:
        print(f"OCR_FAILED: {errors[0]}", file=sys.stderr)
        return 1
    if not result:
        print("OCR_FAILED: the worker returned no transcription", file=sys.stderr)
        return 1
    print(f"OCR_RESULT: {result[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
