"""Render a short demo video from the actual RobustNotes Qt windows."""

import os
import sys
import tempfile
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


def draw_demo_page(notebook, transcription):
    scene = notebook.pages[0]
    background = QPixmap(scene.template_pixmap)
    painter = QPainter(background)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#1e4961"))
    painter.setFont(QFont("Segoe Script", 62))
    painter.drawText(QPointF(145, 500), "Photosynthesis uses light to create energy.")
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
    return scene


def main():
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/demo-video.mp4")
    transcription = " ".join(sys.argv[2:]).strip() or "Photosynthesis uses light to create energy."
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
        scene = draw_demo_page(notebook, transcription)
        notebook.show()
        notebook.ai_sidebar.hide()
        app.processEvents()
        notebook.canvas.fit_page()
        app.processEvents()
        stages.append((capture(notebook), "Write and annotate", "A notebook page with stylus-friendly tools and page-aware context."))

        notebook.ocr_target_scene = scene

        def capture_review_and_accept():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QDialog):
                stages.append((capture(dialog), "Review in a cursive-style preview", "Correct recognition before saving searchable page text."))
                dialog.accept()

        QTimer.singleShot(700, capture_review_and_accept)
        notebook.review_ocr_result(transcription)
        workspace.refresh_documents()
        workspace.search_input.setText("photosynthesis")
        app.processEvents()
        workspace.resize(WIDTH, HEIGHT)
        workspace.show()
        workspace.open_dashboard()
        workspace.search_input.setText("photosynthesis")
        app.processEvents()
        stages.append((capture(workspace), "Find recognized handwriting", "Search matches OCR text saved with the notebook page."))

        if len(stages) < 4:
            raise RuntimeError(f"Expected four demo scenes, captured {len(stages)}.")
        writer = cv2.VideoWriter(
            str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
        )
        if not writer.isOpened():
            raise RuntimeError("OpenCV could not create the MP4 video file.")
        try:
            for raw_frame, title, detail in stages:
                frame = add_caption(raw_frame, title, detail)
                for _ in range(FPS * 4):
                    writer.write(frame)
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
    print(f"Created {output_path.resolve()} ({frame_count} frames, {frame_count / FPS:.1f}s)")


if __name__ == "__main__":
    main()
