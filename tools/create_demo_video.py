"""Render a short demo video from the actual RobustNotes Qt windows."""

import os
import sys
import tempfile
import base64
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsPathItem, QDialog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import canvas_app


WIDTH, HEIGHT, FPS = 1280, 720, 20
ROOT = Path(__file__).resolve().parents[1]


def capture(window):
    QApplication.processEvents()
    pixmap = window.grab()
    image = pixmap.toImage().convertToFormat(canvas_app.QImage.Format.Format_RGB888)
    raw = np.frombuffer(image.bits(), dtype=np.uint8).reshape(image.height(), image.width(), 3)
    return cv2.resize(cv2.cvtColor(raw, cv2.COLOR_RGB2BGR), (WIDTH, HEIGHT))


def add_caption(frame, title, detail):
    frame = frame.copy()
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (WIDTH, 96), (20, 27, 36), -1)
    cv2.addWeighted(overlay, 0.92, frame, 0.08, 0, frame)
    cv2.putText(frame, title, (34, 42), cv2.FONT_HERSHEY_DUPLEX, 0.82, (240, 248, 252), 1, cv2.LINE_AA)
    cv2.putText(frame, detail, (35, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (190, 207, 220), 1, cv2.LINE_AA)
    cv2.putText(frame, "ROBUSTNOTES  |  BETA PREVIEW", (WIDTH - 360, HEIGHT - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (90, 220, 235), 1, cv2.LINE_AA)
    return frame


def slow_zoom(frame, progress):
    """Apply a restrained, centered push-in so captured UI scenes feel less static."""
    scale = 1.0 + 0.018 * progress
    crop_width, crop_height = int(WIDTH / scale), int(HEIGHT / scale)
    left, top = (WIDTH - crop_width) // 2, (HEIGHT - crop_height) // 2
    return cv2.resize(frame[top:top + crop_height, left:left + crop_width], (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)


def embed_site_capture(image_path, html_image_path):
    """Embed generated WebP captures so the site works when only index.html is uploaded."""
    encoded_image = cv2.imencode(
        ".webp", cv2.imread(str(image_path)), [cv2.IMWRITE_WEBP_QUALITY, 78]
    )[1].tobytes()
    data_uri = "data:image/webp;base64," + base64.b64encode(encoded_image).decode("ascii")
    index_path = ROOT / "index.html"
    markup = index_path.read_text(encoding="utf-8")
    pattern = rf'<img src="{re.escape(html_image_path)}" data-fallback="[^"]+"'
    markup, replacements = re.subn(pattern, f'<img src="{data_uri}"', markup, count=1)
    if replacements != 1:
        raise RuntimeError(f"Could not find the website image tag for {html_image_path}.")
    index_path.write_text(markup, encoding="utf-8")


def draw_demo_page(notebook, transcription):
    scene = notebook.pages[0]
    background = QPixmap(scene.template_pixmap)
    painter = QPainter(background)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#1e4961"))
    painter.setFont(QFont("Segoe Script", 42))
    painter.drawText(QPointF(145, 470), "Photosynthesis turns sunlight into")
    painter.drawText(QPointF(145, 555), "chemical energy for plant growth.")
    painter.end()
    scene.template_pixmap = background
    scene.bg_item.setPixmap(background)
    scene.ocr_text = transcription
    notebook.page_list.item(0).setText("Cell energy")

    underline = QPainterPath(QPointF(142, 280))
    underline.cubicTo(QPointF(400, 302), QPointF(695, 276), QPointF(895, 285))
    mark = QGraphicsPathItem(underline)
    mark.setPen(QPen(QColor("#40cddd"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    mark.setZValue(3)
    scene.addItem(mark)
    notebook.save_document()
    notebook.canvas.set_page_scene(scene)
    notebook.canvas.set_zoom(0.84)
    notebook.update_ai_page_label()
    notebook.ai_status_label.setText("Local handwriting recognition · ready to review")
    return scene, mark


def main():
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/demo-video.mp4")
    output_path = output_path if output_path.is_absolute() else ROOT / output_path
    transcription = " ".join(sys.argv[2:]).strip() or "Photosynthesis turns sunlight into chemical energy for plant growth."
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("RobustNotes Studio")
    app.setStyle("Fusion")
    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for font_file in ("segoeui.ttf", "segoeuib.ttf", "SegoeIcons.ttf", "segoesc.ttf"):
        font_path = font_dir / font_file
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont("Segoe UI", 10))
    QStandardPaths.setTestModeEnabled(True)
    stages = []

    with tempfile.TemporaryDirectory(prefix="robustnotes-demo-") as temporary:
        workspace_dir = Path(temporary)
        workspace = canvas_app.WorkspaceWindow()
        workspace.workspace_dir = workspace_dir
        workspace.workspace_index_path = workspace_dir / ".robustnotes-index.json"
        workspace.workspace_index = {}
        workspace.profile = {"name": "Demo User"}
        workspace.open_dashboard()
        workspace.resize(WIDTH, HEIGHT)
        workspace.show()
        app.processEvents()
        stages.append((capture(workspace), "One workspace for notes", "Create notebooks, text files, and whiteboards from the dashboard."))

        note_path = workspace_dir / "Biology Study Notes.rnote"
        notebook = canvas_app.NotebookWindow(note_path, settings_path=workspace_dir / "settings.ini")
        notebook.resize(WIDTH, HEIGHT)
        scene, mark = draw_demo_page(notebook, transcription)
        notebook.show()
        app.processEvents()
        notebook.canvas.fit_page()
        notebook.chat_input.setPlainText("Summarize the key idea on this page.")
        notebook.ai_status_label.setText("Local model · ready for a page-aware question")
        app.processEvents()
        notebook_frame = capture(notebook)
        ai_capture = ROOT / "docs/screenshots/ai-sidebar.png"
        cv2.imwrite(str(ai_capture), notebook_frame)
        embed_site_capture(ai_capture, "docs/screenshots/ai-sidebar.png")
        stages.append((notebook_frame, "Write and ask", "Page-aware chat can use notebook text and imported PDF context."))

        notebook.ocr_target_scene = scene
        notebook.ocr_target_items = [mark]

        def capture_review_and_accept():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QDialog):
                review_frame = capture(dialog)
                ocr_capture = ROOT / "docs/screenshots/ocr-review.png"
                cv2.imwrite(str(ocr_capture), review_frame)
                embed_site_capture(ocr_capture, "docs/screenshots/ocr-review.png")
                stages.append((review_frame, "Review handwriting OCR", "Correct the transcription or replace selected strokes with editable cursive-style text."))
                dialog.accept()

        QTimer.singleShot(700, capture_review_and_accept)
        notebook.review_ocr_result(transcription)

        def capture_study_card():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QDialog):
                return
            reveal_button = next(
                (button for button in dialog.findChildren(canvas_app.QPushButton)
                 if button.text() == "Reveal answer"),
                None,
            )
            if reveal_button is not None:
                reveal_button.click()
            app.processEvents()
            stages.append((capture(dialog), "Turn notes into study cards", "Generate a quick recall prompt from recognized notebook text."))
            dialog.accept()

        QTimer.singleShot(700, capture_study_card)
        notebook.study_page(0)
        workspace.refresh_documents()
        workspace.search_input.setText("sunlight")
        app.processEvents()
        workspace.resize(WIDTH, HEIGHT)
        workspace.show()
        workspace.open_dashboard()
        workspace.search_input.setText("sunlight")
        app.processEvents()
        stages.append((capture(workspace), "Find recognized handwriting", "Search matches OCR text saved with the notebook page."))

        if len(stages) < 5:
            raise RuntimeError(f"Expected five demo scenes, captured {len(stages)}.")
        writer = cv2.VideoWriter(
            str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
        )
        if not writer.isOpened():
            raise RuntimeError("OpenCV could not create the MP4 video file.")
        try:
            captioned = [add_caption(frame, title, detail) for frame, title, detail in stages]
            hold_frames = FPS * 3
            transition_frames = FPS * 3 // 4
            for scene_index, frame in enumerate(captioned):
                for frame_index in range(hold_frames):
                    writer.write(slow_zoom(frame, frame_index / max(1, hold_frames - 1)))
                if scene_index + 1 < len(captioned):
                    next_frame = captioned[scene_index + 1]
                    for frame_index in range(transition_frames):
                        progress = (frame_index + 1) / transition_frames
                        eased = progress * progress * (3 - 2 * progress)
                        blended = cv2.addWeighted(frame, 1 - eased, next_frame, eased, 0)
                        writer.write(blended)
        finally:
            writer.release()
        workspace.close()
        notebook.close()

    verify = cv2.VideoCapture(str(output_path))
    ok, first_frame = verify.read()
    frame_count = int(verify.get(cv2.CAP_PROP_FRAME_COUNT))
    verify.release()
    if not ok or frame_count < FPS * 8:
        raise RuntimeError("The rendered MP4 did not pass its playback sanity check.")
    print(f"Created {output_path.resolve()} ({frame_count} frames, {frame_count / FPS:.1f}s, {len(stages)} scenes with crossfades)")


if __name__ == "__main__":
    main()
