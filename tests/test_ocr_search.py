import io
import json
import stat
import unittest
import zipfile
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

    def test_workspace_archive_rejects_traversal_before_extracting(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "unsafe.zip"
            destination = root / "workspace"
            destination.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("..\\outside.txt", "should not be extracted")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "invalid file path"):
                    canvas_app.restore_workspace_archive(archive, destination)
            self.assertFalse((root / "outside.txt").exists())

    def test_workspace_archive_accepts_normal_workspace_paths(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "safe.zip"
            destination = root / "workspace"
            destination.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("notes/biology.rnote", "{}")
                archive.writestr("notes_assets/page.png", b"image")
            with zipfile.ZipFile(archive_path) as archive:
                canvas_app.restore_workspace_archive(archive, destination)
            self.assertEqual((destination / "notes" / "biology.rnote").read_text(encoding="utf-8"), "{}")
            self.assertEqual((destination / "notes_assets" / "page.png").read_bytes(), b"image")

    def test_workspace_archive_rejects_symlinks(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "symlink.zip"
            destination = root / "workspace"
            destination.mkdir()
            link = zipfile.ZipInfo("notes/link.rnote")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(link, "../../outside.rnote")
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    canvas_app.restore_workspace_archive(archive, destination)

    def test_workspace_archive_rejects_oversized_entries(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive_path = root / "large.zip"
            destination = root / "workspace"
            destination.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("notes.rnote", "small")
            with zipfile.ZipFile(archive_path) as archive:
                archive.getinfo("notes.rnote").file_size = 2 * 1024**3 + 1
                with self.assertRaisesRegex(ValueError, "too large"):
                    canvas_app.restore_workspace_archive(archive, destination)


if __name__ == "__main__":
    unittest.main()
