import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

import canvas_app


class FakeProcessor:
    @classmethod
    def from_pretrained(cls, _name):
        return cls()

    def __call__(self, *, images, return_tensors):
        assert images
        assert return_tensors == "pt"
        return type("Encoded", (), {"pixel_values": type("Pixels", (), {"to": lambda self, _device: self})()})()

    def batch_decode(self, _output, skip_special_tokens=True):
        assert skip_special_tokens
        return ["  Photosynthesis   converts sunlight ,  "]


class FakeModel:
    @classmethod
    def from_pretrained(cls, _name):
        return cls()

    def eval(self):
        return self

    def to(self, _device):
        return self

    def generate(self, _inputs, max_new_tokens):
        assert max_new_tokens == 128
        return object()


class OCRAndSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ocr_normalization_keeps_paragraphs_and_fixes_spacing(self):
        result = canvas_app.normalize_ocr_text("  First   line ,\r\n\r\n Second   line !  ")
        self.assertEqual(result, "First line,\n\nSecond line!")

    def test_ocr_line_detection_groups_rows_and_ignores_dot_grid(self):
        image = Image.new("RGB", (700, 260), "white")
        draw = ImageDraw.Draw(image)
        for x in range(12, 700, 24):
            for y in range(12, 260, 24):
                draw.point((x, y), fill="#d8d8d8")
        draw.text((35, 55), "first handwritten line", fill="black")
        draw.text((35, 155), "second handwritten line", fill="black")
        stream = io.BytesIO()
        image.save(stream, format="PNG")

        crops = canvas_app.extract_handwriting_line_crops(stream.getvalue())
        self.assertEqual(len(crops), 2)

    def test_ocr_worker_preprocesses_page_and_emits_clean_transcription(self):
        image = Image.new("RGB", (700, 220), "white")
        ImageDraw.Draw(image).text((35, 55), "sample handwriting", fill="black")
        stream = io.BytesIO()
        image.save(stream, format="PNG")

        previous_processor, previous_model = canvas_app._TROCR_PROCESSOR, canvas_app._TROCR_MODEL
        canvas_app._TROCR_PROCESSOR = None
        canvas_app._TROCR_MODEL = None
        worker = canvas_app.HandwritingOCRWorker(stream.getvalue())
        received, errors = [], []
        worker.recognized.connect(received.append)
        worker.failed.connect(errors.append)
        with patch("transformers.TrOCRProcessor.from_pretrained", FakeProcessor.from_pretrained), patch(
            "transformers.VisionEncoderDecoderModel.from_pretrained", FakeModel.from_pretrained
        ):
            worker.run()
        canvas_app._TROCR_PROCESSOR, canvas_app._TROCR_MODEL = previous_processor, previous_model

        self.assertEqual(errors, [])
        self.assertEqual(received, ["Photosynthesis converts sunlight,"])

    def test_search_index_source_includes_saved_ocr_text(self):
        with TemporaryDirectory() as temporary_directory:
            note_path = Path(temporary_directory) / "Biology.rnote"
            note_path.write_text(json.dumps({
                "pages": [{
                    "title": "Cell energy",
                    "source_text": "Class lecture notes",
                    "ocr_text": "Photosynthesis converts sunlight into chemical energy.",
                }]
            }), encoding="utf-8")

            searchable = canvas_app.WorkspaceWindow.searchable_document_text(note_path).casefold()
            self.assertIn("photosynthesis", searchable)
            self.assertIn("chemical energy", searchable)
            self.assertIn("cell energy", searchable)


if __name__ == "__main__":
    unittest.main()
