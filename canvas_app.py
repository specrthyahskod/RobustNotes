from __future__ import annotations

import math
import ast
import base64
import ctypes
import hashlib
import os
import html
import io
import json
import re
import random
import sys
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
import uuid
import zipfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Optional, Sequence, TypedDict, cast
from urllib.parse import urlencode, urlparse


APP_VERSION = "1.0.6"
UPDATE_REPOSITORY = "specrthyahskod/RobustNotes"


_ACTIVE_BACKGROUND_WORKERS: set[Any] = set()
_ACTIVE_BACKGROUND_WORKERS_LOCK = threading.Lock()


def retain_background_worker(worker: QThread) -> None:
    """Keep a running Qt thread alive if its owning window closes first."""
    with _ACTIVE_BACKGROUND_WORKERS_LOCK:
        _ACTIVE_BACKGROUND_WORKERS.add(worker)

    def release() -> None:
        with _ACTIVE_BACKGROUND_WORKERS_LOCK:
            _ACTIVE_BACKGROUND_WORKERS.discard(worker)

    worker.finished.connect(release)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect_remembered_login(payload: bytes) -> bytes:
    """Encrypt sign-in data for the current Windows account using DPAPI."""
    if sys.platform != "win32":
        raise OSError("Remembered sign-in is only supported on Windows.")
    source_buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    source = _DataBlob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    encrypted = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "RobustNotes saved sign-in", None, None, None, 0x1,
        ctypes.byref(encrypted),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(encrypted.pbData, encrypted.cbData)
    finally:
        kernel32.LocalFree(encrypted.pbData)


def unprotect_remembered_login(payload: bytes) -> bytes:
    """Decrypt a DPAPI sign-in record for the current Windows account."""
    if sys.platform != "win32":
        raise OSError("Remembered sign-in is only supported on Windows.")
    source_buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    source = _DataBlob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    decrypted = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(decrypted)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(decrypted.pbData, decrypted.cbData)
    finally:
        kernel32.LocalFree(decrypted.pbData)


def restore_workspace_archive(archive: zipfile.ZipFile, destination: Path) -> None:
    """Safely stage and restore a bounded ZIP archive into a workspace."""
    members = archive.infolist()
    if len(members) > 10_000:
        raise ValueError("The archive contains too many entries.")

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    seen_paths: set[str] = set()
    total_size = 0
    max_member_size = 2 * 1024**3
    max_total_size = 4 * 1024**3
    validated_members: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    for member in members:
        raw_name = member.filename
        normalized_name = raw_name.replace("\\", "/")
        parts = normalized_name.split("/")
        if member.is_dir() and parts and parts[-1] == "":
            parts.pop()
        if (
            not normalized_name
            or normalized_name.startswith("/")
            or "\x00" in normalized_name
            or any(part in ("", ".", "..") or ":" in part for part in parts)
        ):
            raise ValueError("The archive contains an invalid file path.")

        relative_path = PurePosixPath(*parts)
        target = (root / Path(*relative_path.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError("The archive contains an invalid file path.") from error

        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise ValueError("The archive contains a symbolic link.")
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError("The archive contains an unsupported file type.")

        canonical_path = "/".join(relative_path.parts).casefold()
        if canonical_path in seen_paths:
            raise ValueError("The archive contains duplicate file paths.")
        seen_paths.add(canonical_path)

        if member.file_size < 0 or member.file_size > max_member_size:
            raise ValueError("The archive contains a file that is too large.")
        if not member.is_dir():
            total_size += member.file_size
            if total_size > max_total_size:
                raise ValueError("The archive expands beyond the 4 GB restore limit.")
        validated_members.append((member, relative_path.parts))

    with tempfile.TemporaryDirectory(prefix=".robustnotes-restore-", dir=root) as staging_name:
        staging = Path(staging_name)
        actual_total_size = 0
        for member, relative_parts in validated_members:
            staged_path = staging.joinpath(*relative_parts)
            if member.is_dir():
                staged_path.mkdir(parents=True, exist_ok=True)
                continue
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            member_size = 0
            with archive.open(member, "r") as source, staged_path.open("xb") as target_file:
                while chunk := source.read(1024 * 1024):
                    member_size += len(chunk)
                    actual_total_size += len(chunk)
                    if member_size > max_member_size or actual_total_size > max_total_size:
                        raise ValueError("The archive expands beyond the safe restore size limit.")
                    target_file.write(chunk)
            if member_size != member.file_size:
                raise ValueError("An archive entry's size does not match its metadata.")

        for staged_path in staging.rglob("*"):
            if staged_path.is_dir():
                continue
            relative_path = staged_path.relative_to(staging)
            target = (root / relative_path).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise ValueError("The archive contains an invalid file path.") from error
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, target)

from PySide6.QtCore import Qt, QEvent, QPointF, QRectF, QSize, QSizeF, QMarginsF, Signal, QTimer, QBuffer, QIODevice, QThread, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QMouseEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QImage,
    QFont,
    QIntValidator,
    QPdfWriter,
    QDesktopServices,
    QImageReader,
    QPageSize,
    QPageLayout,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFileDialog,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsTextItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QGridLayout,
    QInputDialog,
    QLineEdit,
    QLabel,
    QPlainTextEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QLayout,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def full_logo_label(width: int, height: int) -> QLabel:
    """Create a consistently scaled full RobustNotes logo for app headers."""
    label = QLabel()
    logo_path = Path(__file__).resolve().parent / "assets" / "robustnotes-logo.png"
    pixmap = QPixmap(str(logo_path))
    if not pixmap.isNull():
        label.setPixmap(
            pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    label.setFixedSize(width, height)
    label.setStyleSheet("background: transparent;")
    label.setAccessibleName("RobustNotes")
    return label


def robustnotes_icon() -> QIcon:
    """Return the square RobustNotes mark used by native window chrome."""
    root = Path(__file__).resolve().parent / "assets"
    icon_path = root / "robustnotes-icon.ico"
    if not icon_path.is_file():
        icon_path = root / "robustnotes-logo.png"
    return QIcon(str(icon_path))

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

    HAS_AUDIO_INPUT = True
except ImportError:
    QAudioFormat = None
    QAudioSource = None
    QMediaDevices = None
    HAS_AUDIO_INPUT = False

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    HAS_MEDIA_PLAYBACK = True
except ImportError:
    QAudioOutput = None
    QMediaPlayer = None
    HAS_MEDIA_PLAYBACK = False


def release_version_tuple(value: str) -> tuple[int, ...]:
    """Parse a stable numeric release version, tolerating a leading v."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


class UpdateCheckWorker(QThread):
    update_available = Signal(str, str, str)
    up_to_date = Signal()
    request_failed = Signal(str)

    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = current_version

    def run(self) -> None:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "RobustNotes-Updater"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                release = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                raise RuntimeError("GitHub did not return a stable published release.")
            tag = str(release.get("tag_name", ""))
            if release_version_tuple(tag) <= release_version_tuple(self.current_version):
                self.up_to_date.emit()
                return
            assets = release.get("assets", [])
            if not isinstance(assets, list):
                raise RuntimeError("The latest release has an invalid asset list.")
            expected_installer = f"robustnotes-setup-{tag.removeprefix('v').removeprefix('V')}.exe".casefold()
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name", ""))
                url = str(asset.get("browser_download_url", ""))
                if name.casefold() == expected_installer:
                    parsed = urlparse(url)
                    if parsed.scheme != "https" or parsed.hostname != "github.com":
                        raise RuntimeError("The release installer URL is not a trusted GitHub URL.")
                    digest = str(asset.get("digest", ""))
                    self.update_available.emit(tag, url, digest)
                    return
            raise RuntimeError(
                f"The latest release must include the matching installer asset {expected_installer}."
            )
        except Exception as error:
            self.request_failed.emit(f"Could not check for updates: {error}")


class UpdateDownloadWorker(QThread):
    installer_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(self, version: str, url: str, expected_digest: str):
        super().__init__()
        self.version = version
        self.url = url
        self.expected_digest = expected_digest

    def run(self) -> None:
        safe_version = re.sub(r"[^0-9A-Za-z.-]", "", self.version)
        request = urllib.request.Request(
            self.url,
            headers={"Accept": "application/octet-stream", "User-Agent": "RobustNotes-Updater"},
        )
        retryable_http_statuses = {408, 425, 429, 500, 502, 503, 504}
        last_error: Exception = RuntimeError("The installer download did not complete.")

        for attempt in range(4):
            temporary_path = ""
            retry_delay = min(2 ** attempt, 8)
            try:
                digest = hashlib.sha256()
                with urllib.request.urlopen(request, timeout=180) as response:
                    final_host = urlparse(response.geturl()).hostname or ""
                    if not (final_host == "github.com" or final_host.endswith(".githubusercontent.com")):
                        raise RuntimeError("The installer download redirected to an untrusted host.")
                    content_length = int(response.headers.get("Content-Length", "0") or 0)
                    if content_length > 1024**3:
                        raise RuntimeError("The installer file exceeds the 1 GB safety limit.")
                    with tempfile.NamedTemporaryFile(
                        prefix=f"RobustNotes-Setup-{safe_version}-", suffix=".exe", delete=False
                    ) as output:
                        temporary_path = output.name
                        first_bytes = b""
                        total = 0
                        while chunk := response.read(1024 * 1024):
                            total += len(chunk)
                            if total > 1024**3:
                                raise RuntimeError("The installer file exceeds the 1 GB safety limit.")
                            if not first_bytes:
                                first_bytes = chunk[:2]
                            digest.update(chunk)
                            output.write(chunk)
                if content_length and total != content_length:
                    raise ConnectionError(
                        f"The installer download was incomplete ({total} of {content_length} bytes)."
                    )
                if first_bytes != b"MZ":
                    raise RuntimeError("The downloaded file is not a Windows installer.")
                if self.expected_digest:
                    algorithm, separator, expected = self.expected_digest.partition(":")
                    if separator and algorithm.casefold() == "sha256" and digest.hexdigest().casefold() != expected.casefold():
                        raise RuntimeError("The installer checksum does not match the GitHub release metadata.")
                ready_path = temporary_path
                temporary_path = ""
                self.installer_ready.emit(ready_path)
                return
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in retryable_http_statuses or attempt == 3:
                    break
                retry_after = error.headers.get("Retry-After") if error.headers else None
                if retry_after:
                    try:
                        retry_delay = min(max(float(retry_after), 1.0), 30.0)
                    except ValueError:
                        pass
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                last_error = error
                if attempt == 3:
                    break
            except Exception as error:
                last_error = error
                break
            finally:
                if temporary_path:
                    try:
                        os.remove(temporary_path)
                    except OSError:
                        pass
            time.sleep(retry_delay)

        self.request_failed.emit(f"Could not download the update after several attempts: {last_error}")


class LocalAIChatWorker(QThread):
    answer_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(self, endpoint: str, model: str, history: list[dict[str, str]],
                 question: str, context: str, page_image: str):
        super().__init__()
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.history = history
        self.question = question
        self.context = context
        self.page_image = page_image

    def run(self) -> None:
        system_message = (
            "You are RobustNotes AI, a helpful notebook study assistant. "
            "Answer using the user's page image and extracted PDF text when relevant. "
            "Be clear about uncertainty and do not invent details absent from the notes. "
            "If the notes do not contain the answer, say so, then offer general help separately.\n\n"
            f"Imported PDF text and page metadata:\n{self.context[:20000]}"
        )
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_message}
        ]
        for message in self.history[-8:]:
            messages.append({"role": message["role"], "content": message["content"]})
        user_message: dict[str, object] = {"role": "user", "content": self.question}
        if self.page_image:
            user_message["images"] = [self.page_image]
        messages.append(user_message)
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
        }).encode("utf-8")
        parsed_endpoint = urlparse(self.endpoint)
        if parsed_endpoint.scheme != "http" or parsed_endpoint.hostname not in (
            "localhost", "127.0.0.1", "::1"
        ):
            self.request_failed.emit("RobustNotes AI only connects to a local Ollama server.")
            return
        request = urllib.request.Request(
            f"{self.endpoint}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            answer = str(result.get("message", {}).get("content", "")).strip()
            if not answer:
                raise RuntimeError("The AI returned an empty response.")
            self.answer_ready.emit(answer)
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8")
                message = json.loads(detail).get("error", {}).get("message", detail)
            except Exception:
                message = str(error)
            self.request_failed.emit(f"AI request failed: {message}")
        except Exception as error:
            self.request_failed.emit(f"Could not reach RobustNotes AI: {error}")


class LocalTranscriptionWorker(QThread):
    transcript_ready = Signal(str)
    request_failed = Signal(str)

    def __init__(self, audio_wav: bytes):
        super().__init__()
        self.audio_wav = audio_wav

    def run(self) -> None:
        temporary_path = ""
        try:
            from faster_whisper import WhisperModel
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
                audio_file.write(self.audio_wav)
                temporary_path = audio_file.name
            model_name = os.environ.get("ROBUSTNOTES_WHISPER_MODEL", "base")
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
            segments, _ = model.transcribe(temporary_path)
            transcript = " ".join(segment.text.strip() for segment in segments).strip()
            if not transcript:
                raise RuntimeError("No speech was recognized.")
            self.transcript_ready.emit(transcript)
        except Exception as error:
            self.request_failed.emit(
                f"Local voice transcription is unavailable: {error}. Install faster-whisper and its model to enable it."
            )
        finally:
            if temporary_path:
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass


_TROCR_PROCESSOR: Any = None
_TROCR_MODEL: Any = None
_TROCR_LOCK = threading.Lock()


def normalize_ocr_text(text: str) -> str:
    """Tidy OCR spacing while preserving words, punctuation, and paragraph breaks."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in normalized.split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"([,;:])(?=\S)", r"\1 ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def extract_handwriting_line_crops(image_png: bytes):
    """Split a page image into top-to-bottom ink bands without fragmenting cursive words."""
    import cv2
    import numpy as np
    from PIL import Image

    raw_image = cv2.imdecode(np.frombuffer(image_png, dtype=np.uint8), cv2.IMREAD_COLOR)
    if raw_image is None:
        raise RuntimeError("The current page could not be prepared for handwriting recognition.")
    gray = cv2.cvtColor(raw_image, cv2.COLOR_BGR2GRAY)
    threshold_mode = cv2.THRESH_BINARY if float(np.mean(gray)) < 128 else cv2.THRESH_BINARY_INV
    binary = cv2.threshold(gray, 0, 255, threshold_mode | cv2.THRESH_OTSU)[1]
    component_count, component_labels, component_stats, _ = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    valid_components = [
        component_id
        for component_id in range(1, component_count)
        if 5 <= int(component_stats[component_id, cv2.CC_STAT_AREA]) <= raw_image.shape[0] * raw_image.shape[1] * 0.25
        and int(component_stats[component_id, cv2.CC_STAT_WIDTH]) >= 2
        and int(component_stats[component_id, cv2.CC_STAT_HEIGHT]) >= 2
    ]
    binary = np.where(np.isin(component_labels, valid_components), 255, 0).astype(np.uint8)
    ink_rows = np.count_nonzero(binary, axis=1)
    active_rows = np.flatnonzero(ink_rows >= max(1, raw_image.shape[1] // 2500))
    if active_rows.size == 0:
        raise RuntimeError("No handwriting was detected on this page.")

    height, width = gray.shape
    max_row_gap = max(10, min(26, round(height * 0.018)))
    bands: list[tuple[int, int]] = []
    top = previous = int(active_rows[0])
    for row in active_rows[1:]:
        row = int(row)
        if row - previous > max_row_gap:
            bands.append((top, previous + 1))
            top = row
        previous = row
    bands.append((top, previous + 1))

    crops = []
    for top, bottom in bands:
        if bottom - top < 8 or int(ink_rows[top:bottom].sum()) < 16:
            continue
        ys, xs = np.nonzero(binary[top:bottom])
        if xs.size == 0:
            continue
        left = max(0, int(xs.min()) - 18)
        right = min(width, int(xs.max()) + 19)
        crop_top = max(0, top - 16)
        crop_bottom = min(height, bottom + 16)
        if right - left >= 12 and crop_bottom - crop_top >= 8:
            crops.append(
                Image.fromarray(cv2.cvtColor(raw_image[crop_top:crop_bottom, left:right], cv2.COLOR_BGR2RGB))
            )
    if not crops:
        raise RuntimeError("No readable handwriting lines were found.")
    return crops


class HandwritingOCRWorker(QThread):
    """Detect handwriting lines and transcribe them with the local TrOCR model."""

    progress = Signal(str)
    recognized = Signal(str)
    failed = Signal(str)

    def __init__(self, image_png: bytes):
        super().__init__()
        self.image_png = image_png

    def run(self) -> None:
        global _TROCR_PROCESSOR, _TROCR_MODEL
        try:
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            local_app_data = Path(
                QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
            )
            os.environ.setdefault("HF_HOME", str(local_app_data / "Models" / "huggingface"))
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
            crops = extract_handwriting_line_crops(self.image_png)

            self.progress.emit("Loading local handwriting model (first run may download it)…")
            model_name = "microsoft/trocr-base-handwritten"
            recognized_lines: list[str] = []
            with _TROCR_LOCK:
                if _TROCR_PROCESSOR is None or _TROCR_MODEL is None:
                    _TROCR_PROCESSOR = TrOCRProcessor.from_pretrained(model_name)
                    _TROCR_MODEL = VisionEncoderDecoderModel.from_pretrained(model_name)
                    _TROCR_MODEL.eval()
                device = "cuda" if torch.cuda.is_available() else "cpu"
                _TROCR_MODEL.to(device)
                for offset in range(0, len(crops), 4):
                    self.progress.emit(f"Recognizing handwriting · {min(offset + 1, len(crops))} of {len(crops)} lines…")
                    inputs = _TROCR_PROCESSOR(images=crops[offset:offset + 4], return_tensors="pt").pixel_values.to(device)
                    with torch.inference_mode():
                        output = _TROCR_MODEL.generate(inputs, max_new_tokens=128)
                    recognized_lines.extend(_TROCR_PROCESSOR.batch_decode(output, skip_special_tokens=True))
            result = normalize_ocr_text("\n".join(recognized_lines))
            if not result:
                raise RuntimeError("The model could not read the handwriting on this page.")
            self.recognized.emit(result)
        except ImportError as error:
            self.failed.emit(
                "Handwriting recognition needs the optional packages in ocr-requirements.txt. "
                f"Install them, then retry. ({error})"
            )
        except Exception as error:
            self.failed.emit(f"Handwriting recognition failed: {error}")


class FunkwhaleMusicRequestWorker(QThread):
    """Search and stream tracks from a user-configured Funkwhale pod."""

    completed = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, operation: str, server_url: str, username: str, password: str,
                 query: str = "", track_id: str = ""):
        super().__init__()
        self.operation = operation
        self.server_url = server_url.rstrip("/")
        self.username = username
        self.password = password
        self.query = query
        self.track_id = track_id

    def _request_url(self, endpoint: str, params: dict[str, str]) -> str:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        auth = {
            "u": self.username, "t": token, "s": salt,
            "v": "1.16.1", "c": "RobustNotes", "f": "json",
        }
        return f"{self.server_url}/rest/{endpoint}.view?{urlencode(auth | params)}"

    def _api_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._request_url(endpoint, params),
            headers={"User-Agent": "RobustNotes/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=18) as response:
            payload = json.loads(response.read(2_000_001).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("The Funkwhale server returned an unexpected response.")
        envelope = payload.get("subsonic-response")
        if not isinstance(envelope, dict):
            raise RuntimeError("The server did not return a Subsonic response. Check the pod URL.")
        if envelope.get("status") != "ok":
            detail = envelope.get("error")
            message = detail.get("message") if isinstance(detail, dict) else None
            raise RuntimeError(str(message or "The server rejected the request."))
        return envelope

    def run(self) -> None:
        parsed_server = urlparse(self.server_url)
        if (parsed_server.scheme not in {"https", "http"} or not parsed_server.hostname
                or (parsed_server.scheme != "https" and parsed_server.hostname not in {"localhost", "127.0.0.1", "::1"})):
            self.failed.emit(self.operation, "Enter a valid HTTPS Funkwhale pod address (HTTP is allowed for localhost).")
            return
        if not self.username.strip() or not self.password:
            self.failed.emit(self.operation, "Configure your Funkwhale username and Subsonic API password first.")
            return
        if self.operation == "search":
            if not self.query.strip():
                self.failed.emit(self.operation, "Enter a song, artist, or album to search for.")
                return
            params = {"query": self.query.strip(), "songCount": "50", "songOffset": "0",
                      "albumCount": "0", "artistCount": "0"}
        elif self.operation == "stream":
            if not self.track_id.strip():
                self.failed.emit(self.operation, "Select a track to play.")
                return
            params = {"id": self.track_id}
        else:
            params = {}
        try:
            if self.operation == "ping":
                self._api_json("ping", {})
                self.completed.emit(self.operation, {})
            elif self.operation == "search":
                envelope = self._api_json("search3", params)
                result = envelope.get("searchResult3", {})
                songs = result.get("song", []) if isinstance(result, dict) else []
                if isinstance(songs, dict):
                    songs = [songs]
                tracks = []
                for song in songs if isinstance(songs, list) else []:
                    if isinstance(song, dict) and song.get("id"):
                        tracks.append({
                            "id": str(song["id"]), "name": str(song.get("title") or "Untitled"),
                            "artist": str(song.get("artist") or "Unknown artist"),
                            "album": str(song.get("album") or ""),
                        })
                self.completed.emit(self.operation, tracks)
            else:
                self._api_json("getSong", params)
                self.completed.emit(self.operation, {"url": self._request_url("stream", params)})
        except Exception as error:
            self.failed.emit(self.operation, str(error))


class FunkwhaleMusicPlayerDialog(QDialog):
    """Study player for local audio and a user-owned or community Funkwhale pod."""

    def __init__(self, parent, settings: QSettings):
        super().__init__(parent)
        self.settings = settings
        self.worker: Optional[FunkwhaleMusicRequestWorker] = None
        self.pending_track: Optional[dict[str, Any]] = None
        self.queue: list[dict[str, Any]] = []
        self.current_index = -1
        self._seeking = False
        self._shuffle = False
        self._repeat = False
        self.focus_remaining_seconds = 0
        self.server_url = str(settings.value("funkwhaleServer", "https://demo.funkwhale.audio"))
        self.username = str(settings.value("funkwhaleUsername", ""))
        # Keep the Subsonic API password in memory only; it is not written to QSettings.
        self.password = ""
        self.setWindowTitle("RobustNotes · Study Music")
        self.setMinimumSize(640, 620)
        self.resize(760, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Study Music")
        title.setObjectName("Brand")
        header.addWidget(title)
        header.addStretch(1)
        self.connection_status = QLabel("Ready to search")
        self.connection_status.setObjectName("Muted")
        header.addWidget(self.connection_status)
        self.source_combo = QComboBox()
        self.source_combo.addItem("My music files", "local")
        self.source_combo.addItem("Funkwhale library", "funkwhale")
        self.source_combo.currentIndexChanged.connect(self.change_source)
        header.addWidget(self.source_combo)
        self.configure_button = QPushButton("Connect…")
        self.configure_button.clicked.connect(self.configure_connection)
        header.addWidget(self.configure_button)
        root.addLayout(header)

        intro = QLabel("A quiet soundtrack for focused work. Close this window to get your page back—music keeps playing in the background.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.server_label = QLabel(self.server_url)
        self.server_label.setObjectName("Muted")
        self.server_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.server_label)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search your connected library…")
        self.search_input.returnPressed.connect(self.search_library)
        self.search_button = QPushButton("Search")
        self.search_button.setObjectName("PrimaryButton")
        self.search_button.clicked.connect(self.search_library)
        self.add_local_button = QPushButton("Add files…")
        self.add_local_button.setToolTip("Add audio files from this device to the queue")
        self.add_local_button.clicked.connect(self.add_local_files)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.add_local_button)
        root.addLayout(search_row)

        playlist_row = QHBoxLayout()
        playlist_label = QLabel("QUEUE")
        playlist_label.setObjectName("SmallSectionLabel")
        playlist_row.addWidget(playlist_label)
        playlist_row.addStretch(1)
        self.save_playlist_button = QPushButton("Save playlist")
        self.save_playlist_button.clicked.connect(self.save_playlist)
        playlist_row.addWidget(self.save_playlist_button)
        self.load_playlist_button = QPushButton("My playlists")
        self.load_playlist_button.clicked.connect(self.load_playlist)
        playlist_row.addWidget(self.load_playlist_button)
        root.addLayout(playlist_row)

        self.song_list = QListWidget()
        self.song_list.setObjectName("MusicQueue")
        self.song_list.itemActivated.connect(self.play_selected)
        self.song_list.setToolTip("Double-click or press Enter to play a track")
        root.addWidget(self.song_list, 1)

        player_card = QFrame()
        player_card.setObjectName("Card")
        player_layout = QVBoxLayout(player_card)
        player_layout.setContentsMargins(16, 14, 16, 12)
        player_layout.setSpacing(9)
        current_row = QHBoxLayout()
        self.cover_art = QLabel("♪")
        self.cover_art.setFixedSize(54, 54)
        self.cover_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_art.setStyleSheet("background:#203b45; color:#5fe0eb; border-radius:12px; font-size:25px; font-weight:700;")
        current_row.addWidget(self.cover_art)
        current_labels = QVBoxLayout()
        self.now_playing = QLabel("Choose something to listen to")
        self.now_playing.setStyleSheet("font-size:15px; font-weight:700;")
        self.now_playing.setWordWrap(True)
        self.now_artist = QLabel("Your music stays yours")
        self.now_artist.setObjectName("Muted")
        current_labels.addWidget(self.now_playing)
        current_labels.addWidget(self.now_artist)
        current_row.addLayout(current_labels, 1)
        player_layout.addLayout(current_row)

        seek_row = QHBoxLayout()
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setObjectName("Muted")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.position_slider.sliderReleased.connect(self.seek_to_position)
        self.duration_label = QLabel("0:00")
        self.duration_label.setObjectName("Muted")
        seek_row.addWidget(self.elapsed_label)
        seek_row.addWidget(self.position_slider, 1)
        seek_row.addWidget(self.duration_label)
        player_layout.addLayout(seek_row)

        transport = QHBoxLayout()
        self.shuffle_button = QPushButton("⤨")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setToolTip("Shuffle the queue")
        self.shuffle_button.setStyleSheet("QPushButton:checked { background:#203d49; border-color:#48DDF0; color:#5fe0eb; }")
        self.shuffle_button.toggled.connect(lambda checked: setattr(self, "_shuffle", checked))
        transport.addWidget(self.shuffle_button)
        self.previous_button = QPushButton("|◀")
        self.previous_button.setToolTip("Previous track")
        self.previous_button.clicked.connect(self.play_previous)
        transport.addWidget(self.previous_button)
        self.play_pause_button = QPushButton("▶")
        self.play_pause_button.setObjectName("PrimaryButton")
        self.play_pause_button.setFixedSize(48, 42)
        self.play_pause_button.setToolTip("Play or pause")
        self.play_pause_button.clicked.connect(self.toggle_playback)
        transport.addWidget(self.play_pause_button)
        self.next_button = QPushButton("▶|")
        self.next_button.setToolTip("Next track")
        self.next_button.clicked.connect(self.play_next)
        transport.addWidget(self.next_button)
        self.repeat_button = QPushButton("↻")
        self.repeat_button.setCheckable(True)
        self.repeat_button.setToolTip("Repeat the current track")
        self.repeat_button.setStyleSheet("QPushButton:checked { background:#203d49; border-color:#48DDF0; color:#5fe0eb; }")
        self.repeat_button.toggled.connect(lambda checked: setattr(self, "_repeat", checked))
        transport.addWidget(self.repeat_button)
        transport.addStretch(1)
        volume_label = QLabel("Volume")
        volume_label.setObjectName("Muted")
        transport.addWidget(volume_label)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(105)
        raw_saved_volume = settings.value("musicVolume", 0.45)
        try:
            if isinstance(raw_saved_volume, bool) or not isinstance(raw_saved_volume, (int, float, str)):
                raise TypeError("Saved volume is not numeric")
            saved_volume = float(raw_saved_volume)
            if not math.isfinite(saved_volume):
                raise ValueError("Saved volume is not finite")
        except (TypeError, ValueError, OverflowError):
            saved_volume = 0.45
        self.volume_slider.setValue(round(max(0.0, min(1.0, saved_volume)) * 100))
        transport.addWidget(self.volume_slider)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop playback")
        self.stop_button.clicked.connect(self.stop_playback)
        transport.addWidget(self.stop_button)
        player_layout.addLayout(transport)
        root.addWidget(player_card)

        focus_row = QHBoxLayout()
        focus_label = QLabel("Focus session")
        focus_label.setObjectName("Muted")
        focus_row.addWidget(focus_label)
        self.focus_duration = QComboBox()
        self.focus_duration.addItem("25 minutes", 25)
        self.focus_duration.addItem("50 minutes", 50)
        self.focus_duration.addItem("90 minutes", 90)
        focus_row.addWidget(self.focus_duration)
        self.focus_button = QPushButton("Start focus")
        self.focus_button.setToolTip("Start a study timer; playback pauses when it ends")
        self.focus_button.clicked.connect(self.toggle_focus_session)
        focus_row.addWidget(self.focus_button)
        self.focus_status = QLabel("Music and timer are optional")
        self.focus_status.setObjectName("Muted")
        focus_row.addWidget(self.focus_status, 1)
        root.addLayout(focus_row)

        self.player: Any = None
        self.audio_output: Any = None
        if HAS_MEDIA_PLAYBACK and QMediaPlayer is not None and QAudioOutput is not None:
            audio_output = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(audio_output)
            player.errorOccurred.connect(self.on_player_error)
            player.playbackStateChanged.connect(self.update_play_button)
            player.positionChanged.connect(self.update_position)
            player.durationChanged.connect(self.update_duration)
            player.mediaStatusChanged.connect(self.on_media_status)
            self.audio_output = audio_output
            self.player = player
            self.volume_slider.valueChanged.connect(self.set_volume)
            self.set_volume(self.volume_slider.value())
        else:
            self.connection_status.setText("QtMultimedia playback is unavailable")
            self.play_pause_button.setEnabled(False)
            self.add_local_button.setEnabled(False)
        self.focus_timer = QTimer(self)
        self.focus_timer.setInterval(1000)
        self.focus_timer.timeout.connect(self.tick_focus_session)
        self.setStyleSheet(WORKSPACE_STYLE)
        self.change_source()

    def change_source(self, *_args):
        is_local = self.source_combo.currentData() == "local"
        self.search_input.setEnabled(not is_local)
        self.search_button.setVisible(not is_local)
        self.add_local_button.setVisible(is_local)
        self.configure_button.setVisible(not is_local)
        self.server_label.setVisible(not is_local)
        self.search_input.setPlaceholderText(
            "Add audio files from your device to build a queue…"
            if is_local else "Search songs, artists, or albums on your pod…"
        )
        if is_local:
            self.connection_status.setText("Local library · files stay on this device")
        elif self.password:
            self.connection_status.setText("Connected to Funkwhale")
        else:
            self.connection_status.setText("Connect your music library")

    def add_local_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add music to queue",
            str(Path.home()),
            "Audio files (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma);;All files (*.*)",
        )
        if not paths:
            return
        existing = {track.get("path") for track in self.queue if track.get("source") == "local"}
        for raw_path in paths:
            path = Path(raw_path)
            if str(path) in existing:
                continue
            self.queue.append({
                "id": str(path),
                "path": str(path),
                "source": "local",
                "name": path.stem,
                "artist": "On this device",
                "album": path.parent.name,
            })
        self.source_combo.setCurrentIndex(self.source_combo.findData("local"))
        self.render_queue()
        if self.queue:
            self.connection_status.setText(f"{len(self.queue)} track(s) in your local queue")

    def render_queue(self):
        self.song_list.clear()
        for index, track in enumerate(self.queue):
            title = str(track.get("name", "Untitled"))
            artist = str(track.get("artist", "Unknown artist"))
            album = str(track.get("album", ""))
            source_label = "On this device" if track.get("source") == "local" else "Funkwhale"
            detail = "  ·  ".join(value for value in (artist, album, source_label) if value)
            item = QListWidgetItem(f"{title}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.song_list.addItem(item)
        if 0 <= self.current_index < self.song_list.count():
            self.song_list.setCurrentRow(self.current_index)

    def saved_playlists(self) -> dict[str, list[dict[str, Any]]]:
        raw_value = self.settings.value("studyPlaylists", "{}")
        try:
            payload = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(name): [track for track in tracks if isinstance(track, dict)]
            for name, tracks in payload.items()
            if isinstance(tracks, list)
        }

    def save_playlist(self):
        if not self.queue:
            self.connection_status.setText("Add some tracks before saving a playlist.")
            return
        name, accepted = QInputDialog.getText(
            self, "Save playlist", "Playlist name", QLineEdit.EchoMode.Normal, "Study playlist"
        )
        name = " ".join(name.split())
        if not accepted or not name:
            return
        playlists = self.saved_playlists()
        if name in playlists:
            answer = QMessageBox.question(
                self,
                "Replace playlist?",
                f"A playlist named ‘{name}’ already exists. Replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        playlists[name] = [
            {key: value for key, value in track.items()
             if key in {"id", "path", "source", "name", "artist", "album"}}
            for track in self.queue
        ]
        self.settings.setValue("studyPlaylists", json.dumps(playlists, ensure_ascii=False))
        self.connection_status.setText(f"Saved ‘{name}’ on this device")

    def load_playlist(self):
        playlists = self.saved_playlists()
        if not playlists:
            self.connection_status.setText("No saved playlists yet. Build a queue and choose Save playlist.")
            return
        names = sorted(playlists, key=str.casefold)
        name, accepted = QInputDialog.getItem(self, "My playlists", "Choose a playlist", names, 0, False)
        if not accepted or name not in playlists:
            return
        tracks = playlists[name]
        missing_local = sum(
            1 for track in tracks
            if track.get("source") == "local" and not Path(str(track.get("path", ""))).is_file()
        )
        self.queue = tracks
        self.current_index = -1
        self.render_queue()
        if self.queue and all(track.get("source") == "local" for track in self.queue):
            self.source_combo.setCurrentIndex(self.source_combo.findData("local"))
        elif self.queue:
            self.source_combo.setCurrentIndex(self.source_combo.findData("funkwhale"))
        if missing_local:
            self.connection_status.setText(
                f"Loaded ‘{name}’; {missing_local} local file(s) moved or missing."
            )
        else:
            self.connection_status.setText(f"Loaded ‘{name}’ · {len(self.queue)} track(s)")

    def play_track(self, index: int):
        if self.player is None or not self.queue:
            self.connection_status.setText("Audio playback is unavailable")
            return
        if not 0 <= index < len(self.queue):
            return
        if self.worker is not None and self.worker.isRunning():
            self.connection_status.setText("Finishing the previous library request… try again in a moment.")
            return
        self.current_index = index
        track = self.queue[index]
        self.pending_track = track
        self.render_queue()
        self.now_playing.setText(str(track.get("name", "Untitled")))
        self.now_artist.setText(str(track.get("artist", "Unknown artist")))
        self.position_slider.setRange(0, 0)
        self.elapsed_label.setText("0:00")
        self.duration_label.setText("0:00")
        if track.get("source") == "local":
            local_path = Path(str(track.get("path", "")))
            if not local_path.is_file():
                self.connection_status.setText("That local music file is no longer available.")
                return
            self.player.setSource(QUrl.fromLocalFile(str(local_path)))
            self.player.play()
            self.connection_status.setText("Playing from this device")
            return
        self.connection_status.setText("Connecting to your pod…")
        self.request("stream", track=track)

    def play_next(self):
        if not self.queue:
            return
        if self._shuffle and len(self.queue) > 1:
            choices = [index for index in range(len(self.queue)) if index != self.current_index]
            self.play_track(random.choice(choices))
            return
        next_index = self.current_index + 1
        if next_index < len(self.queue):
            self.play_track(max(0, next_index))
        else:
            self.stop_playback()
            self.connection_status.setText("Queue finished")

    def play_previous(self):
        if not self.queue:
            return
        if self.current_index < 0:
            self.play_track(0)
            return
        player = self.player
        if player is not None and player.position() > 3000:
            player.setPosition(0)
            return
        self.play_track((self.current_index - 1) % len(self.queue))

    @staticmethod
    def format_time(milliseconds: int) -> str:
        total_seconds = max(0, milliseconds // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    def update_position(self, position: int):
        if not self._seeking:
            self.position_slider.setValue(position)
        self.elapsed_label.setText(self.format_time(position))

    def update_duration(self, duration: int):
        self.position_slider.setRange(0, max(0, duration))
        self.duration_label.setText(self.format_time(duration))

    def seek_to_position(self):
        self._seeking = False
        if self.player is not None:
            self.player.setPosition(self.position_slider.value())

    def set_volume(self, value: int):
        if self.audio_output is not None:
            self.audio_output.setVolume(max(0, min(100, value)) / 100.0)
        self.settings.setValue("musicVolume", max(0, min(100, value)) / 100.0)

    def toggle_focus_session(self):
        if self.focus_timer.isActive():
            self.focus_timer.stop()
            self.focus_remaining_seconds = 0
            self.focus_button.setText("Start focus")
            self.focus_status.setText("Focus timer paused")
            return
        minutes = int(self.focus_duration.currentData() or 25)
        self.focus_remaining_seconds = minutes * 60
        self.focus_timer.start()
        self.focus_button.setText("End session")
        self.tick_focus_session()

    def tick_focus_session(self):
        if self.focus_remaining_seconds <= 0:
            self.focus_timer.stop()
            self.stop_playback()
            self.focus_button.setText("Start focus")
            self.focus_status.setText("Session complete · take a short break")
            self.connection_status.setText("Focus session complete")
            return
        minutes, seconds = divmod(self.focus_remaining_seconds, 60)
        self.focus_status.setText(f"Focus time · {minutes:02d}:{seconds:02d} remaining")
        self.focus_remaining_seconds -= 1

    def on_media_status(self, status):
        if QMediaPlayer is not None and status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._repeat and self.current_index >= 0:
                self.play_track(self.current_index)
            else:
                self.play_next()

    def configure_connection(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Connect to Funkwhale")
        form = QVBoxLayout(dialog)
        form.addWidget(QLabel("Pod address (for example, https://demo.funkwhale.audio)"))
        server_input = QLineEdit(self.server_url)
        server_input.setPlaceholderText("https://your-funkwhale-pod.example")
        form.addWidget(server_input)
        form.addWidget(QLabel("Funkwhale username"))
        username_input = QLineEdit(self.username)
        form.addWidget(username_input)
        form.addWidget(QLabel("Subsonic API password (request one in your Funkwhale account settings)"))
        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.EchoMode.Password)
        password_input.setPlaceholderText("Only held in memory for this session")
        form.addWidget(password_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addWidget(buttons)
        dialog.setMinimumWidth(460)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.server_url = server_input.text().strip().rstrip("/")
        self.username = username_input.text().strip()
        self.password = password_input.text()
        self.settings.setValue("funkwhaleServer", self.server_url)
        self.settings.setValue("funkwhaleUsername", self.username)
        self.server_label.setText(self.server_url or "No Funkwhale pod configured")
        self.connection_status.setText("Checking Funkwhale connection…")
        self.source_combo.setCurrentIndex(self.source_combo.findData("funkwhale"))
        self.request("ping")

    def request(self, operation: str, query: str = "", track: Optional[dict[str, Any]] = None):
        if self.worker is not None and self.worker.isRunning():
            return
        track_id = str((track or {}).get("id", ""))
        if not self.password:
            self.connection_status.setText("Connect to Funkwhale first using the Connect button.")
            return
        self.search_button.setEnabled(False)
        self.worker = FunkwhaleMusicRequestWorker(
            operation, self.server_url, self.username, self.password, query, track_id,
        )
        self.worker.completed.connect(self.on_request_complete)
        self.worker.failed.connect(self.on_request_failed)
        self.worker.finished.connect(self.release_worker)
        retain_background_worker(self.worker)
        self.worker.start()

    def search_library(self):
        if self.source_combo.currentData() == "local":
            self.connection_status.setText("Use Add files to build a local queue.")
            return
        query = self.search_input.text().strip()
        if query:
            self.connection_status.setText("Searching Funkwhale…")
            self.request("search", query)

    def on_request_complete(self, operation: str, result: object):
        if operation == "ping":
            self.connection_status.setText("Connected to Funkwhale")
            return
        if operation == "stream":
            stream_url = str(result.get("url", "")) if isinstance(result, dict) else ""
            if self.player is None:
                self.connection_status.setText("QtMultimedia playback is unavailable")
                return
            self.player.setSource(QUrl(stream_url))
            self.player.play()
            self.connection_status.setText("Starting playback…")
            return

        tracks = result if isinstance(result, list) else []
        self.queue = [
            track | {"source": "funkwhale"}
            for track in tracks if isinstance(track, dict) and track.get("id")
        ]
        self.current_index = -1
        self.render_queue()
        self.connection_status.setText(f"{len(self.queue)} track(s) found on this pod")

    def on_request_failed(self, operation: str, message: str):
        self.connection_status.setText(f"Funkwhale {operation} failed: {message}")

    def release_worker(self):
        self.search_button.setEnabled(True)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def play_selected(self, item=None):
        item = item or self.song_list.currentItem()
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int):
            self.play_track(index)

    def toggle_playback(self):
        player = self.player
        if player is None or QMediaPlayer is None:
            return
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()

    def stop_playback(self):
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
            self.now_playing.setText("Playback stopped")
            self.now_artist.setText("Choose another track whenever you like")
            self.position_slider.setRange(0, 0)
            self.elapsed_label.setText("0:00")
            self.duration_label.setText("0:00")
        self.pending_track = None

    def end_session(self):
        self.focus_timer.stop()
        self.focus_remaining_seconds = 0
        self.stop_playback()
        self.password = ""
        self.hide()

    def update_play_button(self, state):
        if QMediaPlayer is not None and state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_pause_button.setText("Ⅱ")
        else:
            self.play_pause_button.setText("▶")

    def on_player_error(self, _error, message: str):
        if message:
            self.connection_status.setText(f"Playback error: {message}")

try:
    from PySide6.QtPdf import QPdfDocument

    HAS_PDF_SUPPORT = True
except ImportError:
    QPdfDocument = None
    HAS_PDF_SUPPORT = False


# ---------------------------------------------------------------------------
# Theme definitions
# ---------------------------------------------------------------------------

class Theme(TypedDict):
    canvas: str
    surface: str
    surface_alt: str
    border: str
    accent: str
    text: str
    muted: str
    swatches: list[str]


THEMES: dict[str, Theme] = {
    "Cyber Cyan": {
        "canvas": "#17191F",
        "surface": "#20232B",
        "surface_alt": "#292D37",
        "border": "#363B48",
        "accent": "#48DDF0",
        "text": "#F2F4F8",
        "muted": "#A3AAB8",
        "swatches": ["#48DDF0", "#FF657A", "#62D6A5", "#FFD166", "#B69CFF", "#FFFFFF"],
    },
    "Emerald Studio": {
        "canvas": "#111A17",
        "surface": "#19251F",
        "surface_alt": "#24342C",
        "border": "#34483D",
        "accent": "#48C995",
        "text": "#EFFAF4",
        "muted": "#A8BDB0",
        "swatches": ["#48C995", "#5CB9F2", "#F36B83", "#F6C85F", "#B69CFF", "#FFFFFF"],
    },
    "Sunset Warm": {
        "canvas": "#1C171A",
        "surface": "#282024",
        "surface_alt": "#382B31",
        "border": "#4A3941",
        "accent": "#FF9966",
        "text": "#FFF5EF",
        "muted": "#C3AEB5",
        "swatches": ["#FF9966", "#F36FA5", "#EAC45B", "#55C8D4", "#A78BFA", "#FFFFFF"],
    },
    "Monolith Dark": {
        "canvas": "#101010",
        "surface": "#191919",
        "surface_alt": "#252525",
        "border": "#383838",
        "accent": "#F5F5F5",
        "text": "#FAFAFA",
        "muted": "#A5A5A5",
        "swatches": ["#FFFFFF", "#C8C8C8", "#999999", "#707070", "#E5E5E5", "#F5F5F5"],
    },
    "High Contrast": {
        "canvas": "#000000",
        "surface": "#090909",
        "surface_alt": "#171717",
        "border": "#FFFFFF",
        "accent": "#FFFF00",
        "text": "#FFFFFF",
        "muted": "#E0E0E0",
        "swatches": ["#FFFF00", "#00FFFF", "#FF66FF", "#FFFFFF", "#FF6B6B", "#70FF9B"],
    },
}


@dataclass
class HistoryEntry:
    """One reversible scene operation."""

    action: str
    items: list[QGraphicsItem]
    before: Optional[list[QPointF]] = None
    after: Optional[list[QPointF]] = None
    before_scales: Optional[list[float]] = None
    after_scales: Optional[list[float]] = None


class CanvasTextItem(QGraphicsTextItem):
    """Selectable, movable page text that becomes editable on double click."""

    content_changed = Signal()

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.document().contentsChanged.connect(self.content_changed.emit)

    def set_foreground_color(self, color: QColor) -> None:
        """Apply a color to the selection, or to all text when none is selected."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.Document)
        if cursor.hasSelection():
            char_format = QTextCharFormat()
            char_format.setForeground(color)
            cursor.mergeCharFormat(char_format)
            self.setTextCursor(cursor)
        self.setDefaultTextColor(color)
        self.content_changed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        scene = self.scene()
        before_text = self.toPlainText()
        before_cursor = self.textCursor()
        super().keyPressEvent(event)
        if not isinstance(scene, PageScene) or self.toPlainText() == before_text:
            return
        if self.sceneBoundingRect().bottom() > scene.page_rect.bottom() - 14:
            self.document().undo()
            self.setTextCursor(before_cursor)
            owner = scene.parent()
            if isinstance(owner, NotebookWindow):
                owner.statusBar().showMessage("Text must stay within the page. Move the box up or make the text shorter.", 3500)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.content_changed.emit()


class ResizablePixmapItem(QGraphicsPixmapItem):
    """A movable image with an aspect-preserving bottom-right resize handle."""

    HANDLE_SIZE = 18.0

    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setAcceptHoverEvents(True)
        self._resizing = False
        self._resize_start_scale = 1.0
        self._resize_start_diagonal = QPointF()

    def _handle_rect(self) -> QRectF:
        bounds = self.boundingRect()
        return QRectF(
            bounds.right() - self.HANDLE_SIZE,
            bounds.bottom() - self.HANDLE_SIZE,
            self.HANDLE_SIZE,
            self.HANDLE_SIZE,
        )

    def paint(self, painter: QPainter, option, widget: Optional[QWidget] = None) -> None:
        super().paint(painter, option, cast(QWidget, widget))
        if self.isSelected():
            bounds = self.boundingRect().adjusted(1, 1, -1, -1)
            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#38D7E8"), 2.0, Qt.PenStyle.DashLine))
            painter.drawRect(bounds)
            painter.setPen(QPen(QColor("#10202C"), 1.5))
            painter.setBrush(QColor("#43D8E7"))
            painter.drawRect(self._handle_rect())
            painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.isSelected() and event.button() == Qt.MouseButton.LeftButton and self._handle_rect().contains(event.pos()):
            self._resizing = True
            self._resize_start_scale = self.scale()
            self._resize_start_diagonal = QPointF(
                self.pixmap().width() * self._resize_start_scale,
                self.pixmap().height() * self._resize_start_scale,
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._resizing:
            origin = self.mapToScene(QPointF(0, 0))
            delta = event.scenePos() - origin
            start = self._resize_start_diagonal
            denominator = start.x() * start.x() + start.y() * start.y()
            if denominator > 0:
                projection = (delta.x() * start.x() + delta.y() * start.y()) / denominator
                width = max(1, self.pixmap().width())
                height = max(1, self.pixmap().height())
                min_scale = 40.0 / max(width, height)
                max_scale = 10000.0 / max(width, height)
                page_scene = self.scene()
                if isinstance(page_scene, PageScene):
                    page = page_scene.page_rect
                    max_scale = min(
                        max_scale,
                        max(1.0, page.right() - self.pos().x()) / width,
                        max(1.0, page.bottom() - self.pos().y()) / height,
                    )
                min_scale = min(min_scale, max_scale)
                self.setScale(max(min_scale, min(max_scale, self._resize_start_scale * projection)))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._resizing:
            self._resizing = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.isSelected() and self._handle_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.isSelected() else Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.unsetCursor()
        super().hoverLeaveEvent(event)


class PageScene(QGraphicsScene):
    """A page-sized scene with a locked background and local history."""

    HISTORY_LIMIT = 100

    def __init__(self, template_pixmap: QPixmap, parent: Optional[QWidget] = None,
                 source_text: str = "", source_name: str = ""):
        super().__init__(parent)
        self.template_pixmap = template_pixmap
        self.source_text = source_text
        self.source_name = source_name
        self.ocr_text = ""
        self.page_rect = QRectF(0, 0, template_pixmap.width(), template_pixmap.height())
        self.setSceneRect(self.page_rect)

        self.bg_item = QGraphicsPixmapItem(template_pixmap)
        self.bg_item.setZValue(-100)
        self.bg_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.bg_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.bg_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.addItem(self.bg_item)

        self.undo_stack: list[HistoryEntry] = []
        self.redo_stack: list[HistoryEntry] = []

    def push_action(
        self,
        action: str,
        items: Sequence[QGraphicsItem],
        before: Optional[list[QPointF]] = None,
        after: Optional[list[QPointF]] = None,
        before_scales: Optional[list[float]] = None,
        after_scales: Optional[list[float]] = None,
    ) -> None:
        if not items:
            return
        self.undo_stack.append(HistoryEntry(action, list(items), before, after, before_scales, after_scales))
        if len(self.undo_stack) > self.HISTORY_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False

        entry = self.undo_stack.pop()
        if entry.action == "ADD":
            for item in entry.items:
                if item.scene() is self:
                    self.removeItem(item)
        elif entry.action == "REMOVE":
            for item in entry.items:
                if item.scene() is None:
                    self.addItem(item)
        elif entry.action == "MOVE" and entry.before is not None:
            for item, position in zip(entry.items, entry.before):
                item.setPos(position)
        elif entry.action == "TRANSFORM" and entry.before is not None and entry.before_scales is not None:
            for item, position, scale in zip(entry.items, entry.before, entry.before_scales):
                item.setPos(position)
                item.setScale(scale)
        elif entry.action == "REPLACE" and entry.items:
            replacement = entry.items[-1]
            if replacement.scene() is self:
                self.removeItem(replacement)
            for item in entry.items[:-1]:
                if item.scene() is None:
                    self.addItem(item)

        self.redo_stack.append(entry)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False

        entry = self.redo_stack.pop()
        if entry.action == "ADD":
            for item in entry.items:
                if item.scene() is None:
                    self.addItem(item)
        elif entry.action == "REMOVE":
            for item in entry.items:
                if item.scene() is self:
                    self.removeItem(item)
        elif entry.action == "MOVE" and entry.after is not None:
            for item, position in zip(entry.items, entry.after):
                item.setPos(position)
        elif entry.action == "TRANSFORM" and entry.after is not None and entry.after_scales is not None:
            for item, position, scale in zip(entry.items, entry.after, entry.after_scales):
                item.setPos(position)
                item.setScale(scale)
        elif entry.action == "REPLACE" and entry.items:
            for item in entry.items[:-1]:
                if item.scene() is self:
                    self.removeItem(item)
            replacement = entry.items[-1]
            if replacement.scene() is None:
                self.addItem(replacement)

        self.undo_stack.append(entry)
        return True


# ---------------------------------------------------------------------------
# Canvas interaction
# ---------------------------------------------------------------------------

class SmoothCanvasView(QGraphicsView):
    stroke_completed = Signal()
    zoom_changed = Signal(float)

    MIN_ZOOM = 0.20
    MAX_ZOOM = 4.00

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CanvasView")
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)

        self.current_mode = "PEN"
        self.lasso_submode = "FREE"
        self.shape_submode = "RECT"
        self.pen_color = QColor("#48DDF0")
        self.accent_color = QColor("#48DDF0")
        self.pen_width = 3.0
        self._tablet_active = False
        self._tablet_mouse_routed = False
        self._tablet_last_point = QPointF()
        self._tablet_last_width = 0.0
        self._tablet_items: list[QGraphicsItem] = []
        self._tablet_segment_item: Optional[QGraphicsPathItem] = None
        self.highlighter_width = 24.0
        self.current_zoom = 1.0
        self.fit_mode = "width"
        self.navigation_locked = False

        self.is_drawing = False
        self.is_panning = False
        self.space_pressed = False
        self.last_pan_pos = QPointF()

        self.points: list[QPointF] = []
        self.current_path: Optional[QPainterPath] = None
        self.current_item: Optional[QGraphicsPathItem] = None
        self.erased_in_drag: list[QGraphicsItem] = []
        self.lasso_path_item: Optional[QGraphicsPathItem] = None
        self.moving_items_before: Optional[list[tuple[QGraphicsItem, QPointF, float]]] = None

    def current_page_scene(self) -> Optional[PageScene]:
        scene = self.scene()
        return scene if isinstance(scene, PageScene) else None

    def set_page_scene(self, page_scene: PageScene) -> None:
        self.setScene(page_scene)
        self.fit_page_width()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if getattr(self, "fit_mode", "width") == "width":
            QTimer.singleShot(0, self.fit_page_width)

    def fit_page(self) -> None:
        scene = self.current_page_scene()
        if not scene or self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return
        self.resetTransform()
        margin = 28
        self.fitInView(scene.page_rect.adjusted(-margin, -margin, margin, margin), Qt.AspectRatioMode.KeepAspectRatio)
        self.fit_mode = "page"
        self.current_zoom = self.transform().m11()
        self.zoom_changed.emit(self.current_zoom)

    def fit_page_width(self) -> None:
        scene = self.current_page_scene()
        available_width = self.viewport().width() - 28
        if scene is None or available_width <= 0 or scene.page_rect.width() <= 0:
            return
        self.resetTransform()
        # Width-fit is a layout operation, so allow a smaller scale than the
        # manual zoom slider's minimum on narrow windows. This guarantees that
        # the whole sheet remains visible horizontally.
        target_zoom = max(0.05, min(self.MAX_ZOOM, available_width / scene.page_rect.width()))
        self.scale(target_zoom, target_zoom)
        self.centerOn(scene.page_rect.center())
        self.fit_mode = "width"
        self.current_zoom = self.transform().m11()
        self.zoom_changed.emit(self.current_zoom)

    def center_page(self) -> None:
        scene = self.current_page_scene()
        if scene is not None:
            self.centerOn(scene.page_rect.center())

    def set_zoom(self, target_zoom: float) -> None:
        if self.navigation_locked:
            return
        target_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, target_zoom))
        current = max(self.transform().m11(), 0.001)
        factor = target_zoom / current
        self.scale(factor, factor)
        self.fit_mode = "manual"
        self.current_zoom = self.transform().m11()
        self.zoom_changed.emit(self.current_zoom)

    def _page_point(self, point: QPointF, inset: float = 0.0) -> QPointF:
        """Keep drawing coordinates inside the printable page rectangle."""
        scene = self.current_page_scene()
        if scene is None:
            return point
        bounds = scene.page_rect.adjusted(inset, inset, -inset, -inset)
        if bounds.width() <= 0 or bounds.height() <= 0:
            bounds = scene.page_rect
        return QPointF(
            min(max(point.x(), bounds.left()), bounds.right()),
            min(max(point.y(), bounds.top()), bounds.bottom()),
        )

    def _keep_item_on_page(self, item: QGraphicsItem, scene: PageScene) -> None:
        """Clamp a moved item to the current page, keeping its full bounds visible."""
        page = scene.page_rect
        bounds = item.sceneBoundingRect()
        dx = 0.0
        dy = 0.0
        if bounds.left() < page.left():
            dx = page.left() - bounds.left()
        elif bounds.right() > page.right():
            dx = page.right() - bounds.right()
        if bounds.top() < page.top():
            dy = page.top() - bounds.top()
        elif bounds.bottom() > page.bottom():
            dy = page.bottom() - bounds.bottom()
        item.moveBy(dx, dy)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.transform().m11() * factor)

    def make_pen(self) -> QPen:
        if self.current_mode == "LASER":
            laser_color = QColor(self.pen_color)
            laser_color.setAlpha(245)
            return QPen(
                laser_color,
                max(2.5, self.pen_width),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        if self.current_mode == "HIGHLIGHTER":
            color = QColor(self.pen_color)
            color.setAlpha(88)
            return QPen(
                color,
                self.highlighter_width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        return QPen(
            self.pen_color,
            self.pen_width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )

    def _start_path_item(self, scene_pos: QPointF) -> None:
        self.current_path = QPainterPath(scene_pos)
        self.current_item = QGraphicsPathItem(self.current_path)
        self.current_item.setPen(self.make_pen())
        self.current_item.setData(0, self.current_mode)
        self.current_item.setZValue(-10 if self.current_mode == "HIGHLIGHTER" else 1)
        if self.current_mode == "LASER":
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(25)
            glow_color = QColor(self.pen_color)
            glow_color.setAlpha(210)
            glow.setColor(glow_color)
            glow.setOffset(0, 0)
            self.current_item.setGraphicsEffect(glow)
        # Repaint the vector path at the current zoom instead of scaling a
        # cached raster, which can make ink look grainy after zoom changes.
        self.current_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.current_item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        scene = self.current_page_scene()
        if scene:
            scene.addItem(self.current_item)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        scene = self.current_page_scene()
        if not scene:
            super().mousePressEvent(event)
            return

        button = event.button()
        wants_pan = button == Qt.MouseButton.MiddleButton or (
            self.space_pressed and button == Qt.MouseButton.LeftButton
        )
        if wants_pan:
            if self.navigation_locked:
                event.accept()
                return
            self.is_panning = True
            self.last_pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if button != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        # A tap on the pasteboard must not create ink, erase content, or start
        # a selection. Space/middle-button panning remains available there.
        if not scene.page_rect.contains(scene_pos):
            event.accept()
            return
            return

        if self.current_mode == "TEXT":
            hit_item = scene.itemAt(scene_pos, self.transform())
            if isinstance(hit_item, CanvasTextItem):
                hit_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
                hit_item.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
                return
            owner = self.window()
            if isinstance(owner, NotebookWindow):
                owner.add_text_box(scene_pos, begin_edit=True)
                event.accept()
                return

        # In lasso mode, clicking an existing item selects/moves it. Dragging
        # empty canvas starts a new lasso selection.
        if self.current_mode == "LASSO":
            item = scene.itemAt(scene_pos, self.transform())
            if item is not None and item is not scene.bg_item:
                if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and not item.isSelected():
                    scene.clearSelection()
                super().mousePressEvent(event)
                self.moving_items_before = [
                    (selected, QPointF(selected.pos()), selected.scale())
                    for selected in scene.selectedItems()
                    if selected is not scene.bg_item
                ]
                event.accept()
                return

        scene.clearSelection()

        if self.current_mode == "ERASER":
            self.is_drawing = True
            self.erased_in_drag = []
            self.erase_items_at(scene_pos)
            event.accept()
            return

        if self.current_mode == "LASSO":
            self.is_drawing = True
            self.points = [scene_pos]
            self.current_path = QPainterPath(scene_pos)
            self.lasso_path_item = QGraphicsPathItem(self.current_path)
            self.lasso_path_item.setPen(
                QPen(
                    self.accent_color,
                    2.0,
                    Qt.PenStyle.DashLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            self.lasso_path_item.setZValue(100)
            scene.addItem(self.lasso_path_item)
            event.accept()
            return

        self.is_drawing = True
        if self.current_mode != "LASSO":
            inset = self.make_pen().widthF() / 2.0 + 1.0
            scene_pos = self._page_point(scene_pos, inset)
        self.points = [scene_pos]
        self._start_path_item(scene_pos)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.is_panning:
            delta = event.position() - self.last_pan_pos
            self.last_pan_pos = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
            event.accept()
            return

        if not self.is_drawing or not self.current_page_scene():
            super().mouseMoveEvent(event)
            return

        raw_scene_pos = self.mapToScene(event.position().toPoint())
        inset = self.make_pen().widthF() / 2.0 + 1.0
        scene_pos = self._page_point(raw_scene_pos, inset)

        if self.current_mode == "ERASER":
            self.erase_items_at(scene_pos)
            event.accept()
            return

        if self.current_mode == "LASSO":
            self._update_lasso(scene_pos)
            event.accept()
            return

        if self.current_mode in ("LINE", "SHAPE", "RULER", "COMPASS"):
            if self.points:
                self.update_geometric_shape(self.points[0], scene_pos)
            event.accept()
            return

        if not self.current_path or not self.current_item:
            return

        if (scene_pos - self.points[-1]).manhattanLength() < 1.0:
            return

        self.points.append(scene_pos)
        previous = self.points[-2]
        midpoint = QPointF(
            (previous.x() + scene_pos.x()) / 2.0,
            (previous.y() + scene_pos.y()) / 2.0,
        )
        if len(self.points) == 2:
            self.current_path.lineTo(midpoint)
        else:
            self.current_path.quadTo(previous, midpoint)
        self.current_item.setPath(self.current_path)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.is_panning:
            self.is_panning = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return

        scene = self.current_page_scene()

        if self.moving_items_before is not None:
            super().mouseReleaseEvent(event)
            if scene:
                changed_items: list[QGraphicsItem] = []
                before_positions: list[QPointF] = []
                after_positions: list[QPointF] = []
                before_scales: list[float] = []
                after_scales: list[float] = []
                for item, old_pos, old_scale in self.moving_items_before:
                    self._keep_item_on_page(item, scene)
                    new_pos = QPointF(item.pos())
                    new_scale = item.scale()
                    if (new_pos - old_pos).manhattanLength() > 0.01 or abs(new_scale - old_scale) > 0.0001:
                        changed_items.append(item)
                        before_positions.append(old_pos)
                        after_positions.append(new_pos)
                        before_scales.append(old_scale)
                        after_scales.append(new_scale)
                if changed_items:
                    scene.push_action(
                        "TRANSFORM", changed_items, before_positions, after_positions,
                        before_scales, after_scales,
                    )
                    self.stroke_completed.emit()
            self.moving_items_before = None
            event.accept()
            return

        if not self.is_drawing or not scene:
            super().mouseReleaseEvent(event)
            return

        self.is_drawing = False
        release_pos = self._page_point(
            self.mapToScene(event.position().toPoint()),
            self.make_pen().widthF() / 2.0 + 1.0,
        )

        if self.current_mode == "ERASER":
            if self.erased_in_drag:
                scene.push_action("REMOVE", self.erased_in_drag)
                self.erased_in_drag = []
                self.stroke_completed.emit()
            event.accept()
            return

        if self.current_mode == "LASSO":
            self.finish_lasso_selection()
            self.stroke_completed.emit()
            event.accept()
            return

        if self.current_item and self.current_path:
            if self.current_mode in ("LINE", "SHAPE", "RULER", "COMPASS"):
                self.update_geometric_shape(self.points[0], release_pos)
            elif self.points:
                release_added = (release_pos - self.points[-1]).manhattanLength() >= 1.0
                if release_added:
                    self.points.append(release_pos)
                if len(self.points) == 1:
                    self.current_path.lineTo(release_pos)
                elif len(self.points) == 2:
                    self.current_path.lineTo(self.points[-1])
                else:
                    control = self.points[-2] if release_added else self.points[-1]
                    self.current_path.quadTo(control, release_pos)
                self.current_item.setPath(self.current_path)

            # Ignore an accidental zero-length click.
            if not self.current_path.isEmpty() and self.current_path.length() > 0.5:
                scene.push_action("ADD", [self.current_item])
            else:
                scene.removeItem(self.current_item)

        self.current_item = None
        self.current_path = None
        self.points.clear()
        self.stroke_completed.emit()
        event.accept()

    def tabletEvent(self, event) -> None:  # noqa: N802 - Qt override
        event_type = event.type()
        # QGraphicsView's base mouse handlers accept QMouseEvent only. Route
        # tablet input through equivalent mouse events for tools that rely on
        # Graphics View's item selection/drag behavior (notably lasso); keep
        # native tablet events for pressure-sensitive ink below.
        if self.current_mode not in {"PEN", "HIGHLIGHTER", "LASER"}:
            if event_type == QEvent.Type.TabletPress and event.button() == Qt.MouseButton.LeftButton:
                self._tablet_mouse_routed = True
                mouse_event = QMouseEvent(
                    QEvent.Type.MouseButtonPress,
                    event.position(),
                    event.globalPosition(),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    event.modifiers(),
                )
                self.mousePressEvent(mouse_event)
                event.accept()
                return
            if event_type == QEvent.Type.TabletMove and self._tablet_mouse_routed:
                mouse_event = QMouseEvent(
                    QEvent.Type.MouseMove,
                    event.position(),
                    event.globalPosition(),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton,
                    event.modifiers(),
                )
                self.mouseMoveEvent(mouse_event)
                event.accept()
                return
            if event_type == QEvent.Type.TabletRelease and self._tablet_mouse_routed:
                mouse_event = QMouseEvent(
                    QEvent.Type.MouseButtonRelease,
                    event.position(),
                    event.globalPosition(),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                    event.modifiers(),
                )
                self._tablet_mouse_routed = False
                self.mouseReleaseEvent(mouse_event)
                event.accept()
                return
        if event_type == QEvent.Type.TabletPress and event.button() == Qt.MouseButton.LeftButton:
            mouse_event = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                event.modifiers(),
            )
            self.mousePressEvent(mouse_event)
            if self.is_drawing and self.current_mode in {"PEN", "HIGHLIGHTER", "LASER"}:
                scene = self.current_page_scene()
                if scene is not None:
                    if self.current_item is not None:
                        scene.removeItem(self.current_item)
                    self.current_item = None
                    self.current_path = None
                    self._tablet_items = []
                    self._tablet_segment_item = None
                    self._tablet_last_point = self._page_point(
                        self.mapToScene(event.position().toPoint()), self.make_pen().widthF() / 2.0 + 1.0
                    )
                    self._tablet_last_width = 0.0
                    self._tablet_active = True
            event.accept()
            return
        if event_type == QEvent.Type.TabletMove and self._tablet_active:
            scene = self.current_page_scene()
            point = self._page_point(
                self.mapToScene(event.position().toPoint()), self.make_pen().widthF() / 2.0 + 1.0
            )
            pressure = max(0.08, min(1.0, float(event.pressure())))
            # Tablet pressure sensors can jitter from frame to frame. Smooth
            # pressure and use wider width buckets so one stroke does not
            # fragment into dozens of tiny graphics items.
            pressure = pressure * 0.35 + getattr(self, "_tablet_smoothed_pressure", pressure) * 0.65
            self._tablet_smoothed_pressure = pressure
            base_width = self.highlighter_width if self.current_mode == "HIGHLIGHTER" else max(2.5, self.pen_width) if self.current_mode == "LASER" else self.pen_width
            width = float(max(1, round(max(0.6, base_width * (0.22 + pressure * 0.78)))))
            if scene is not None and (point - self._tablet_last_point).manhattanLength() >= 0.8:
                segment = self._tablet_segment_item
                if segment is None or abs(width - self._tablet_last_width) >= 1.5:
                    segment_path = QPainterPath()
                    segment_path.moveTo(self._tablet_last_point)
                    segment = QGraphicsPathItem()
                    segment.setPath(segment_path)
                    pen = self.make_pen()
                    pen.setWidthF(width)
                    segment.setPen(pen)
                    segment.setData(0, self.current_mode)
                    segment.setZValue(-10 if self.current_mode == "HIGHLIGHTER" else 1)
                    segment.setCacheMode(QGraphicsItem.CacheMode.NoCache)
                    segment.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
                    if self.current_mode == "LASER":
                        glow = QGraphicsDropShadowEffect()
                        glow.setBlurRadius(25)
                        glow.setColor(self.pen_color)
                        glow.setOffset(0, 0)
                        segment.setGraphicsEffect(glow)
                    scene.addItem(segment)
                    self._tablet_items.append(segment)
                    self._tablet_segment_item = segment
                    self._tablet_last_width = width
                path = segment.path()
                path.lineTo(point)
                segment.setPath(path)
                self._tablet_last_point = point
            event.accept()
            return
        if event_type == QEvent.Type.TabletRelease and self._tablet_active:
            scene = self.current_page_scene()
            self._tablet_active = False
            self.is_drawing = False
            if scene is not None and self._tablet_items:
                scene.push_action("ADD", self._tablet_items)
                self.stroke_completed.emit()
            self._tablet_items = []
            self._tablet_segment_item = None
            self._tablet_smoothed_pressure = 0.0
            self.points.clear()
            self.current_path = None
            self.current_item = None
            event.accept()
            return
        super().tabletEvent(event)

    def _update_lasso(self, scene_pos: QPointF) -> None:
        lasso_item = self.lasso_path_item
        current_path = self.current_path
        if lasso_item is None or not self.points or current_path is None:
            return
        if self.lasso_submode == "FREE":
            self.points.append(scene_pos)
            current_path.lineTo(scene_pos)
        else:
            rect = QRectF(self.points[0], scene_pos).normalized()
            path = QPainterPath()
            path.addRect(rect)
            current_path = path
            self.current_path = current_path
        lasso_item.setPath(current_path)

    def finish_lasso_selection(self) -> None:
        scene = self.current_page_scene()
        if not scene or not self.lasso_path_item or not self.current_path:
            self._clear_lasso_state()
            return

        selection_path = QPainterPath(self.current_path)
        if self.lasso_submode == "FREE":
            selection_path.closeSubpath()

        for item in scene.items(selection_path, Qt.ItemSelectionMode.IntersectsItemShape):
            if item is not scene.bg_item and item is not self.lasso_path_item:
                if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                    item.setSelected(True)

        scene.removeItem(self.lasso_path_item)
        self._clear_lasso_state()

    def _clear_lasso_state(self) -> None:
        self.lasso_path_item = None
        self.current_path = None
        self.points.clear()

    def erase_items_at(self, scene_pos: QPointF) -> None:
        scene = self.current_page_scene()
        if not scene:
            return
        radius = 13.0
        hit_box = QRectF(
            scene_pos.x() - radius,
            scene_pos.y() - radius,
            radius * 2,
            radius * 2,
        )
        for item in scene.items(hit_box, Qt.ItemSelectionMode.IntersectsItemShape):
            if item is not scene.bg_item and item not in self.erased_in_drag:
                scene.removeItem(item)
                self.erased_in_drag.append(item)

    def update_geometric_shape(self, start: QPointF, end: QPointF) -> None:
        if not self.current_item:
            return

        path = QPainterPath()
        rect = QRectF(start, end).normalized()
        if self.current_mode in ("LINE", "RULER"):
            path.moveTo(start)
            path.lineTo(end)
            if self.current_mode == "RULER":
                delta_x = end.x() - start.x()
                delta_y = end.y() - start.y()
                length = math.hypot(delta_x, delta_y)
                if length > 1:
                    unit_x, unit_y = delta_x / length, delta_y / length
                    normal_x, normal_y = -unit_y, unit_x
                    for mark in range(0, int(length), 40):
                        fraction = mark / length
                        center = QPointF(start.x() + delta_x * fraction, start.y() + delta_y * fraction)
                        half_tick = 14 if mark % 200 == 0 else 7
                        inset = self.current_item.pen().widthF() / 2.0 + 1.0
                        tick_start = self._page_point(
                            QPointF(center.x() - normal_x * half_tick, center.y() - normal_y * half_tick), inset
                        )
                        tick_end = self._page_point(
                            QPointF(center.x() + normal_x * half_tick, center.y() + normal_y * half_tick), inset
                        )
                        path.moveTo(tick_start)
                        path.lineTo(tick_end)
        elif self.current_mode == "COMPASS":
            radius = math.hypot(end.x() - start.x(), end.y() - start.y())
            scene = self.current_page_scene()
            if scene is not None:
                page = scene.page_rect
                inset = self.current_item.pen().widthF() / 2.0 + 1.0
                radius = min(
                    radius,
                    max(0.0, start.x() - page.left() - inset),
                    max(0.0, page.right() - start.x() - inset),
                    max(0.0, start.y() - page.top() - inset),
                    max(0.0, page.bottom() - start.y() - inset),
                )
            path.addEllipse(QRectF(start.x() - radius, start.y() - radius, radius * 2, radius * 2))
        elif self.shape_submode == "RECT":
            path.addRect(rect)
        elif self.shape_submode == "CIRCLE":
            path.addEllipse(rect)
        elif self.shape_submode == "TRIANGLE":
            polygon = QPolygonF(
                [
                    QPointF(rect.center().x(), rect.top()),
                    QPointF(rect.bottomLeft()),
                    QPointF(rect.bottomRight()),
                ]
            )
            path.addPolygon(polygon)
            path.closeSubpath()
        self.current_path = path
        self.current_item.setPath(path)

    def delete_selected_items(self) -> None:
        scene = self.current_page_scene()
        if not scene:
            return
        selected = [item for item in scene.selectedItems() if item is not scene.bg_item]
        if not selected:
            return
        for item in selected:
            scene.removeItem(item)
        scene.push_action("REMOVE", selected)
        self.stroke_completed.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_pressed = True
            if not self.navigation_locked:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected_items()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_V
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            owner = self.window()
            if isinstance(owner, NotebookWindow) and owner.paste_clipboard_content():
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.space_pressed = False
            self.is_panning = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.navigation_locked:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.12 if delta > 0 else 1.0 / 1.12)
        event.accept()


# ---------------------------------------------------------------------------
# Floating canvas controls
# ---------------------------------------------------------------------------

class ToolSubOptionsWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("FloatingPanel")
        self.sub_layout = QHBoxLayout(self)
        self.sub_layout.setContentsMargins(8, 6, 8, 6)
        self.sub_layout.setSpacing(7)
        self.hide()


class ShapeInspectorPopup(QWidget):
    def __init__(self, canvas: SmoothCanvasView, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.canvas = canvas
        self.setObjectName("FloatingPanel")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(7)

        title = QLabel("SHAPE STYLE")
        title.setObjectName("PanelHeading")
        layout.addWidget(title)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 20)
        self.size_slider.setValue(int(canvas.pen_width))
        self.size_slider.valueChanged.connect(lambda value: setattr(canvas, "pen_width", float(value)))
        self.size_slider.setFixedWidth(78)
        layout.addWidget(QLabel("Stroke"))
        layout.addWidget(self.size_slider)

        self.shape_buttons: dict[str, QPushButton] = {}
        options = [("Rectangle", "RECT"), ("Circle", "CIRCLE"), ("Triangle", "TRIANGLE")]
        for label, mode in options:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(canvas.shape_submode == mode)
            button.clicked.connect(lambda checked=False, selected=mode: self.set_shape(selected))
            layout.addWidget(button)
            self.shape_buttons[mode] = button
        self.setFixedSize(410, 52)
        self.hide()

    def set_shape(self, mode: str) -> None:
        self.canvas.shape_submode = mode
        for shape, button in self.shape_buttons.items():
            button.setChecked(shape == mode)


class CalculatorDialog(QDialog):
    """Small calculator with a deliberately restricted arithmetic parser."""

    def __init__(self, parent: QWidget, theme: Theme):
        super().__init__(parent)
        self.setWindowTitle("RobustNotes Calculator")
        self.setModal(False)
        self.setFixedSize(300, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.display = QLineEdit()
        self.display.setObjectName("CalculatorDisplay")
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setPlaceholderText("0")
        self.display.setMinimumHeight(48)
        self.display.returnPressed.connect(self.calculate)
        layout.addWidget(self.display)

        grid = QGridLayout()
        grid.setSpacing(7)
        keys = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2), ("÷", 0, 3),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2), ("×", 1, 3),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2), ("−", 2, 3),
            ("0", 3, 0), (".", 3, 1), ("(", 3, 2), ("+", 3, 3),
            ("C", 4, 0), ("⌫", 4, 1), (")", 4, 2), ("=", 4, 3),
        ]
        for label, row, column in keys:
            button = QPushButton(label)
            button.setMinimumHeight(42)
            if label == "=":
                button.setObjectName("CalculatorEquals")
                button.clicked.connect(self.calculate)
            elif label == "C":
                button.clicked.connect(self.display.clear)
            elif label == "⌫":
                button.clicked.connect(self.display.backspace)
            else:
                text = {"÷": "/", "×": "*", "−": "-"}.get(label, label)
                button.clicked.connect(lambda checked=False, value=text: self.display.insert(value))
            grid.addWidget(button, row, column)
        layout.addLayout(grid)
        self.setStyleSheet(
            f"QDialog {{ background: {theme['surface']}; color: {theme['text']}; }} "
            f"QLineEdit#CalculatorDisplay {{ background: {theme['canvas']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 9px; padding: 8px; font-size: 20px; }} "
            f"QPushButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 8px; font-size: 15px; font-weight: 700; }} "
            f"QPushButton:hover {{ border-color: {theme['accent']}; }} "
            f"QPushButton#CalculatorEquals {{ background: {theme['accent']}; color: #101318; border: none; }}"
        )

    def calculate(self) -> None:
        expression = self.display.text().strip()
        if not expression or len(expression) > 200:
            return
        try:
            tree = ast.parse(expression, mode="eval")
            result = self._evaluate(tree.body)
            if not math.isfinite(result):
                raise ValueError("Result is outside the supported range.")
            self.display.setText(format(result, ".12g"))
        except Exception:
            self.display.setStyleSheet("QLineEdit { border: 1px solid #F36B83; }")
            QTimer.singleShot(900, lambda: self.display.setStyleSheet(""))

    @classmethod
    def _evaluate(cls, node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = cls._evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = cls._evaluate(node.left)
            right = cls._evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow) and abs(right) <= 12:
                return left ** right
        raise ValueError("Only basic arithmetic is supported.")


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class NotebookWindow(QMainWindow):
    def __init__(
        self,
        document_path: Optional[Path] = None,
        document_type: str = "notebook",
        on_saved=None,
        settings_path: Optional[Path] = None,
        template_name: str = "Dot grid",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowIcon(robustnotes_icon())
        self.document_path = Path(document_path) if document_path else None
        self.document_type = document_type
        self.document_title = self.document_path.stem if self.document_path else "Untitled notebook"
        self.on_saved = on_saved
        self.is_loading_document = True
        self.setWindowTitle(f"{self.document_title} — RobustNotes Studio")
        self.resize(1500, 920)
        self.setMinimumSize(980, 650)

        self.current_theme_name = "Cyber Cyan"
        self.template_name = template_name
        self.template_pixmap = self.make_whiteboard_template() if document_type == "whiteboard" else self.make_notebook_template(self.load_or_create_template(), template_name)
        self.pages: list[PageScene] = []
        self._saved_page_image_keys: dict[Path, int] = {}
        self._saved_canvas_image_keys: dict[Path, int] = {}
        self.imported_pdf_texts: list[tuple[str, str]] = []
        self.ai_history: list[dict[str, str]] = []
        self.calculator_dialog: Optional[CalculatorDialog] = None
        self.ai_settings = (
            QSettings(str(settings_path), QSettings.Format.IniFormat)
            if settings_path is not None
            else QSettings(
                str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "settings.ini"),
                QSettings.Format.IniFormat,
            )
        )
        self.ai_endpoint = os.environ.get(
            "ROBUSTNOTES_OLLAMA_URL",
            str(self.ai_settings.value("aiEndpoint", "http://127.0.0.1:11434")),
        ).rstrip("/")
        self.ai_model = os.environ.get(
            "ROBUSTNOTES_MODEL",
            str(self.ai_settings.value("aiModel", "robustnotes-slm")),
        )
        self.ai_worker: Optional[LocalAIChatWorker] = None
        self.update_check_worker: Optional[UpdateCheckWorker] = None
        self.update_download_worker: Optional[UpdateDownloadWorker] = None
        self.ocr_worker: Optional[HandwritingOCRWorker] = None
        self.ocr_target_scene: Optional[PageScene] = None
        self.ocr_target_items: Sequence[QGraphicsItem] = ()
        self.music_dialog: Optional[FunkwhaleMusicPlayerDialog] = None
        self.transcription_worker: Optional[LocalTranscriptionWorker] = None
        self.audio_source = None
        self.audio_capture_device = None
        self.audio_samples = bytearray()
        self.audio_sample_rate = 16_000
        self.audio_channel_count = 1
        self.voice_timer = QTimer(self)
        self.voice_timer.setSingleShot(True)
        self.voice_timer.setInterval(60_000)
        self.voice_timer.timeout.connect(self.stop_voice_input)
        self.tool_buttons: dict[str, QPushButton] = {}
        self.swatch_buttons: list[QPushButton] = []
        self.shortcuts: list[QShortcut] = []
        self.stroke_slider: Optional[QSlider] = None
        self.stroke_value_label: Optional[QLabel] = None

        central = QWidget()
        central.setObjectName("AppRoot")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.toolbar_widget = self.build_top_toolbar()
        root_layout.addWidget(self.toolbar_widget)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.sidebar_widget = self.build_left_sidebar()
        body_layout.addWidget(self.sidebar_widget)

        self.canvas = SmoothCanvasView()
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        # Avoid a delayed tooltip appearing under a stylus hovering over the page.
        # The same guidance is already available in the editor status bar.
        self.thumbnail_timer = QTimer(self)
        self.thumbnail_timer.setSingleShot(True)
        self.thumbnail_timer.setInterval(600)
        self.thumbnail_timer.timeout.connect(self.update_current_thumbnail)
        self.document_save_timer = QTimer(self)
        self.document_save_timer.setSingleShot(True)
        self.document_save_timer.setInterval(800)
        self.document_save_timer.timeout.connect(self.save_document)
        self.canvas.stroke_completed.connect(self.schedule_thumbnail_update)
        self.canvas.stroke_completed.connect(self.update_status)
        self.canvas.zoom_changed.connect(self.update_zoom_hud)
        body_layout.addWidget(self.canvas, 1)
        self.zoom_hud = self.build_zoom_hud()
        self.ai_sidebar = self.build_ai_sidebar()
        body_layout.addWidget(self.ai_sidebar)
        root_layout.addWidget(body, 1)

        self.shape_inspector = ShapeInspectorPopup(self.canvas, self.canvas)
        self.shape_inspector.setParent(self.toolbar_widget)
        self.toolbar_layout.insertWidget(self.toolbar_context_index, self.shape_inspector)
        self.toolbar_context_index += 1

        self.bind_shortcuts()
        if self.document_path and self.document_path.exists():
            self.load_document()
        else:
            self.add_new_page()
        self.is_loading_document = False
        self.select_tool("PEN")
        self.apply_theme(self.current_theme_name)
        self.save_document()
        QTimer.singleShot(0, self.canvas.fit_page_width)
        self.statusBar().showMessage("Ready  ·  P to draw  ·  Space + drag to pan  ·  Ctrl+Z to undo")

    # ----- Layout construction ------------------------------------------------

    def build_top_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopToolbar")
        bar.setFixedHeight(76)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(5)
        self.toolbar_layout = layout

        undo_btn = QPushButton()
        undo_btn.setObjectName("IconButton")
        undo_btn.setFixedSize(36, 36)
        undo_btn.setIcon(self.make_history_icon(redo=False))
        undo_btn.setIconSize(QSize(21, 21))
        undo_btn.setToolTip("Undo  ·  Ctrl+Z")
        undo_btn.clicked.connect(self.undo_current)

        redo_btn = QPushButton()
        redo_btn.setObjectName("IconButton")
        redo_btn.setFixedSize(36, 36)
        redo_btn.setIcon(self.make_history_icon(redo=True))
        redo_btn.setIconSize(QSize(21, 21))
        redo_btn.setToolTip("Redo  ·  Ctrl+Y")
        redo_btn.clicked.connect(self.redo_current)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 14, 0)
        brand_layout.setSpacing(0)
        brand_layout.addWidget(full_logo_label(190, 48))
        layout.addWidget(brand)
        layout.addWidget(undo_btn)
        layout.addWidget(redo_btn)

        separator = QWidget()
        separator.setObjectName("ToolbarSeparator")
        separator.setFixedWidth(1)
        separator.setFixedHeight(38)
        layout.addWidget(separator)

        tools = [
            ("PEN", "Pen", "P"),
            ("HIGHLIGHTER", "Marker", "H"),
            ("LASER", "Laser", "Z"),
            ("ERASER", "Eraser", "E"),
            ("LINE", "Line", "I"),
            ("SHAPE", "Shapes", "S"),
            ("LASSO", "Select", "L"),
            ("TEXT", "Text box", "T"),
            ("IMAGE", "Insert image", ""),
        ]
        tool_deck = QWidget()
        tool_deck.setObjectName("ToolDeck")
        tool_layout = QHBoxLayout(tool_deck)
        tool_layout.setContentsMargins(5, 5, 5, 5)
        tool_layout.setSpacing(3)
        for mode, label, shortcut in tools:
            button = QPushButton()
            button.setObjectName("ToolButton")
            button.setCheckable(mode != "IMAGE")
            button.setFixedSize(44, 44)
            button.setIcon(self.make_tool_icon(mode))
            button.setIconSize(QSize(25, 25))
            button.setAccessibleName(label)
            button.setToolTip(f"{label}  ·  {shortcut}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda checked=False, selected=mode: (
                    self.import_canvas_image() if selected == "IMAGE" else self.select_tool(selected)
                )
            )
            tool_layout.addWidget(button)
            self.tool_buttons[mode] = button
        layout.addWidget(tool_deck)

        separator2 = QWidget()
        separator2.setObjectName("ToolbarSeparator")
        separator2.setFixedWidth(1)
        separator2.setFixedHeight(38)
        layout.addWidget(separator2)

        colors_panel = QWidget()
        colors_panel.setObjectName("InkDeck")
        colors_layout = QVBoxLayout(colors_panel)
        colors_layout.setContentsMargins(7, 3, 7, 3)
        colors_layout.setSpacing(3)
        colors_title = QLabel("INK")
        colors_title.setObjectName("SmallSectionLabel")
        colors_layout.addWidget(colors_title)
        self.swatch_container = QWidget()
        self.swatch_layout = QHBoxLayout(self.swatch_container)
        self.swatch_layout.setContentsMargins(0, 0, 0, 0)
        self.swatch_layout.setSpacing(5)
        colors_layout.addWidget(self.swatch_container)
        layout.addWidget(colors_panel)

        self.custom_color_btn = QPushButton()
        self.custom_color_btn.setObjectName("IconButton")
        self.custom_color_btn.setFixedSize(34, 34)
        self.custom_color_btn.setIcon(self.make_action_icon("PLUS"))
        self.custom_color_btn.setIconSize(QSize(18, 18))
        self.custom_color_btn.setAccessibleName("Custom ink colour")
        self.custom_color_btn.setToolTip("Choose a custom ink colour")
        self.custom_color_btn.clicked.connect(self.open_color_dialog)
        layout.addWidget(self.custom_color_btn)

        self.sub_options_widget = ToolSubOptionsWidget(bar)
        layout.addWidget(self.sub_options_widget)
        self.toolbar_context_index = layout.count()

        self.ai_toggle_btn = QPushButton("AI")
        self.ai_toggle_btn.setObjectName("IconButton")
        self.ai_toggle_btn.setCheckable(True)
        self.ai_toggle_btn.setChecked(True)
        self.ai_toggle_btn.setFixedSize(38, 36)
        self.ai_toggle_btn.setAccessibleName("RobustNotes AI")
        self.ai_toggle_btn.setToolTip("Show or hide RobustNotes AI chat")
        self.ai_toggle_btn.toggled.connect(self.toggle_ai_sidebar)
        layout.addWidget(self.ai_toggle_btn)

        self.music_toggle_btn = QPushButton("♪")
        self.music_toggle_btn.setObjectName("IconButton")
        self.music_toggle_btn.setFixedSize(38, 36)
        self.music_toggle_btn.setAccessibleName("Study Music")
        self.music_toggle_btn.setToolTip("Open the separate Study Music player")
        self.music_toggle_btn.clicked.connect(self.open_music_player)
        layout.addWidget(self.music_toggle_btn)

        layout.addStretch(1)

        theme_label = QLabel("THEME")
        theme_label.setObjectName("SmallSectionLabel")
        layout.addWidget(theme_label)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        self.theme_combo.setFixedWidth(118)
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        layout.addWidget(self.theme_combo)

        more_btn = QPushButton()
        more_btn.setObjectName("IconButton")
        more_btn.setFixedSize(38, 38)
        more_btn.setIcon(self.make_action_icon("MORE"))
        more_btn.setIconSize(QSize(20, 20))
        more_btn.setAccessibleName("More notebook actions")
        more_btn.setToolTip("More notebook actions")
        more_btn.clicked.connect(self.show_more_menu)
        layout.addWidget(more_btn)
        return bar

    @staticmethod
    def make_tool_icon(mode: str, color: str = "#D7DEE8") -> QIcon:
        """Create crisp, consistent vector-like icons for the toolbar tools."""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 5.0, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if mode == "PEN":
            path = QPainterPath(QPointF(16, 43))
            path.lineTo(39, 20)
            path.lineTo(45, 26)
            path.lineTo(22, 49)
            painter.drawPath(path)
            painter.drawLine(QPointF(16, 43), QPointF(12, 52))
            painter.drawLine(QPointF(12, 52), QPointF(22, 49))
            painter.drawLine(QPointF(36, 17), QPointF(42, 11))
            painter.drawLine(QPointF(45, 20), QPointF(51, 14))
        elif mode == "HIGHLIGHTER":
            painter.save()
            painter.translate(32, 32)
            painter.rotate(-45)
            painter.drawRoundedRect(QRectF(-10, -22, 20, 40), 4, 4)
            painter.drawLine(QPointF(-10, 8), QPointF(10, 8))
            painter.drawLine(QPointF(-6, 18), QPointF(6, 18))
            painter.restore()
        elif mode == "LASER":
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(32, 32), 12, 12)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(32, 32), 22, 22)
            painter.drawLine(QPointF(32, 5), QPointF(32, 10))
            painter.drawLine(QPointF(32, 54), QPointF(32, 59))
            painter.drawLine(QPointF(5, 32), QPointF(10, 32))
            painter.drawLine(QPointF(54, 32), QPointF(59, 32))
        elif mode == "ERASER":
            polygon = QPolygonF([
                QPointF(13, 37), QPointF(31, 17), QPointF(49, 33),
                QPointF(34, 50), QPointF(23, 50),
            ])
            painter.drawPolygon(polygon)
            painter.drawLine(QPointF(24, 26), QPointF(41, 42))
            painter.drawLine(QPointF(21, 51), QPointF(50, 51))
        elif mode == "LINE":
            painter.drawLine(QPointF(14, 48), QPointF(48, 14))
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(12, 46), 4, 4)
            painter.drawEllipse(QPointF(46, 12), 4, 4)
        elif mode == "SHAPE":
            painter.drawRoundedRect(QRectF(10, 11, 28, 28), 3, 3)
            painter.drawEllipse(QRectF(27, 27, 26, 26))
            painter.drawLine(QPointF(18, 18), QPointF(30, 18))
        elif mode == "LASSO":
            path = QPainterPath()
            path.moveTo(43, 42)
            path.cubicTo(36, 52, 19, 51, 13, 38)
            path.cubicTo(6, 22, 19, 11, 33, 14)
            path.cubicTo(47, 17, 51, 29, 43, 37)
            painter.drawPath(path)
            painter.drawLine(QPointF(43, 37), QPointF(51, 44))
            painter.drawEllipse(QPointF(51, 44), 2.5, 2.5)
        elif mode == "TEXT":
            painter.setFont(QFont("Segoe UI", 35, QFont.Weight.Bold))
            painter.drawText(QRectF(12, 6, 40, 48), Qt.AlignmentFlag.AlignCenter, "T")
            painter.drawLine(QPointF(13, 54), QPointF(51, 54))
        elif mode == "IMAGE":
            painter.drawRoundedRect(QRectF(8, 10, 48, 42), 5, 5)
            painter.drawEllipse(QPointF(42, 21), 3.5, 3.5)
            path = QPainterPath(QPointF(12, 46))
            path.lineTo(24, 32)
            path.lineTo(32, 40)
            path.lineTo(40, 29)
            path.lineTo(52, 45)
            painter.drawPath(path)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def make_history_icon(redo: bool) -> QIcon:
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#D7DEE8"), 4.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if redo:
            path = QPainterPath(QPointF(35, 15))
            path.cubicTo(10, 6, 8, 36, 30, 36)
            painter.drawPath(path)
            painter.drawLine(QPointF(35, 15), QPointF(25, 12))
            painter.drawLine(QPointF(35, 15), QPointF(34, 25))
        else:
            path = QPainterPath(QPointF(13, 15))
            path.cubicTo(38, 6, 40, 36, 18, 36)
            painter.drawPath(path)
            painter.drawLine(QPointF(13, 15), QPointF(23, 12))
            painter.drawLine(QPointF(13, 15), QPointF(14, 25))
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def make_action_icon(mode: str, color: str = "#D7DEE8") -> QIcon:
        """Draw common controls without relying on installed font glyphs."""
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 4.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if mode == "PLUS":
            painter.drawLine(QPointF(24, 11), QPointF(24, 37))
            painter.drawLine(QPointF(11, 24), QPointF(37, 24))
        elif mode == "MORE":
            painter.setBrush(QColor(color))
            for x in (10, 24, 38):
                painter.drawEllipse(QPointF(x, 24), 2.4, 2.4)
        elif mode == "REFRESH":
            painter.drawArc(QRectF(10, 10, 28, 28), 35 * 16, 300 * 16)
            painter.drawLine(QPointF(35, 7), QPointF(38, 18))
            painter.drawLine(QPointF(35, 7), QPointF(25, 10))
        painter.end()
        return QIcon(pixmap)

    def build_left_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(226)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title = QLabel("PAGES")
        title.setObjectName("SidebarHeading")
        self.page_count_label = QLabel("01")
        self.page_count_label.setObjectName("PageCount")
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.page_count_label)
        layout.addLayout(heading)

        new_page_btn = QPushButton("New page")
        new_page_btn.setObjectName("NewPageButton")
        new_page_btn.setIcon(self.make_action_icon("PLUS"))
        new_page_btn.setIconSize(QSize(16, 16))
        new_page_btn.setAccessibleName("New page")
        new_page_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_page_btn.clicked.connect(self.add_new_page)
        layout.addWidget(new_page_btn)

        self.page_list = QListWidget()
        self.page_list.setObjectName("PageList")
        self.page_list.setIconSize(QSize(178, 104))
        self.page_list.setViewMode(QListView.ViewMode.IconMode)
        self.page_list.setFlow(QListView.Flow.TopToBottom)
        self.page_list.setWrapping(False)
        self.page_list.setMovement(QListView.Movement.Static)
        self.page_list.setGridSize(QSize(198, 142))
        self.page_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.page_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_list.setSpacing(8)
        self.page_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.page_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.page_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.page_list.currentRowChanged.connect(self.switch_page)
        self.page_list.model().rowsMoved.connect(self.on_page_rows_moved)
        self.page_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.page_list.customContextMenuRequested.connect(self.show_page_context_menu)
        self.page_list.itemDoubleClicked.connect(self.rename_page)
        layout.addWidget(self.page_list, 1)

        footer = QLabel("TIP  ·  Double-click to rename  ·  Ctrl-click pages to study together")
        footer.setObjectName("SidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        return sidebar

    def build_ai_sidebar(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("AISidebar")
        panel.setFixedWidth(350)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("RobustNotes AI")
        title.setObjectName("AIHeading")
        header.addWidget(title)
        header.addStretch(1)
        self.ai_close_btn = QPushButton("×")
        self.ai_close_btn.setObjectName("AIHideButton")
        self.ai_close_btn.setFixedSize(30, 30)
        self.ai_close_btn.setToolTip("Hide RobustNotes AI")
        self.ai_close_btn.clicked.connect(lambda: self.ai_toggle_btn.setChecked(False))
        header.addWidget(self.ai_close_btn)
        self.ai_model_btn = QPushButton("Model")
        self.ai_model_btn.setObjectName("AIKeyButton")
        self.ai_model_btn.setToolTip("Configure your local RobustNotes SLM")
        self.ai_model_btn.clicked.connect(self.configure_local_model)
        header.addWidget(self.ai_model_btn)
        layout.addLayout(header)

        self.ai_context_label = QLabel("Grounded in the current page and imported PDFs")
        self.ai_context_label.setObjectName("AIContextLabel")
        self.ai_context_label.setWordWrap(True)
        layout.addWidget(self.ai_context_label)

        self.ocr_button = QPushButton("Recognize handwriting")
        self.ocr_button.setObjectName("AIActionButton")
        self.ocr_button.setToolTip("Read handwriting on the current page using a local transformer model")
        self.ocr_button.clicked.connect(self.recognize_handwriting)
        layout.addWidget(self.ocr_button)

        self.ai_chat_view = QTextBrowser()
        self.ai_chat_view.setObjectName("AIChatView")
        self.ai_chat_view.setOpenExternalLinks(True)
        self.ai_chat_view.append(
            '<div class="assistant"><b>RobustNotes AI</b><br>'
            "Ask about your handwritten page, diagrams, or text in an imported PDF. "
            "Questions and page context stay on this computer and are sent to your configured local model.</div>"
        )
        layout.addWidget(self.ai_chat_view, 1)

        self.ai_status_label = QLabel(f"Local model · {self.ai_model}")
        self.ai_status_label.setObjectName("AIStatusLabel")
        layout.addWidget(self.ai_status_label)

        self.chat_input = QTextEdit()
        self.chat_input.setObjectName("AIInput")
        self.chat_input.setPlaceholderText("Ask something about these notes…")
        self.chat_input.setFixedHeight(82)
        layout.addWidget(self.chat_input)

        actions = QHBoxLayout()
        self.voice_button = QPushButton("● Voice")
        self.voice_button.setObjectName("AIActionButton")
        self.voice_button.setToolTip("Record a voice question and transcribe it")
        self.voice_button.clicked.connect(self.toggle_voice_input)
        actions.addWidget(self.voice_button)
        actions.addStretch(1)
        self.ai_send_button = QPushButton("Ask  ↑")
        self.ai_send_button.setObjectName("AISendButton")
        self.ai_send_button.clicked.connect(self.send_ai_message)
        actions.addWidget(self.ai_send_button)
        layout.addLayout(actions)
        return panel

    def build_zoom_hud(self) -> QWidget:
        hud = QWidget(self.canvas)
        hud.setObjectName("ZoomHUD")
        layout = QHBoxLayout(hud)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(7)

        self.zoom_percent_input = QLineEdit("84")
        self.zoom_percent_input.setObjectName("ZoomInput")
        self.zoom_percent_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_percent_input.setFixedWidth(48)
        self.zoom_percent_input.setValidator(QIntValidator(20, 400, self.zoom_percent_input))
        self.zoom_percent_input.setToolTip("Type a zoom percentage from 20 to 400, then press Enter")
        self.zoom_percent_input.returnPressed.connect(self.apply_typed_zoom)
        self.zoom_percent_input.editingFinished.connect(self.apply_typed_zoom)
        layout.addWidget(self.zoom_percent_input)
        percent_label = QLabel("%")
        layout.addWidget(percent_label)

        self.zoom_hud_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_hud_slider.setRange(20, 400)
        self.zoom_hud_slider.setValue(84)
        self.zoom_hud_slider.setFixedWidth(118)
        self.zoom_hud_slider.setToolTip("Canvas zoom")
        self.zoom_hud_slider.valueChanged.connect(self.set_scale_from_hud)
        layout.addWidget(self.zoom_hud_slider)

        center_button = QPushButton("Center")
        center_button.setObjectName("ZoomFitButton")
        center_button.setToolTip("Recenter the page without changing zoom")
        center_button.clicked.connect(self.canvas.center_page)
        layout.addWidget(center_button)

        width_button = QPushButton("Width")
        width_button.setObjectName("ZoomFitButton")
        width_button.setToolTip("Fit the page width to the canvas")
        width_button.clicked.connect(self.canvas.fit_page_width)
        layout.addWidget(width_button)

        fit_button = QPushButton("Page")
        fit_button.setObjectName("ZoomFitButton")
        fit_button.setToolTip("Fit the entire page to the canvas")
        fit_button.clicked.connect(self.canvas.fit_page)
        layout.addWidget(fit_button)
        hud.setFixedHeight(44)
        hud.adjustSize()
        hud.raise_()
        return hud

    def apply_typed_zoom(self) -> None:
        text = self.zoom_percent_input.text().strip()
        if not text:
            self.update_zoom_hud(self.canvas.current_zoom)
            return
        self.canvas.set_zoom(int(text) / 100.0)

    def set_scale_from_hud(self, percentage: int) -> None:
        self.canvas.set_zoom(percentage / 100.0)

    def update_zoom_hud(self, scale: float) -> None:
        percentage = round(scale * 100)
        self.zoom_percent_input.setText(str(percentage))
        self.zoom_hud_slider.blockSignals(True)
        self.zoom_hud_slider.setValue(percentage)
        self.zoom_hud_slider.blockSignals(False)

    def toggle_ai_sidebar(self, visible: bool) -> None:
        if hasattr(self, "ai_sidebar"):
            self.ai_sidebar.setVisible(visible)

    def configure_local_model(self) -> None:
        endpoint, accepted = QInputDialog.getText(
            self,
            "Local RobustNotes model",
            "Ollama base URL (local machine only):",
            QLineEdit.EchoMode.Normal,
            self.ai_endpoint,
        )
        if not accepted or not endpoint.strip():
            return
        endpoint = endpoint.strip().rstrip("/")
        parsed_endpoint = urlparse(endpoint)
        if parsed_endpoint.scheme != "http" or parsed_endpoint.hostname not in (
            "localhost", "127.0.0.1", "::1"
        ):
            self.append_ai_message(
                "assistant", "Use an Ollama endpoint on this computer, such as http://127.0.0.1:11434."
            )
            return
        model, model_accepted = QInputDialog.getText(
            self,
            "Local RobustNotes model",
            "Installed Ollama model name:",
            QLineEdit.EchoMode.Normal,
            self.ai_model,
        )
        if not model_accepted or not model.strip():
            return
        self.ai_endpoint = endpoint
        self.ai_model = model.strip()
        self.ai_settings.setValue("aiEndpoint", self.ai_endpoint)
        self.ai_settings.setValue("aiModel", self.ai_model)
        self.ai_status_label.setText(f"Local model · {self.ai_model}")

    def append_ai_message(self, role: str, text: str) -> None:
        safe_text = html.escape(text).replace("\n", "<br>")
        label = "You" if role == "user" else "RobustNotes AI"
        css_class = "user" if role == "user" else "assistant"
        self.ai_chat_view.append(
            f'<div class="{css_class}"><b>{label}</b><br>{safe_text}</div>'
        )
        scrollbar = self.ai_chat_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def collect_ai_context(self) -> str:
        parts: list[str] = []
        current_index = self.page_list.currentRow()
        if 0 <= current_index < len(self.pages):
            scene = self.pages[current_index]
            page_name = self.page_list.item(current_index).text()
            parts.append(f"Current notebook page: {page_name}")
            if scene.source_text.strip():
                parts.append(f"Text extracted from this page's PDF:\n{scene.source_text.strip()}")
            if scene.ocr_text.strip():
                parts.append(f"Handwriting recognized from this page:\n{scene.ocr_text.strip()}")
            ink_count = sum(1 for item in scene.items() if item is not scene.bg_item)
            parts.append(f"Current page contains {ink_count} handwritten/drawn annotation item(s).")
        if self.imported_pdf_texts:
            pdf_content = "\n\n".join(
                f"PDF: {name}\n{text}" for name, text in self.imported_pdf_texts if text.strip()
            )
            if pdf_content:
                parts.append("Text extracted from imported PDF pages:\n" + pdf_content)
        if not self.imported_pdf_texts and not parts:
            return "No text could be extracted from this notebook yet. Inspect the attached page image."
        return "\n\n".join(parts) or "No selectable PDF text was found. Inspect the attached page image."

    def recognize_handwriting(self) -> None:
        scene = self.canvas.current_page_scene()
        if scene is None:
            return
        if self.ocr_worker is not None and self.ocr_worker.isRunning():
            return
        selected_items = [
            item for item in scene.selectedItems()
            if item is not scene.bg_item and isinstance(item, QGraphicsPathItem)
        ]
        encoded_image = self.current_page_image_base64(selected_items or None)
        if not encoded_image:
            self.ai_status_label.setText("Could not capture the current page")
            return
        self.ocr_target_scene = scene
        self.ocr_target_items = selected_items
        self.ocr_button.setEnabled(False)
        self.ai_status_label.setText("Preparing page for handwriting recognition…")
        self.ocr_worker = HandwritingOCRWorker(base64.b64decode(encoded_image))
        self.ocr_worker.progress.connect(self.ai_status_label.setText)
        self.ocr_worker.recognized.connect(self.review_ocr_result)
        self.ocr_worker.failed.connect(self.on_ocr_error)
        self.ocr_worker.finished.connect(self.release_ocr_worker)
        retain_background_worker(self.ocr_worker)
        self.ocr_worker.start()

    def review_ocr_result(self, recognized_text: str) -> None:
        scene = getattr(self, "ocr_target_scene", None)
        if scene is None:
            return
        review = QDialog(self)
        review.setWindowTitle("Review handwriting")
        review.setMinimumSize(560, 390)
        review_layout = QVBoxLayout(review)
        review_layout.setContentsMargins(22, 20, 22, 18)
        review_layout.setSpacing(12)

        heading = QLabel("Recognized handwriting")
        heading.setObjectName("AIHeading")
        review_layout.addWidget(heading)
        instruction = QLabel(
            "Correct any recognition errors below. Select handwriting with the lasso before OCR "
            "to replace those strokes with editable cursive text."
        )
        instruction.setWordWrap(True)
        instruction.setObjectName("Muted")
        review_layout.addWidget(instruction)

        editor = QTextEdit()
        editor.setObjectName("OCRCursivePreview")
        normalized_preview = normalize_ocr_text(recognized_text)
        preview_html = html.escape(normalized_preview).replace("\n", "<br>")
        editor.setHtml(
            "<div style=\"font-family:'Segoe Script'; font-size:18pt; color:#24333d;\">"
            f"{preview_html}</div>"
        )
        editor.setPlaceholderText("Recognized text appears here…")
        editor.setToolTip("Editable cursive-style preview of the recognized text")
        review_layout.addWidget(editor, 1)
        review.setStyleSheet(
            "QDialog { background: #171d26; color: #f3f7fb; } "
            "QTextEdit#OCRCursivePreview { background: #f8f5ee; color: #24333d; "
            "border: 1px solid #c8d3d7; border-radius: 12px; padding: 16px; "
            "font-family: 'Segoe Script'; font-size: 20pt; } "
            "QLabel#AIHeading { color: #f3f7fb; font-size: 17px; font-weight: 700; } "
            "QLabel#Muted { color: #9eacbb; } "
            "QPushButton { background: #253241; color: #f3f7fb; border: 1px solid #3c4b5b; "
            "border-radius: 8px; padding: 9px 14px; } "
            "QPushButton:hover { border-color: #48ddef; }"
        )
        cursive_font = QFont("Segoe Script", 18)
        cursive_font.setStyleHint(QFont.StyleHint.Cursive)
        editor.setFont(cursive_font)
        editor.document().setDefaultFont(cursive_font)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.addButton("Save searchable text", QDialogButtonBox.ButtonRole.AcceptRole)
        replace_button = buttons.addButton("Replace selected handwriting", QDialogButtonBox.ButtonRole.ActionRole)
        replace_button.setEnabled(bool(self.ocr_target_items))
        replace_button.setToolTip(
            "Select handwritten strokes with the lasso before running OCR to enable replacement."
        )
        review_choice = {"replace": False}

        def accept_review(replace_selected: bool) -> None:
            review_choice["replace"] = replace_selected
            review.accept()

        save_button.clicked.connect(lambda: accept_review(False))
        replace_button.clicked.connect(lambda: accept_review(True))
        buttons.rejected.connect(review.reject)
        review_layout.addWidget(buttons)

        accepted = review.exec() == QDialog.DialogCode.Accepted
        corrected_text = normalize_ocr_text(editor.toPlainText())
        if accepted and corrected_text:
            scene.ocr_text = corrected_text
            if review_choice["replace"]:
                self.replace_selected_handwriting(scene, self.ocr_target_items, corrected_text)
            self.save_document()
            if review_choice["replace"]:
                self.ai_status_label.setText("Selected handwriting replaced with cursive text")
            else:
                self.ai_status_label.setText("Handwriting text saved to this page")
                self.append_ai_message("assistant", "I added the recognized handwriting to this page's searchable context. You can now ask RobustNotes AI about it.")
        elif accepted:
            scene.ocr_text = ""
            self.save_document()
            self.ai_status_label.setText("Handwriting text cleared")
        else:
            self.ai_status_label.setText("OCR result discarded")
        self.ocr_target_items = ()

    def replace_selected_handwriting(
        self, scene: PageScene, selected_items: Sequence[QGraphicsItem], text: str,
    ) -> None:
        """Replace the lasso-selected ink strokes with an editable cursive text item."""
        selected = [item for item in selected_items if item.scene() is scene]
        if not selected:
            return
        bounds = QRectF(selected[0].sceneBoundingRect())
        for item in selected[1:]:
            bounds = bounds.united(item.sceneBoundingRect())
        bounds = bounds.intersected(scene.page_rect)
        if bounds.isEmpty():
            return

        source_color = next(
            (item.pen().color() for item in selected if isinstance(item, QGraphicsPathItem)),
            QColor("#24333d"),
        )
        font = QFont("Segoe Script")
        font.setStyleHint(QFont.StyleHint.Cursive)
        font.setPointSizeF(max(10.0, min(22.0, bounds.height() * 0.58)))
        text_item = QGraphicsTextItem()
        text_item.setPlainText(text)
        text_item.setFont(font)
        text_item.setDefaultTextColor(source_color)
        text_item.setTextWidth(max(100.0, bounds.width() + 24.0))
        text_item.setPos(bounds.left(), bounds.top())
        text_item.setZValue(2)
        text_item.setData(0, "OCR_CURSIVE_TEXT")
        text_item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )

        for item in selected:
            scene.removeItem(item)
        scene.addItem(text_item)
        scene.push_action("REPLACE", [*selected, text_item])
        self.canvas.stroke_completed.emit()

    def on_ocr_error(self, message: str) -> None:
        self.ai_status_label.setText("Handwriting recognition needs setup or another attempt")
        QMessageBox.information(self, "Handwriting recognition", message)

    def release_ocr_worker(self) -> None:
        self.ocr_button.setEnabled(True)
        if self.ocr_worker is not None:
            self.ocr_worker.deleteLater()
            self.ocr_worker = None

    def current_page_image_base64(
        self, selected_items: Optional[Sequence[QGraphicsItem]] = None,
    ) -> str:
        scene = self.canvas.current_page_scene()
        if scene is None:
            return ""
        page_rect = scene.page_rect
        hidden_items: list[tuple[QGraphicsItem, bool]] = []
        if selected_items:
            selection_bounds = QRectF(selected_items[0].sceneBoundingRect())
            for item in selected_items[1:]:
                selection_bounds = selection_bounds.united(item.sceneBoundingRect())
            page_rect = selection_bounds.adjusted(-36, -36, 36, 36).intersected(scene.page_rect)
            for item in scene.items():
                if item not in selected_items:
                    hidden_items.append((item, item.isVisible()))
                    item.setVisible(False)
        if page_rect.isEmpty():
            for item, was_visible in hidden_items:
                item.setVisible(was_visible)
            return ""
        target_width = min(1100, max(1, round(page_rect.width())))
        target_height = max(1, round(target_width * page_rect.height() / page_rect.width()))
        image = QImage(target_width, target_height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        scene.render(painter, QRectF(0, 0, target_width, target_height), page_rect)
        painter.end()
        for item, was_visible in hidden_items:
            item.setVisible(was_visible)
        temporary_path = ""
        try:
            temporary_directory = (
                self.document_path.parent
                if self.document_path is not None
                else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
            )
            with tempfile.NamedTemporaryFile(
                suffix=".png", dir=str(temporary_directory), delete=False
            ) as image_file:
                temporary_path = image_file.name
            # The .png suffix lets Qt choose the encoder; this also avoids a
            # PySide6 stub/runtime mismatch for QImage.save's format argument.
            if not image.save(temporary_path):
                return ""
            return base64.b64encode(Path(temporary_path).read_bytes()).decode("ascii")
        except OSError:
            return ""
        finally:
            if temporary_path:
                try:
                    os.remove(temporary_path)
                except OSError:
                    pass

    def send_ai_message(self) -> None:
        question = self.chat_input.toPlainText().strip()
        if not question:
            return
        if self.ai_worker is not None and self.ai_worker.isRunning():
            return
        self.chat_input.clear()
        self.append_ai_message("user", question)
        self.ai_history.append({"role": "user", "content": question})
        context = self.collect_ai_context()
        page_image = self.current_page_image_base64()
        history = list(self.ai_history[:-1])
        self.ai_status_label.setText("Reading your notes…")
        self.ai_send_button.setEnabled(False)
        self.ai_worker = LocalAIChatWorker(
            self.ai_endpoint, self.ai_model, history, question, context, page_image
        )
        self.ai_worker.answer_ready.connect(self.on_ai_answer)
        self.ai_worker.request_failed.connect(self.on_ai_error)
        self.ai_worker.finished.connect(self.release_ai_worker)
        retain_background_worker(self.ai_worker)
        self.ai_worker.start()

    def on_ai_answer(self, answer: str) -> None:
        self.ai_history.append({"role": "assistant", "content": answer})
        self.append_ai_message("assistant", answer)
        self.ai_status_label.setText(f"Ready · local model {self.ai_model}")

    def on_ai_error(self, error: str) -> None:
        self.append_ai_message("assistant", error)
        self.ai_status_label.setText("Local model request did not complete")

    def release_ai_worker(self) -> None:
        if self.ai_worker is not None:
            self.ai_worker.deleteLater()
            self.ai_worker = None
        self.ai_send_button.setEnabled(True)

    def update_ai_page_label(self) -> None:
        current = self.page_list.currentRow()
        page_name = self.page_list.item(current).text() if current >= 0 else "No page"
        self.ai_context_label.setText(
            f"Current page: {page_name} · PDF text pages: {len(self.imported_pdf_texts)}"
        )

    def toggle_voice_input(self) -> None:
        if self.audio_source is not None:
            self.stop_voice_input()
            return
        if not HAS_AUDIO_INPUT or QAudioFormat is None or QAudioSource is None or QMediaDevices is None:
            self.append_ai_message(
                "assistant", "Voice capture is unavailable in this PySide6 installation. Install the PySide6 QtMultimedia component to enable microphone input."
            )
            return
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self.append_ai_message("assistant", "No microphone is available. Connect a microphone and try again.")
            return
        audio_format = None
        for sample_rate, channels in ((16_000, 1), (44_100, 1), (48_000, 1), (44_100, 2), (48_000, 2)):
            candidate = QAudioFormat()
            candidate.setSampleRate(sample_rate)
            candidate.setChannelCount(channels)
            candidate.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if device.isFormatSupported(candidate):
                audio_format = candidate
                self.audio_sample_rate = sample_rate
                self.audio_channel_count = channels
                break
        if audio_format is None:
            self.append_ai_message("assistant", "This microphone does not support a compatible PCM recording format.")
            return
        self.audio_samples.clear()
        self.audio_source = QAudioSource(device, audio_format, self)
        self.audio_capture_device = self.audio_source.start()
        if self.audio_capture_device is None:
            self.audio_source = None
            self.append_ai_message("assistant", "The microphone could not be started.")
            return
        self.audio_capture_device.readyRead.connect(self.capture_audio_chunk)
        self.voice_button.setText("■ Stop")
        self.ai_status_label.setText("Listening · up to 60 seconds")
        self.voice_timer.start()

    def capture_audio_chunk(self) -> None:
        if self.audio_capture_device is not None:
            self.audio_samples.extend(bytes(self.audio_capture_device.readAll().data()))

    def stop_voice_input(self) -> None:
        self.voice_timer.stop()
        if self.audio_source is None:
            return
        self.capture_audio_chunk()
        self.audio_source.stop()
        self.audio_source.deleteLater()
        self.audio_source = None
        self.audio_capture_device = None
        self.voice_button.setText("● Voice")
        if len(self.audio_samples) < 1000:
            self.ai_status_label.setText("No speech captured")
            return
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as wav_file:
            wav_file.setnchannels(self.audio_channel_count)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.audio_sample_rate)
            wav_file.writeframes(bytes(self.audio_samples))
        self.ai_status_label.setText("Transcribing voice…")
        self.voice_button.setEnabled(False)
        self.transcription_worker = LocalTranscriptionWorker(audio_buffer.getvalue())
        self.transcription_worker.transcript_ready.connect(self.on_transcription_ready)
        self.transcription_worker.request_failed.connect(self.on_transcription_error)
        self.transcription_worker.finished.connect(self.release_transcription_worker)
        retain_background_worker(self.transcription_worker)
        self.transcription_worker.start()

    def on_transcription_ready(self, transcript: str) -> None:
        existing = self.chat_input.toPlainText().strip()
        self.chat_input.setPlainText(f"{existing} {transcript}".strip())
        self.ai_status_label.setText("Voice transcribed · review and send")

    def on_transcription_error(self, error: str) -> None:
        self.append_ai_message("assistant", error)
        self.ai_status_label.setText("Voice transcription failed")

    def release_transcription_worker(self) -> None:
        if self.transcription_worker is not None:
            self.transcription_worker.deleteLater()
            self.transcription_worker = None
        self.voice_button.setEnabled(True)

    # ----- Theme and UI state -------------------------------------------------

    def apply_theme(self, theme_name: str) -> None:
        if theme_name not in THEMES:
            return
        self.current_theme_name = theme_name
        theme = THEMES[theme_name]
        accent = str(theme["accent"])
        self.canvas.setBackgroundBrush(QColor(str(theme["canvas"])))
        self.canvas.accent_color = QColor(accent)
        self.rebuild_swatches()
        self.zoom_hud.setStyleSheet(
            f"""
            QWidget#ZoomHUD {{ background: {theme['surface']}; border: 1px solid {theme['border']}; border-radius: 13px; }}
            QLineEdit#ZoomInput {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 7px; padding: 4px 2px; font-weight: 800; }}
            QLabel {{ color: {theme['muted']}; font-size: 11px; font-weight: 700; }}
            QPushButton#ZoomFitButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 7px; padding: 5px 9px; font-size: 10px; font-weight: 700; }}
            QPushButton#ZoomFitButton:hover {{ border-color: {accent}; color: {accent}; }}
            QSlider::groove:horizontal {{ height: 4px; background: {theme['border']}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; background: {accent}; border-radius: 6px; }}
            """
        )

        self.toolbar_widget.setStyleSheet(
            f"""
            QWidget#TopToolbar {{ background: {theme['surface']}; border-bottom: 1px solid {theme['border']}; }}
            QLabel {{ color: {theme['text']}; }}
            QLabel#BrandMark {{ background: {accent}; color: #101318; border-radius: 11px; font-size: 22px; font-weight: 900; }}
            QLabel#BrandName {{ font-size: 16px; font-weight: 800; color: {theme['text']}; }}
            QLabel#BrandSubtitle, QLabel#SmallSectionLabel {{ color: {theme['muted']}; font-size: 9px; font-weight: 800; letter-spacing: 1.2px; }}
            QWidget#ToolbarSeparator {{ background: {theme['border']}; }}
            QWidget#ToolDeck {{ background: {theme['surface_alt']}; border: 1px solid {theme['border']}; border-radius: 13px; }}
            QWidget#InkDeck {{ background: {theme['surface_alt']}; border: 1px solid {theme['border']}; border-radius: 10px; }}
            QPushButton#ToolButton {{ background: transparent; color: {theme['muted']}; border: 1px solid transparent; border-radius: 9px; font-size: 11px; font-weight: 700; padding: 3px; }}
            QPushButton#ToolButton:hover {{ background: {theme['surface']}; color: {theme['text']}; }}
            QPushButton#ToolButton:checked {{ background: {accent}; color: #101318; border: 1px solid {accent}; }}
            QPushButton#IconButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 10px; font-size: 18px; font-weight: 700; }}
            QPushButton#IconButton:hover {{ border-color: {accent}; color: {accent}; }}
            QPushButton#IconButton:checked {{ background: {accent}; color: #101318; border-color: {accent}; }}
            QComboBox {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 8px; padding: 7px 10px; }}
            QComboBox QAbstractItemView {{ background: {theme['surface']}; color: {theme['text']}; selection-background-color: {accent}; selection-color: #101318; }}
            QSlider::groove:horizontal {{ height: 4px; background: {theme['border']}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; background: {accent}; border-radius: 6px; }}
            QLabel#ZoomLabel {{ color: {theme['text']}; font-family: Consolas, monospace; font-size: 11px; font-weight: 800; }}
            """
        )
        self.sidebar_widget.setStyleSheet(
            f"""
            QWidget#Sidebar {{ background: {theme['surface']}; border-right: 1px solid {theme['border']}; }}
            QLabel#SidebarHeading {{ color: {theme['text']}; font-size: 11px; font-weight: 900; letter-spacing: 1.5px; }}
            QLabel#PageCount {{ color: {theme['muted']}; font-size: 10px; font-weight: 800; }}
            QLabel#SidebarFooter {{ color: {theme['muted']}; font-size: 10px; padding: 8px 2px 2px 2px; }}
            QPushButton#NewPageButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 9px; padding: 10px; text-align: left; font-weight: 700; }}
            QPushButton#NewPageButton:hover {{ border-color: {accent}; color: {accent}; }}
            QListWidget#PageList {{ background: transparent; border: none; outline: none; color: {theme['text']}; }}
            QListWidget#PageList::item {{ background: {theme['surface_alt']}; border: 1px solid {theme['border']}; border-radius: 10px; padding: 8px; min-height: 126px; color: {theme['text']}; font-size: 11px; font-weight: 700; }}
            QListWidget#PageList::item:selected {{ background: {theme['surface_alt']}; border: 2px solid {accent}; color: {theme['text']}; }}
            QListWidget#PageList::item:hover {{ border-color: {theme['muted']}; }}
            """
        )
        self.ai_sidebar.setStyleSheet(
            f"""
            QWidget#AISidebar {{ background: {theme['surface']}; border-left: 1px solid {theme['border']}; }}
            QLabel#AIHeading {{ color: {theme['text']}; font-size: 16px; font-weight: 800; }}
            QLabel#AIContextLabel, QLabel#AIStatusLabel {{ color: {theme['muted']}; font-size: 10px; }}
            QTextBrowser#AIChatView {{ background: {theme['canvas']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 10px; padding: 8px; font-size: 12px; }}
            QTextBrowser#AIChatView div.assistant {{ background: {theme['surface_alt']}; border-radius: 10px; padding: 9px; margin: 5px 0; }}
            QTextBrowser#AIChatView div.user {{ background: {accent}; color: #101318; border-radius: 10px; padding: 9px; margin: 5px 0; }}
            QTextEdit#AIInput {{ background: {theme['canvas']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 10px; padding: 8px; }}
            QPushButton#AIKeyButton, QPushButton#AIActionButton, QPushButton#AIHideButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 8px; padding: 7px 9px; }}
            QPushButton#AISendButton {{ background: {accent}; color: #101318; border: none; border-radius: 8px; padding: 8px 14px; font-weight: 800; }}
            QPushButton#AIKeyButton:hover, QPushButton#AIActionButton:hover, QPushButton#AIHideButton:hover {{ border-color: {accent}; color: {accent}; }}
            """
        )
        self.ai_chat_view.document().setDefaultStyleSheet(
            f"body {{ color: {theme['text']}; }} "
            f"div.assistant {{ background: {theme['surface_alt']}; color: {theme['text']}; padding: 8px; }} "
            f"div.user {{ background: {accent}; color: #101318; padding: 8px; }}"
        )
        panel_style = (
            f"QWidget#FloatingPanel {{ background: {theme['surface']}; border: 1px solid {theme['border']}; "
            f"border-radius: 13px; color: {theme['text']}; }} "
            f"QWidget#FloatingPanel QLabel {{ color: {theme['text']}; }} "
            f"QWidget#FloatingPanel QLabel#PanelHeading {{ color: {theme['muted']}; font-size: 9px; font-weight: 900; letter-spacing: 1.2px; }} "
            f"QWidget#FloatingPanel QPushButton {{ background: {theme['surface_alt']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: 7px; padding: 7px 9px; font-size: 11px; font-weight: 700; }} "
            f"QWidget#FloatingPanel QPushButton:hover {{ border-color: {accent}; }} "
            f"QWidget#FloatingPanel QPushButton:checked {{ background: {accent}; color: #101318; border-color: {accent}; }} "
            f"QWidget#FloatingPanel QSlider::groove:horizontal {{ height: 4px; background: {theme['border']}; border-radius: 2px; }} "
            f"QWidget#FloatingPanel QSlider::handle:horizontal {{ width: 12px; margin: -5px 0; background: {accent}; border-radius: 6px; }}"
        )
        self.sub_options_widget.setStyleSheet(panel_style)
        self.shape_inspector.setStyleSheet(panel_style)
        self.setStyleSheet(
            f"QWidget {{ font-family: 'Segoe UI'; }} "
            f"QMainWindow {{ background: {theme['canvas']}; }} "
            f"QStatusBar {{ background: {theme['surface']}; color: {theme['muted']}; border-top: 1px solid {theme['border']}; font-size: 10px; padding: 3px 8px; }}"
        )

    def rebuild_swatches(self) -> None:
        while self.swatch_layout.count():
            item = self.swatch_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.swatch_buttons.clear()

        theme = THEMES[self.current_theme_name]
        for color_value in theme["swatches"]:
            color = str(color_value)
            button = QPushButton()
            button.setFixedSize(23, 23)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(color)
            border = "#FFFFFF" if QColor(color) == self.canvas.pen_color else str(theme["border"])
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 2px solid {border}; border-radius: 11px; }} "
                f"QPushButton:hover {{ border: 2px solid {theme['text']}; }}"
            )
            button.clicked.connect(lambda checked=False, selected=color: self.set_color(selected))
            self.swatch_layout.addWidget(button)
            self.swatch_buttons.append(button)

    def update_status(self) -> None:
        mode = self.current_mode_label()
        color = self.canvas.pen_color.name().upper()
        self.statusBar().showMessage(
            f"{mode}  ·  Ink {color}  ·  Page edges locked  ·  Ctrl+Z undo  ·  Space + drag to pan"
        )

    def current_mode_label(self) -> str:
        labels = {
            "PEN": "Pen tool",
            "HIGHLIGHTER": "Highlighter tool",
            "LASER": "Laser tool",
            "ERASER": "Eraser tool",
            "LINE": "Line tool",
            "SHAPE": "Shape tool",
            "LASSO": "Selection tool",
            "TEXT": "Text box tool",
            "RULER": "Ruler / scale",
            "COMPASS": "Compass",
        }
        return labels.get(self.canvas.current_mode, "Ready")

    # ----- Tool selection and contextual options -----------------------------

    def select_tool(self, mode_id: str) -> None:
        if mode_id not in self.tool_buttons:
            return
        self.canvas.current_mode = mode_id
        for mode, button in self.tool_buttons.items():
            selected = mode == mode_id
            button.setChecked(selected)
            button.setIcon(
                self.make_tool_icon(mode, "#101318" if selected else "#D7DEE8")
            )
        self.update_sub_options(mode_id)
        self.canvas.setFocus()
        self.update_status()

    def select_auxiliary_tool(self, mode_id: str) -> None:
        if mode_id not in ("RULER", "COMPASS"):
            return
        self.canvas.current_mode = mode_id
        for mode, button in self.tool_buttons.items():
            button.setChecked(False)
            button.setIcon(self.make_tool_icon(mode, "#D7DEE8"))
        self.update_sub_options(mode_id)
        self.canvas.setFocus()
        self.update_status()

    def _clear_layout(self, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            else:
                child = item.layout()
                if child is None:
                    continue
                while child.count():
                    nested = child.takeAt(0)
                    if nested is not None:
                        nested_widget = nested.widget()
                        if nested_widget is not None:
                            nested_widget.deleteLater()

    def update_sub_options(self, mode_id: str) -> None:
        self._clear_layout(self.sub_options_widget.sub_layout)
        self.stroke_slider = None
        self.stroke_value_label = None
        self.shape_inspector.hide()

        if mode_id in ("PEN", "HIGHLIGHTER", "LASER"):
            title = QLabel("SIZE")
            title.setObjectName("PanelHeading")
            self.sub_options_widget.sub_layout.addWidget(title)
            current = (
                self.canvas.highlighter_width / 6.0
                if mode_id == "HIGHLIGHTER"
                else self.canvas.pen_width
            )
            self.stroke_value_label = QLabel(f"{round(current)} px")
            self.stroke_value_label.setMinimumWidth(38)
            self.sub_options_widget.sub_layout.addWidget(self.stroke_value_label)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 20)
            slider.setValue(max(1, min(20, round(current))))
            slider.valueChanged.connect(self.on_size_changed)
            self.stroke_slider = slider
            slider.setFixedWidth(118)
            self.sub_options_widget.sub_layout.addWidget(slider)
            self.sub_options_widget.setFixedSize(220, 52)
            self.sub_options_widget.show()

        elif mode_id == "LASSO":
            title = QLabel("LASSO")
            title.setObjectName("PanelHeading")
            self.sub_options_widget.sub_layout.addWidget(title)
            for label, submode in (("Freeform", "FREE"), ("Rectangle", "RECT")):
                button = QPushButton(label)
                button.setCheckable(True)
                button.setChecked(self.canvas.lasso_submode == submode)
                button.clicked.connect(lambda checked=False, selected=submode: self.set_lasso_submode(selected))
                self.sub_options_widget.sub_layout.addWidget(button)
            self.sub_options_widget.setFixedSize(230, 52)
            self.sub_options_widget.show()

        elif mode_id == "SHAPE":
            self.shape_inspector.size_slider.setValue(max(1, min(20, round(self.canvas.pen_width))))
            self.shape_inspector.show()

    def set_pen_width(self, width: float) -> None:
        if self.canvas.current_mode == "HIGHLIGHTER":
            self.canvas.highlighter_width = width * 6.0
        else:
            self.canvas.pen_width = width
        if self.stroke_slider is not None:
            self.stroke_slider.blockSignals(True)
            self.stroke_slider.setValue(max(1, min(20, round(width))))
            self.stroke_slider.blockSignals(False)
        if self.stroke_value_label is not None:
            self.stroke_value_label.setText(f"{round(width)} px")

    def on_size_changed(self, value: int) -> None:
        self.set_pen_width(float(value))

    def set_lasso_submode(self, submode: str) -> None:
        self.canvas.lasso_submode = submode
        for index in range(self.sub_options_widget.sub_layout.count()):
            item = self.sub_options_widget.sub_layout.itemAt(index)
            direct_button = item.widget() if item else None
            if isinstance(direct_button, QPushButton):
                direct_button.setChecked(
                    direct_button.text() == ("Freeform" if submode == "FREE" else "Rectangle")
                )
            layout = item.layout() if item else None
            if layout:
                for child_index in range(layout.count()):
                    widget_item = layout.itemAt(child_index)
                    button = widget_item.widget() if widget_item else None
                    if isinstance(button, QPushButton):
                        button.setChecked(button.text() == ("Freeform" if submode == "FREE" else "Rectangle"))

    def set_color(self, color_value: str) -> None:
        color = QColor(color_value)
        if not color.isValid():
            return
        self.canvas.pen_color = color
        for scene in self.pages:
            for item in scene.selectedItems():
                if isinstance(item, CanvasTextItem):
                    item.set_foreground_color(color)
        self.rebuild_swatches()
        self.update_status()

    def open_color_dialog(self) -> None:
        color = QColorDialog.getColor(self.canvas.pen_color, self, "Choose ink colour")
        if color.isValid():
            self.set_color(color.name(QColor.NameFormat.HexArgb))

    # ----- Page management ----------------------------------------------------

    def add_page(self, pixmap: Optional[QPixmap] = None, title: Optional[str] = None,
                 source_text: str = "", source_name: str = "") -> None:
        page_pixmap = pixmap if pixmap and not pixmap.isNull() else self.template_pixmap
        scene = PageScene(page_pixmap, self, source_text, source_name)
        self.pages.append(scene)
        page_number = len(self.pages)
        item = QListWidgetItem(title or f"Page {page_number}")
        item.setIcon(self.render_scene_thumbnail(scene))
        item.setSizeHint(QSize(198, 142))
        self.page_list.addItem(item)
        self.page_list.setCurrentRow(page_number - 1)
        self.update_page_count()
        if hasattr(self, "ai_context_label"):
            self.update_ai_page_label()

    def add_new_page(self) -> None:
        self.add_page()
        self.save_document()

    def switch_page(self, index: int) -> None:
        if 0 <= index < len(self.pages):
            self.canvas.set_page_scene(self.pages[index])
            self.update_page_count()
            self.update_status()
            self.update_ai_page_label()

    def on_page_rows_moved(self, _source_parent, source_start: int, source_end: int, _destination_parent, destination_row: int) -> None:
        if source_start != source_end or not (0 <= source_start < len(self.pages)):
            return
        destination_index = destination_row - 1 if destination_row > source_start else destination_row
        destination_index = max(0, min(destination_index, len(self.pages) - 1))
        if destination_index == source_start:
            return
        page = self.pages.pop(source_start)
        self.pages.insert(destination_index, page)
        self.update_page_count()
        self.save_document()

    def update_page_count(self) -> None:
        current = max(0, self.page_list.currentRow() + 1)
        self.page_count_label.setText(f"{current:02d} / {len(self.pages):02d}")

    def render_scene_thumbnail(self, scene: PageScene) -> QIcon:
        thumbnail = QPixmap(178, 104)
        thumbnail.fill(QColor("#E9EBEF"))
        painter = QPainter(thumbnail)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(painter, QRectF(0, 0, 178, 104), scene.page_rect)
        painter.end()
        return QIcon(thumbnail)

    def update_current_thumbnail(self) -> None:
        index = self.page_list.currentRow()
        if 0 <= index < len(self.pages):
            self.page_list.item(index).setIcon(self.render_scene_thumbnail(self.pages[index]))

    def add_text_box(self, scene_pos: QPointF, begin_edit: bool = False,
                     text: str = "") -> Optional[CanvasTextItem]:
        scene = self.canvas.current_page_scene()
        if scene is None:
            return None
        item = CanvasTextItem(text)
        item.setFont(QFont("Segoe UI", 20))
        item.setDefaultTextColor(QColor("#172334"))
        page = scene.page_rect.adjusted(14, 14, -14, -14)
        x = min(max(scene_pos.x(), page.left()), page.right())
        item.setTextWidth(min(480.0, max(40.0, page.right() - x)))
        item_bounds = item.boundingRect()
        x = min(x, max(page.left(), page.right() - item_bounds.width()))
        y = min(max(scene_pos.y(), page.top()), max(page.top(), page.bottom() - item_bounds.height()))
        item.setPos(x, y)
        item.setZValue(3)
        item.setData(0, "CANVAS_TEXT")
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        item.content_changed.connect(self.schedule_document_save)
        scene.addItem(item)
        scene.clearSelection()
        item.setSelected(True)
        scene.push_action("ADD", [item])
        if begin_edit:
            item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            item.setFocus(Qt.FocusReason.MouseFocusReason)
        self.canvas.stroke_completed.emit()
        return item

    def add_image_to_page(self, pixmap: QPixmap) -> bool:
        scene = self.canvas.current_page_scene()
        if scene is None or pixmap.isNull():
            return False
        if pixmap.width() * pixmap.height() > 40_000_000:
            QMessageBox.warning(self, "Image is too large", "Choose an image smaller than 40 megapixels.")
            return False
        image = ResizablePixmapItem(pixmap)
        image_width = max(1, pixmap.width())
        image_height = max(1, pixmap.height())
        page = scene.page_rect.adjusted(24, 24, -24, -24)
        image.setScale(min(
            1.0,
            720.0 / image_width,
            560.0 / image_height,
            page.width() / image_width,
            page.height() / image_height,
        ))
        center = scene.page_rect.center()
        image.setPos(
            center.x() - image_width * image.scale() / 2.0,
            center.y() - image_height * image.scale() / 2.0,
        )
        image.setZValue(2)
        image.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        scene.addItem(image)
        scene.clearSelection()
        image.setSelected(True)
        scene.push_action("ADD", [image])
        self.select_tool("LASSO")
        self.canvas.stroke_completed.emit()
        self.statusBar().showMessage(
            "Image added · Select it with the lasso, then drag the corner handle to resize", 5000
        )
        return True

    def load_canvas_image_file(self, file_path: str) -> QPixmap:
        reader = QImageReader(file_path)
        dimensions = reader.size()
        if dimensions.isValid() and dimensions.width() * dimensions.height() > 40_000_000:
            QMessageBox.warning(self, "Image is too large", "Choose an image smaller than 40 megapixels.")
            return QPixmap()
        image = reader.read()
        return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

    def import_canvas_image(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Insert image on page",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff);;All files (*)",
        )
        if file_path:
            pixmap = self.load_canvas_image_file(file_path)
            if not self.add_image_to_page(pixmap):
                if pixmap.isNull():
                    QMessageBox.warning(self, "Could not open image", "The selected file is not a supported image.")

    def paste_clipboard_content(self) -> bool:
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None:
            return False
        if mime.hasImage():
            image_data = mime.imageData()
            if isinstance(image_data, QPixmap):
                pixmap = QPixmap(image_data)
            elif isinstance(image_data, QImage):
                pixmap = QPixmap.fromImage(image_data)
            else:
                pixmap = QPixmap.fromImage(QImage(image_data))
            if self.add_image_to_page(pixmap):
                return True
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    pixmap = self.load_canvas_image_file(url.toLocalFile())
                    if not pixmap.isNull():
                        return self.add_image_to_page(pixmap)
        if mime.hasText() and mime.text().strip():
            center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
            self.add_text_box(center, text=mime.text())
            return True
        return False

    def schedule_thumbnail_update(self) -> None:
        if hasattr(self, "thumbnail_timer"):
            self.thumbnail_timer.start()

    def schedule_document_save(self) -> None:
        if self.document_path is not None and not self.is_loading_document:
            self.document_save_timer.start()

    def rename_page(self, item: QListWidgetItem) -> None:
        index = self.page_list.row(item)
        name, accepted = QInputDialog.getText(self, "Rename page", "Page name:", text=item.text())
        if accepted and name.strip():
            item.setText(name.strip())
            self.update_page_count()
            self.save_document()

    def show_page_context_menu(self, position) -> None:
        item = self.page_list.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        rename_action = menu.addAction("Rename page")
        duplicate_action = None
        clear_action = None
        ruling_action = None
        study_action = None
        export_image_action = None
        if self.document_type == "notebook":
            duplicate_action = menu.addAction("Duplicate page")
            clear_action = menu.addAction("Clear page contents")
            if not self.pages[self.page_list.row(item)].source_name:
                ruling_action = menu.addAction("Change page layout…")
            menu.addSeparator()
            study_action = menu.addAction("Study selected page(s)…")
            export_image_action = menu.addAction("Export page as image…")
        delete_action = menu.addAction("Delete page")
        chosen = menu.exec(self.page_list.mapToGlobal(position))
        if chosen == rename_action:
            self.rename_page(item)
        elif duplicate_action is not None and chosen == duplicate_action:
            self.duplicate_page(self.page_list.row(item))
        elif clear_action is not None and chosen == clear_action:
            self.clear_page(self.page_list.row(item))
        elif ruling_action is not None and chosen == ruling_action:
            self.change_page_layout(self.page_list.row(item))
        elif study_action is not None and chosen == study_action:
            self.study_page(self.page_list.row(item))
        elif export_image_action is not None and chosen == export_image_action:
            self.export_page_image(self.page_list.row(item))
        elif chosen == delete_action:
            self.delete_page(self.page_list.row(item))

    def change_page_layout(self, index: int) -> None:
        if self.document_type != "notebook" or not (0 <= index < len(self.pages)):
            return
        choices = ["Dot grid", "Cornell notes", "Meeting notes", "Weekly planner"]
        current = self.page_list.item(index).text()
        selected, accepted = QInputDialog.getItem(self, "Change page layout", "Layout:", choices, 0, False)
        if not accepted:
            return
        scene = self.pages[index]
        background = self.make_notebook_template(self.load_or_create_template(), selected)
        scene.template_pixmap = QPixmap(background)
        scene.bg_item.setPixmap(background)
        if self.page_list.currentRow() == index:
            self.update_current_thumbnail()
        else:
            self.page_list.item(index).setIcon(self.render_scene_thumbnail(scene))
        self.save_document()

    def export_page_image(self, index: int) -> None:
        if not (0 <= index < len(self.pages)):
            return
        suggested = f"{self.page_list.item(index).text()}.png"
        filename, _ = QFileDialog.getSaveFileName(self, "Export page image", suggested, "PNG image (*.png)")
        if not filename:
            return
        if not filename.lower().endswith(".png"):
            filename += ".png"
        scene = self.pages[index]
        image = QImage(round(scene.page_rect.width()), round(scene.page_rect.height()), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#FFFFFF"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(painter, QRectF(0, 0, image.width(), image.height()), scene.page_rect)
        painter.end()
        if not image.save(filename):
            QMessageBox.critical(self, "Export failed", "The page image could not be saved.")

    def study_page(self, index: int) -> None:
        if self.document_type != "notebook" or not (0 <= index < len(self.pages)):
            return
        selected_rows = sorted({self.page_list.row(selected) for selected in self.page_list.selectedItems()})
        if index not in selected_rows:
            selected_rows = [index]
        source = " ".join(
            part
            for row in selected_rows
            for part in (self.pages[row].source_text, self.pages[row].ocr_text)
            if part
        ).strip()
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", source) if len(part.split()) >= 7]
        if not sentences:
            QMessageBox.information(self, "No study text yet", "The selected page(s) have no extracted text. Import a text PDF or run handwriting recognition first.")
            return
        cards = []
        for sentence in sentences[:30]:
            words = sentence.split()
            answer = " ".join(words[:min(3, max(1, len(words) // 5))])
            prompt = sentence.replace(answer, "________", 1)
            cards.append((prompt, answer))

        dialog = QDialog(self)
        dialog.setWindowTitle("Study this page")
        dialog.resize(620, 360)
        layout = QVBoxLayout(dialog)
        progress = QLabel("")
        progress.setObjectName("Muted")
        layout.addWidget(progress)
        prompt_label = QLabel("")
        prompt_label.setWordWrap(True)
        prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_label.setStyleSheet("font-size:22px; font-weight:650; padding:24px")
        layout.addWidget(prompt_label, 1)
        answer_label = QLabel("")
        answer_label.setWordWrap(True)
        answer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        answer_label.setStyleSheet("font-size:18px; color:#58E0E7")
        layout.addWidget(answer_label)
        controls = QHBoxLayout()
        reveal = QPushButton("Reveal answer")
        previous = QPushButton("Previous")
        next_button = QPushButton("Next card")
        controls.addWidget(previous)
        controls.addStretch(1)
        controls.addWidget(reveal)
        controls.addWidget(next_button)
        layout.addLayout(controls)
        state = {"index": 0}

        def show_card():
            card_index = state["index"]
            progress.setText(f"CARD {card_index + 1:02d}  /  {len(cards):02d}")
            prompt_label.setText(cards[card_index][0])
            answer_label.clear()
        def show_answer():
            answer_label.setText(cards[state["index"]][1])
        def move(delta: int):
            state["index"] = (state["index"] + delta) % len(cards)
            show_card()
        reveal.clicked.connect(show_answer)
        previous.clicked.connect(lambda: move(-1))
        next_button.clicked.connect(lambda: move(1))
        show_card()
        dialog.setStyleSheet(WORKSPACE_STYLE)
        dialog.exec()

    def duplicate_page(self, index: int) -> None:
        if self.document_type != "notebook" or not (0 <= index < len(self.pages)):
            return
        source = self.pages[index]
        base_title = self.page_list.item(index).text()
        title = f"{base_title} (copy)"
        copy_number = 2
        existing_titles = {self.page_list.item(i).text() for i in range(self.page_list.count())}
        while title in existing_titles:
            title = f"{base_title} (copy {copy_number})"
            copy_number += 1

        self.add_page(
            QPixmap(source.bg_item.pixmap()),
            title,
            source.source_text,
            source.source_name,
        )
        duplicate = self.pages[-1]
        duplicate.ocr_text = source.ocr_text
        for item in source.items(Qt.SortOrder.AscendingOrder):
            if item is source.bg_item:
                continue
            if isinstance(item, QGraphicsPathItem):
                copied_item = QGraphicsPathItem(item.path())
                copied_item.setPen(QPen(item.pen()))
                copied_item.setPos(item.pos())
                copied_item.setZValue(item.zValue())
                copied_item.setData(0, item.data(0))
                copied_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
                if item.data(0) == "LASER":
                    glow = QGraphicsDropShadowEffect()
                    glow.setBlurRadius(25)
                    glow.setColor(item.pen().color())
                    glow.setOffset(0, 0)
                    copied_item.setGraphicsEffect(glow)
            elif isinstance(item, ResizablePixmapItem):
                copied_item = ResizablePixmapItem(QPixmap(item.pixmap()))
                copied_item.setScale(item.scale())
                copied_item.setPos(item.pos())
                copied_item.setZValue(item.zValue())
            elif isinstance(item, CanvasTextItem):
                copied_item = CanvasTextItem()
                copied_item.setHtml(item.toHtml())
                copied_item.setFont(QFont(item.font()))
                copied_item.setDefaultTextColor(item.defaultTextColor())
                copied_item.setTextWidth(item.textWidth())
                copied_item.setPos(item.pos())
                copied_item.setZValue(item.zValue())
                copied_item.setData(0, "CANVAS_TEXT")
                copied_item.content_changed.connect(self.schedule_document_save)
            elif isinstance(item, QGraphicsTextItem) and item.data(0) == "OCR_CURSIVE_TEXT":
                copied_item = QGraphicsTextItem(item.toPlainText())
                copied_item.setFont(QFont(item.font()))
                copied_item.setDefaultTextColor(item.defaultTextColor())
                copied_item.setTextWidth(item.textWidth())
                copied_item.setPos(item.pos())
                copied_item.setZValue(item.zValue())
                copied_item.setData(0, "OCR_CURSIVE_TEXT")
            else:
                continue
            copied_item.setFlags(
                QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                | (QGraphicsItem.GraphicsItemFlag.ItemIsFocusable if isinstance(copied_item, CanvasTextItem) else QGraphicsItem.GraphicsItemFlag(0))
            )
            duplicate.addItem(copied_item)

        self.update_current_thumbnail()
        self.save_document()

    def clear_page(self, index: int) -> None:
        if self.document_type != "notebook" or not (0 <= index < len(self.pages)):
            return
        page_title = self.page_list.item(index).text()
        answer = QMessageBox.question(
            self,
            "Clear page contents",
            f"Remove all handwriting and drawings from {page_title}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        scene = self.pages[index]
        for item in scene.items():
            if item is not scene.bg_item:
                scene.removeItem(item)
        scene.undo_stack.clear()
        scene.redo_stack.clear()
        scene.ocr_text = ""
        if self.page_list.currentRow() == index:
            self.update_current_thumbnail()
            self.update_status()
            self.update_ai_page_label()
        else:
            self.page_list.item(index).setIcon(self.render_scene_thumbnail(scene))
        self.save_document()

    def delete_page(self, index: int) -> None:
        if len(self.pages) <= 1:
            QMessageBox.information(self, "Keep one page", "A notebook must contain at least one page.")
            return
        if not (0 <= index < len(self.pages)):
            return
        answer = QMessageBox.question(
            self,
            "Delete page",
            f"Delete {self.page_list.item(index).text()}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed_scene = self.pages.pop(index)
        self.page_list.takeItem(index)
        new_index = min(index, len(self.pages) - 1)
        self.page_list.setCurrentRow(new_index)
        if self.canvas.scene() is removed_scene:
            self.canvas.set_page_scene(self.pages[new_index])
        removed_scene.deleteLater()
        self.update_page_count()
        self.save_document()

    # ----- Undo and redo ------------------------------------------------------

    def undo_current(self) -> None:
        scene = self.canvas.current_page_scene()
        if scene and scene.undo():
            self.schedule_thumbnail_update()
            self.schedule_document_save()
            self.statusBar().showMessage("Undo", 1200)

    def redo_current(self) -> None:
        scene = self.canvas.current_page_scene()
        if scene and scene.redo():
            self.schedule_thumbnail_update()
            self.schedule_document_save()
            self.statusBar().showMessage("Redo", 1200)

    # ----- Import/export and menus --------------------------------------------

    def show_more_menu(self) -> None:
        menu = QMenu(self)
        theme = THEMES[self.current_theme_name]
        accent = str(theme["accent"])
        menu.setStyleSheet(
            f"QMenu {{ background: {theme['surface']}; color: {theme['text']}; border: 1px solid {theme['border']}; padding: 5px; }}"
            f"QMenu::item {{ padding: 8px 24px; border-radius: 5px; }}"
            f"QMenu::item:selected {{ background: {accent}; color: #101318; }}"
            f"QMenu::separator {{ height: 1px; background: {theme['border']}; margin: 4px 8px; }}"
        )
        undo_action = menu.addAction("Undo     Ctrl+Z")
        redo_action = menu.addAction("Redo     Ctrl+Y")
        menu.addSeparator()
        tools_menu = menu.addMenu("Tools")
        calculator_action = tools_menu.addAction("Calculator…")
        ruler_action = tools_menu.addAction("Ruler / scale")
        ruler_action.setToolTip("Drag on the page to draw a straight, tick-marked guide")
        compass_action = tools_menu.addAction("Compass")
        compass_action.setToolTip("Drag from the circle centre to set its radius")
        text_box_action = tools_menu.addAction("Add text box")
        image_action = tools_menu.addAction("Insert image…")
        menu.addSeparator()
        fit_action = menu.addAction("Fit page to window")
        focus_action = menu.addAction("Toggle focus mode     F11")
        import_action = menu.addAction("Import PDF…")
        export_action = menu.addAction("Export notebook as PDF…")
        music_action = menu.addAction("Open music player…")
        menu.addSeparator()
        delete_action = menu.addAction("Delete current page")
        menu.addSeparator()
        updates_action = menu.addAction(f"Check for updates…  ·  v{APP_VERSION}")
        sender = self.sender()
        if not isinstance(sender, QWidget):
            return
        chosen = menu.exec(sender.mapToGlobal(sender.rect().bottomLeft()))

        if chosen == undo_action:
            self.undo_current()
        elif chosen == redo_action:
            self.redo_current()
        elif chosen == calculator_action:
            if self.calculator_dialog is None:
                self.calculator_dialog = CalculatorDialog(self, THEMES[self.current_theme_name])
            self.calculator_dialog.show()
            self.calculator_dialog.raise_()
            self.calculator_dialog.activateWindow()
        elif chosen == ruler_action:
            self.select_auxiliary_tool("RULER")
        elif chosen == compass_action:
            self.select_auxiliary_tool("COMPASS")
        elif chosen == text_box_action:
            self.select_tool("TEXT")
        elif chosen == image_action:
            self.import_canvas_image()
        elif chosen == fit_action:
            self.canvas.fit_page()
        elif chosen == focus_action:
            self.toggle_focus_mode()
        elif chosen == import_action:
            self.import_pdf()
        elif chosen == export_action:
            self.export_notebook_to_pdf()
        elif chosen == music_action:
            self.open_music_player()
        elif chosen == delete_action:
            self.delete_page(self.page_list.currentRow())
        elif chosen == updates_action:
            self.check_for_updates()

    def check_for_updates(self) -> None:
        if self.update_check_worker is not None and self.update_check_worker.isRunning():
            self.statusBar().showMessage("Already checking for updates…", 2500)
            return
        worker = UpdateCheckWorker(APP_VERSION)
        self.update_check_worker = worker
        worker.update_available.connect(self.offer_update)
        worker.up_to_date.connect(self.on_update_check_complete)
        worker.request_failed.connect(self.on_update_check_failed)
        worker.finished.connect(lambda: setattr(self, "update_check_worker", None))
        retain_background_worker(worker)
        self.statusBar().showMessage("Checking for RobustNotes updates…", 5000)
        worker.start()

    def on_update_check_complete(self) -> None:
        self.statusBar().showMessage(f"RobustNotes v{APP_VERSION} is up to date.", 5000)

    def on_update_check_failed(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)

    def offer_update(self, version: str, url: str, digest: str) -> None:
        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self,
                "Update available",
                f"RobustNotes {version} is available. This development copy cannot update itself; open the GitHub release page to download it.",
            )
            QDesktopServices.openUrl(QUrl(f"https://github.com/{UPDATE_REPOSITORY}/releases/latest"))
            return
        answer = QMessageBox.question(
            self,
            "RobustNotes update available",
            f"Version {version} is ready to download and install.\n\nThe update runs quietly in place and keeps your local profiles and notes. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = UpdateDownloadWorker(version, url, digest)
        self.update_download_worker = worker
        worker.installer_ready.connect(self.install_downloaded_update)
        worker.request_failed.connect(self.on_update_download_failed)
        worker.finished.connect(lambda: setattr(self, "update_download_worker", None))
        retain_background_worker(worker)
        self.statusBar().showMessage(f"Downloading RobustNotes {version}…", 0)
        worker.start()

    def on_update_download_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Update failed", message)

    def install_downloaded_update(self, installer_path: str) -> None:
        if not getattr(sys, "frozen", False) or sys.platform != "win32":
            QMessageBox.information(self, "Update downloaded", f"The installer is ready at:\n{installer_path}")
            return
        app_path = Path(sys.executable).resolve()
        if not app_path.is_file():
            QMessageBox.warning(self, "Update could not start", "The installed RobustNotes launcher could not be found.")
            return
        def ps_quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"
        script_path = Path(tempfile.gettempdir()) / f"robustnotes-update-{os.getpid()}.ps1"
        installer = str(Path(installer_path).resolve())
        script = (
            f"$ErrorActionPreference = 'Stop'\n"
            f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue\n"
            f"$installer = {ps_quote(installer)}\n"
            f"$app = {ps_quote(str(app_path))}\n"
            f"Start-Process -FilePath $installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS' -Wait\n"
            f"if (Test-Path -LiteralPath $app) {{ Start-Process -FilePath $app }}\n"
            f"Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue\n"
            f"Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n"
        )
        try:
            script_path.write_text(script, encoding="utf-8")
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            QMessageBox.warning(self, "Update could not start", f"Could not launch the installer helper.\n\n{error}")
            return
        self.statusBar().showMessage("Installing the update. RobustNotes will restart shortly…", 5000)
        workspace = self.window()
        if isinstance(workspace, WorkspaceWindow):
            workspace._allow_exit_without_confirmation = True
        QTimer.singleShot(250, QApplication.closeAllWindows)

    def open_music_player(self) -> None:
        if self.music_dialog is None:
            self.music_dialog = FunkwhaleMusicPlayerDialog(self, self.ai_settings)
        self.music_dialog.setModal(False)
        self.music_dialog.show()
        self.music_dialog.raise_()
        self.music_dialog.activateWindow()

    def import_pdf(self) -> None:
        if not HAS_PDF_SUPPORT or QPdfDocument is None:
            QMessageBox.warning(
                self,
                "PDF support unavailable",
                "This PySide6 installation does not include QtPdf. Install a PySide6 build with QtPdf support to import PDFs.",
            )
            return

        file_path, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF documents (*.pdf)")
        if not file_path:
            return

        document = QPdfDocument()
        result = document.load(file_path)
        if result != QPdfDocument.Error.None_:
            QMessageBox.critical(self, "Could not open PDF", "The selected PDF could not be opened.")
            return

        imported = 0
        for page_index in range(document.pageCount()):
            page_size = document.pagePointSize(page_index)
            if page_size.width() <= 0 or page_size.height() <= 0:
                continue
            width = 1920
            height = max(1, round(width * page_size.height() / page_size.width()))
            image = document.render(page_index, QSize(width, height))
            if image.isNull():
                continue
            pixmap = QPixmap.fromImage(image)
            selection = document.getAllText(page_index)
            page_text = selection.text().strip() if selection.isValid() else ""
            source_name = os.path.basename(file_path)
            if page_text:
                self.imported_pdf_texts.append((f"{source_name}, page {page_index + 1}", page_text))
            self.add_page(
                pixmap,
                f"{source_name} · {page_index + 1}",
                source_text=page_text,
                source_name=source_name,
            )
            imported += 1

        if imported:
            self.update_ai_page_label()
            self.save_document()
            self.statusBar().showMessage(f"Imported {imported} PDF page(s)", 3500)
        else:
            QMessageBox.warning(self, "No pages imported", "The PDF did not contain any pages that could be rendered.")

    def export_notebook_to_pdf(self) -> None:
        if not self.pages:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export notebook",
            "RobustNotes.pdf",
            "PDF documents (*.pdf)",
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        first_rect = self.pages[0].page_rect
        page_width_pt = max(1.0, first_rect.width() * 0.75)
        page_height_pt = max(1.0, first_rect.height() * 0.75)

        painter: Optional[QPainter] = None
        try:
            writer = QPdfWriter(file_path)
            writer.setPageSize(
                QPageSize(QSizeF(page_width_pt, page_height_pt), QPageSize.Unit.Point)
            )
            writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)
            writer.setResolution(300)

            painter = QPainter(writer)
            if not painter.isActive():
                raise RuntimeError("The PDF output file could not be opened for writing.")
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            target = QRectF(0, 0, float(writer.width()), float(writer.height()))
            for index, scene in enumerate(self.pages):
                if index > 0 and not writer.newPage():
                    raise RuntimeError("Could not create the next PDF page.")

                page_rect = scene.page_rect
                source_ratio = page_rect.width() / max(page_rect.height(), 1.0)
                target_ratio = target.width() / max(target.height(), 1.0)
                if source_ratio > target_ratio:
                    dest_width = target.width()
                    dest_height = dest_width / source_ratio
                else:
                    dest_height = target.height()
                    dest_width = dest_height * source_ratio
                destination = QRectF(
                    target.center().x() - dest_width / 2,
                    target.center().y() - dest_height / 2,
                    dest_width,
                    dest_height,
                )

                selected_items = scene.selectedItems()
                scene.clearSelection()
                try:
                    painter.fillRect(target, QColor("#FFFFFF"))
                    scene.render(painter, destination, page_rect)
                finally:
                    for item in selected_items:
                        if item.scene() is scene:
                            item.setSelected(True)

            painter.end()
            painter = None
            QMessageBox.information(self, "Export complete", f"Notebook exported successfully to:\n{file_path}")
        except Exception as error:  # Keep the UI responsive and report the failure.
            QMessageBox.critical(self, "Export failed", f"The notebook could not be exported.\n\n{error}")
        finally:
            if painter is not None and painter.isActive():
                painter.end()

    def save_document(self) -> None:
        if hasattr(self, "document_save_timer") and self.document_save_timer.isActive():
            self.document_save_timer.stop()
        if self.document_path is None or self.is_loading_document:
            return
        if self.document_path.exists():
            try:
                self.create_recovery_snapshot()
            except OSError as error:
                self.statusBar().showMessage(f"Recovery snapshot skipped: {error}", 4000)
        try:
            self.document_path.parent.mkdir(parents=True, exist_ok=True)
            asset_dir = self.document_path.with_name(f"{self.document_path.stem}_assets")
            asset_dir.mkdir(parents=True, exist_ok=True)
            page_data: list[dict[str, object]] = []
            for page_index, scene in enumerate(self.pages):
                image_name = f"page_{page_index + 1:04d}.png"
                image_path = asset_dir / image_name
                image_key = scene.template_pixmap.cacheKey()
                if not image_path.exists() or self._saved_page_image_keys.get(image_path) != image_key:
                    if not scene.template_pixmap.save(str(image_path)):
                        raise OSError(f"Could not save notebook page background: {image_name}")
                    self._saved_page_image_keys[image_path] = image_key
                drawings: list[dict[str, object]] = []
                image_number = 0
                for item in scene.items(Qt.SortOrder.AscendingOrder):
                    if item is scene.bg_item:
                        continue
                    position = item.pos()
                    if isinstance(item, ResizablePixmapItem):
                        image_number += 1
                        image_name = f"page_{page_index + 1:04d}_item_{image_number:04d}.png"
                        image_path = asset_dir / image_name
                        image_key = item.pixmap().cacheKey()
                        if (
                            not image_path.exists()
                            or self._saved_canvas_image_keys.get(image_path) != image_key
                        ):
                            if not item.pixmap().save(str(image_path), "PNG"):
                                raise OSError(f"Could not save a pasted image: {image_name}")
                            self._saved_canvas_image_keys[image_path] = image_key
                        drawings.append({
                            "kind": "image",
                            "image": str(image_path.relative_to(self.document_path.parent)),
                            "scale": item.scale(),
                            "x": position.x(),
                            "y": position.y(),
                            "z": item.zValue(),
                        })
                    elif isinstance(item, QGraphicsPathItem):
                        path = item.path()
                        elements = [
                            [path.elementAt(index).type.value, path.elementAt(index).x, path.elementAt(index).y]
                            for index in range(path.elementCount())
                        ]
                        pen = item.pen()
                        drawings.append({
                            "kind": "path",
                            "elements": elements,
                            "color": pen.color().name(QColor.NameFormat.HexArgb),
                            "width": pen.widthF(),
                            "mode": str(item.data(0) or "PEN"),
                            "x": position.x(),
                            "y": position.y(),
                            "z": item.zValue(),
                        })
                    elif isinstance(item, CanvasTextItem):
                        font = item.font()
                        drawings.append({
                            "kind": "canvas-text",
                            "text": item.toPlainText(),
                            "html": item.toHtml(),
                            "font_family": font.family(),
                            "font_size": font.pointSizeF(),
                            "color": item.defaultTextColor().name(QColor.NameFormat.HexArgb),
                            "text_width": item.textWidth(),
                            "x": position.x(),
                            "y": position.y(),
                            "z": item.zValue(),
                        })
                    elif isinstance(item, QGraphicsTextItem) and item.data(0) == "OCR_CURSIVE_TEXT":
                        font = item.font()
                        drawings.append({
                            "kind": "text",
                            "text": item.toPlainText(),
                            "font_family": font.family(),
                            "font_size": font.pointSizeF(),
                            "color": item.defaultTextColor().name(QColor.NameFormat.HexArgb),
                            "text_width": item.textWidth(),
                            "x": position.x(),
                            "y": position.y(),
                            "z": item.zValue(),
                        })
                page_data.append({
                    "title": self.page_list.item(page_index).text(),
                    "image": str(image_path.relative_to(self.document_path.parent)),
                    "source_text": scene.source_text,
                    "source_name": scene.source_name,
                    "ocr_text": scene.ocr_text,
                    "drawings": drawings,
                })
            payload = {
                "format": "robustnotes-document",
                "version": 1,
                "type": self.document_type,
                "title": self.document_title,
                "template": self.template_name,
                "updated": datetime.now().isoformat(timespec="seconds"),
                "pages": page_data,
            }
            temporary_path = self.document_path.with_suffix(self.document_path.suffix + ".tmp")
            temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary_path, self.document_path)
            if self.on_saved is not None:
                self.on_saved()
        except Exception as error:
            self.statusBar().showMessage(f"Could not save this document: {error}", 5000)

    def create_recovery_snapshot(self) -> None:
        if self.document_path is None:
            return
        history_dir = self.document_path.parent / ".robustnotes-recovery"
        history_dir.mkdir(parents=True, exist_ok=True)
        snapshots = sorted(history_dir.glob(f"{self.document_path.name}.*.zip"), key=lambda item: item.stat().st_mtime)
        if snapshots and datetime.now().timestamp() - snapshots[-1].stat().st_mtime < 300:
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        snapshot = history_dir / f"{self.document_path.name}.{timestamp}.zip"
        asset_dir = self.document_path.with_name(f"{self.document_path.stem}_assets")
        with zipfile.ZipFile(snapshot, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(self.document_path, self.document_path.name)
            if asset_dir.is_dir():
                for child in asset_dir.rglob("*"):
                    if child.is_file() and not child.is_symlink():
                        archive.write(child, child.relative_to(self.document_path.parent))
        for stale in snapshots[:-8]:
            try:
                stale.unlink()
            except OSError:
                pass

    def load_document(self) -> None:
        if self.document_path is None:
            return
        try:
            payload = json.loads(self.document_path.read_text(encoding="utf-8"))
            self.document_type = str(payload.get("type", self.document_type))
            self.document_title = str(payload.get("title", self.document_title))
            self.template_name = str(payload.get("template", self.template_name))
            if self.document_type == "notebook":
                self.template_pixmap = self.make_notebook_template(self.load_or_create_template(), self.template_name)
            self.setWindowTitle(f"{self.document_title} — RobustNotes Studio")
            for page_record in payload.get("pages", []):
                image_path = self.document_path.parent / str(page_record.get("image", ""))
                pixmap = QPixmap(str(image_path)) if image_path.exists() else self.template_pixmap
                stored_image_key = pixmap.cacheKey() if image_path.exists() and not pixmap.isNull() else None
                source_name = str(page_record.get("source_name", ""))
                # A blank page with an unreadable/dark template is a broken
                # local asset; imported PDF pages may intentionally be dark.
                if pixmap.isNull() or (not source_name and not self.is_valid_page_template(pixmap)):
                    pixmap = self.template_pixmap
                self.add_page(
                    pixmap,
                    str(page_record.get("title", f"Page {len(self.pages) + 1}")),
                    str(page_record.get("source_text", "")),
                    source_name,
                )
                scene = self.pages[-1]
                if stored_image_key is not None and pixmap.cacheKey() == stored_image_key:
                    self._saved_page_image_keys[image_path] = stored_image_key
                scene.ocr_text = str(page_record.get("ocr_text", ""))
                for drawing in page_record.get("drawings", []):
                    if drawing.get("kind") == "image":
                        image_path = self.document_path.parent / str(drawing.get("image", ""))
                        image_pixmap = QPixmap(str(image_path)) if image_path.is_file() else QPixmap()
                        if image_pixmap.isNull():
                            continue
                        item = ResizablePixmapItem(image_pixmap)
                        try:
                            item.setScale(max(0.001, min(100.0, float(drawing.get("scale", 1.0)))))
                            item.setPos(float(drawing.get("x", 0.0)), float(drawing.get("y", 0.0)))
                            item.setZValue(float(drawing.get("z", 2.0)))
                        except (TypeError, ValueError):
                            pass
                        item.setFlags(
                            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                        )
                        scene.addItem(item)
                        self._saved_canvas_image_keys[image_path] = image_pixmap.cacheKey()
                        continue
                    if drawing.get("kind") in ("text", "canvas-text"):
                        text_value = str(drawing.get("text", ""))
                        is_canvas_text = drawing.get("kind") == "canvas-text"
                        if is_canvas_text:
                            item = CanvasTextItem()
                            html_value = drawing.get("html")
                            if isinstance(html_value, str) and html_value:
                                item.setHtml(html_value)
                            else:
                                item.setPlainText(text_value)
                            item.setData(0, "CANVAS_TEXT")
                            item.content_changed.connect(self.schedule_document_save)
                        else:
                            item = QGraphicsTextItem(text_value)
                            item.setData(0, "OCR_CURSIVE_TEXT")
                        font = QFont(str(drawing.get("font_family", "Segoe Script")))
                        font.setStyleHint(QFont.StyleHint.Cursive)
                        try:
                            font.setPointSizeF(float(drawing.get("font_size", 18.0)))
                            item.setTextWidth(max(20.0, float(drawing.get("text_width", 500.0))))
                            item.setPos(float(drawing.get("x", 0.0)), float(drawing.get("y", 0.0)))
                            item.setZValue(float(drawing.get("z", 2.0)))
                        except (TypeError, ValueError):
                            font.setPointSize(18)
                        item.setFont(font)
                        item.setDefaultTextColor(QColor(str(drawing.get("color", "#24333D"))))
                        item.setFlags(
                            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                            | (QGraphicsItem.GraphicsItemFlag.ItemIsFocusable if drawing.get("kind") == "canvas-text" else QGraphicsItem.GraphicsItemFlag(0))
                        )
                        scene.addItem(item)
                        continue
                    elements = drawing.get("elements", [])
                    path = self.path_from_elements(elements)
                    if path.isEmpty():
                        continue
                    item = QGraphicsPathItem(path)
                    color = QColor(str(drawing.get("color", "#FF48DDF0")))
                    width = float(drawing.get("width", 3.0))
                    item.setPen(QPen(
                        color, width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
                    ))
                    mode = str(drawing.get("mode", "PEN"))
                    item.setData(0, mode)
                    item.setPos(float(drawing.get("x", 0.0)), float(drawing.get("y", 0.0)))
                    item.setZValue(float(drawing.get("z", 1.0)))
                    item.setFlags(
                        QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                        | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                    )
                    item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
                    if mode == "LASER":
                        glow = QGraphicsDropShadowEffect()
                        glow.setBlurRadius(25)
                        glow.setColor(color)
                        glow.setOffset(0, 0)
                        item.setGraphicsEffect(glow)
                    scene.addItem(item)
            if not self.pages:
                self.add_page(self.template_pixmap, "Page 1")
            self.page_list.setCurrentRow(0)
            self.update_current_thumbnail()
        except Exception as error:
            QMessageBox.warning(self, "Could not open document", f"The document could not be read.\n\n{error}")
            if not self.pages:
                self.add_page(self.template_pixmap, "Page 1")

    @staticmethod
    def path_from_elements(elements: object) -> QPainterPath:
        path = QPainterPath()
        # JSON decoding yields object values. Validate and normalize the data
        # before passing coordinates to Qt, which also handles damaged files.
        if not isinstance(elements, list):
            return path
        points: list[tuple[int, float, float]] = []
        for entry in elements:
            if (
                not isinstance(entry, list)
                or len(entry) < 3
                or not all(isinstance(value, (int, float)) for value in entry[:3])
            ):
                continue
            points.append((int(entry[0]), float(entry[1]), float(entry[2])))
        index = 0
        move_type = 0
        line_type = 1
        curve_type = 2
        while index < len(points):
            element_type, x, y = points[index]
            if element_type == move_type:
                path.moveTo(x, y)
                index += 1
            elif element_type == line_type:
                path.lineTo(x, y)
                index += 1
            elif element_type == curve_type and index + 2 < len(points):
                control_one = points[index]
                control_two = points[index + 1]
                end_point = points[index + 2]
                path.cubicTo(
                    control_one[1], control_one[2],
                    control_two[1], control_two[2],
                    end_point[1], end_point[2],
                )
                index += 3
            else:
                index += 1
        return path

    @staticmethod
    def make_whiteboard_template() -> QPixmap:
        pixmap = QPixmap(1920, 1200)
        pixmap.fill(QColor("#FFFFFF"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#F3F5F8"), 1))
        for x in range(48, 1920, 48):
            painter.drawLine(x, 0, x, 1200)
        for y in range(48, 1200, 48):
            painter.drawLine(0, y, 1920, y)
        painter.end()
        return pixmap

    @staticmethod
    def make_notebook_template(base: QPixmap, template_name: str) -> QPixmap:
        if template_name == "Dot grid" or base.isNull():
            return QPixmap(base)
        pixmap = QPixmap(base.size())
        pixmap.fill(QColor("#FFFFFF"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = pixmap.width(), pixmap.height()
        blue = QColor("#D8E5F3")
        accent = QColor("#E8A2A2")
        if template_name == "Cornell notes":
            painter.setPen(QPen(blue, 2))
            for y in range(190, height - 35, 58):
                painter.drawLine(70, y, width - 65, y)
            painter.setPen(QPen(accent, 3))
            painter.drawLine(round(width * 0.32), 190, round(width * 0.32), height - 35)
            painter.drawLine(70, 150, width - 65, 150)
        elif template_name == "Meeting notes":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F1F5FA"))
            painter.drawRoundedRect(QRectF(62, 52, width - 124, 190), 18, 18)
            painter.setPen(QPen(QColor("#7890AA"), 2))
            painter.drawLine(86, 146, width - 86, 146)
            painter.drawLine(86, 208, width - 86, 208)
            painter.setPen(QPen(blue, 2))
            for y in range(300, height - 120, 58):
                painter.drawLine(70, y, width - 65, y)
            painter.setPen(QPen(QColor("#B9C6D5"), 2))
            painter.drawRoundedRect(QRectF(62, height - 105, width - 124, 60), 12, 12)
        elif template_name == "Weekly planner":
            left, top, right, bottom = 55, 75, width - 55, height - 55
            columns, rows = 4, 2
            cell_w, cell_h = (right - left) / columns, (bottom - top) / rows
            for row in range(rows):
                for column in range(columns):
                    rect = QRectF(left + column * cell_w, top + row * cell_h, cell_w, cell_h)
                    painter.setPen(QPen(QColor("#C9D5E2"), 2))
                    painter.setBrush(QColor("#F7F9FC") if row == 0 else QColor("#FFFFFF"))
                    painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), 12, 12)
                    painter.setPen(QPen(QColor("#8CA0B5"), 2))
                    painter.drawLine(round(rect.left() + 20), round(rect.top() + 64), round(rect.right() - 20), round(rect.top() + 64))
        painter.end()
        return pixmap

    @staticmethod
    def is_valid_page_template(pixmap: QPixmap) -> bool:
        """Reject the all-black/corrupt default page asset without blocking dark PDFs."""
        if pixmap.isNull():
            return False
        image = pixmap.toImage()
        samples = [
            image.pixelColor(round((image.width() - 1) * x), round((image.height() - 1) * y)).lightness()
            for x, y in ((0.1, 0.1), (0.5, 0.1), (0.9, 0.1), (0.1, 0.5),
                         (0.5, 0.5), (0.9, 0.5), (0.1, 0.9), (0.5, 0.9), (0.9, 0.9))
        ]
        return sum(samples) / len(samples) >= 48

    # ----- Template and startup helpers ---------------------------------------

    def load_or_create_template(self) -> QPixmap:
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        os.makedirs(assets_dir, exist_ok=True)
        pdf_path = os.path.join(assets_dir, "sample-page.pdf")
        png_path = os.path.join(assets_dir, "page_template.png")

        if os.path.exists(png_path):
            existing = QPixmap(png_path)
            if self.is_valid_page_template(existing):
                return existing

        if HAS_PDF_SUPPORT and QPdfDocument is not None and os.path.exists(pdf_path):
            document = QPdfDocument()
            if document.load(pdf_path) == QPdfDocument.Error.None_ and document.pageCount() > 0:
                page_size = document.pagePointSize(0)
                width = 1920
                height = max(1, round(width * page_size.height() / page_size.width()))
                image = document.render(0, QSize(width, height))
                pixmap = QPixmap.fromImage(image)
                if self.is_valid_page_template(pixmap):
                    pixmap.save(png_path)
                    return pixmap

        pixmap = QPixmap(1920, 1200)
        pixmap.fill(QColor("#FAFBFD"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot_pen = QPen(QColor("#E4E8EF"), 2)
        painter.setPen(dot_pen)
        for x in range(40, 1920, 40):
            for y in range(40, 1200, 40):
                painter.drawPoint(x, y)
        painter.end()
        pixmap.save(png_path)
        return pixmap

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.thumbnail_timer.stop()
        self.document_save_timer.stop()
        self.save_document()
        super().closeEvent(event)

    def toggle_focus_mode(self, enabled: Optional[bool] = None) -> None:
        next_state = not getattr(self, "focus_mode", False) if enabled is None else enabled
        if next_state == getattr(self, "focus_mode", False):
            return
        if next_state:
            self._focus_ai_was_visible = self.ai_sidebar.isVisible()
            self.toolbar_widget.hide()
            self.sidebar_widget.hide()
            self.ai_sidebar.hide()
            self.statusBar().hide()
        else:
            self.toolbar_widget.show()
            self.sidebar_widget.show()
            self.ai_sidebar.setVisible(getattr(self, "_focus_ai_was_visible", True))
            self.statusBar().show()
        self.focus_mode = next_state
        QTimer.singleShot(0, self.canvas.fit_page)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "zoom_hud"):
            margin = 18
            x = self.canvas.width() - self.zoom_hud.width() - margin
            y = self.canvas.height() - self.zoom_hud.height() - margin
            self.zoom_hud.move(max(8, x), max(8, y))
            self.zoom_hud.raise_()

    def bind_shortcuts(self) -> None:
        shortcuts = [
            ("Ctrl+N", self.add_new_page),
            ("Ctrl+Z", self.undo_current),
            ("Ctrl+Y", self.redo_current),
            ("Ctrl+Shift+Z", self.redo_current),
            ("F11", self.toggle_focus_mode),
            ("Escape", lambda: self.toggle_focus_mode(False)),
            ("P", lambda: self.select_tool("PEN")),
            ("H", lambda: self.select_tool("HIGHLIGHTER")),
            ("Z", lambda: self.select_tool("LASER")),
            ("E", lambda: self.select_tool("ERASER")),
            ("I", lambda: self.select_tool("LINE")),
            ("S", lambda: self.select_tool("SHAPE")),
            ("L", lambda: self.select_tool("LASSO")),
            ("T", lambda: self.select_tool("TEXT")),
        ]
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

        for index in range(6):
            shortcut = QShortcut(QKeySequence(str(index + 1)), self)
            shortcut.activated.connect(
                lambda selected=index: self.set_color(
                    str(THEMES[self.current_theme_name]["swatches"][selected])
                )
            )
            self.shortcuts.append(shortcut)

class OnboardingDialog(QDialog):
    """A short, skippable introduction shown once after creating a profile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to RobustNotes")
        self.setModal(True)
        self.setMinimumSize(760, 540)
        self.setObjectName("Onboarding")
        root = QVBoxLayout(self)
        root.setContentsMargins(42, 34, 42, 28)
        root.setSpacing(18)
        root.addWidget(full_logo_label(220, 56), 0, Qt.AlignmentFlag.AlignLeft)
        self.pages = QStackedWidget()
        pages = [
            ("Make space for your thinking.", "Create a calm home for class notes, project ideas, code snippets, and visual planning. Everything in this workspace is saved locally on this device.", "01  ·  YOUR SPACE", "✧", "A workspace\nthat feels like yours", "CAPTURE  ·  SHAPE  ·  KEEP"),
            ("Choose the right canvas.", "NOTEBOOKS  ·  Write by hand, import PDFs, and keep ideas in pages.\nTEXT FILES  ·  Keep plain text, Markdown, or Python files close at hand.\nWHITEBOARDS  ·  Spread out with an open, spacious canvas for diagrams and brainstorming.", "02  ·  THREE WAYS TO WORK", "▤", "Three formats,\none home", "PAGES  ·  TEXT  ·  CANVAS"),
            ("A focused toolset, ready when you are.", "Use pen, highlighter, lasso, shapes, and laser on the notebook canvas. Add practical measuring tools and a calculator from the tools menu. Your notes remain yours; local AI options can be configured later in the editor.", "03  ·  CREATE AND EXPLORE", "⌁", "Tools that keep\nyou in flow", "INK  ·  SHAPES  ·  IDEAS"),
            ("Pick up right where you left off.", "Your dashboard keeps recent work together. Reopen a file with one click, rename it from its editor, and create something new whenever an idea arrives. You can revisit this introduction from the dashboard help menu.", "04  ·  YOUR NEXT STEP", "↗", "From first thought\nto finished work", "RECENTS  ·  REOPEN  ·  CONTINUE"),
        ]
        for title, description, kicker, symbol, visual_title, visual_caption in pages:
            page = QWidget()
            layout = QHBoxLayout(page)
            layout.setContentsMargins(0, 12, 0, 12)
            layout.setSpacing(22)
            visual = QFrame()
            visual.setObjectName("OnboardVisual")
            visual.setMinimumWidth(205)
            visual.setMaximumWidth(235)
            visual_layout = QVBoxLayout(visual)
            visual_layout.setContentsMargins(22, 22, 22, 22)
            visual_layout.addWidget(QLabel("YOUR WORKSPACE"))
            visual_mark = QLabel(symbol)
            visual_mark.setObjectName("OnboardSymbol")
            visual_layout.addWidget(visual_mark, 1, Qt.AlignmentFlag.AlignCenter)
            visual_heading = QLabel(visual_title)
            visual_heading.setObjectName("VisualHeading")
            visual_heading.setWordWrap(True)
            visual_layout.addWidget(visual_heading)
            visual_caption_label = QLabel(visual_caption)
            visual_caption_label.setObjectName("Eyebrow")
            visual_caption_label.setWordWrap(True)
            visual_layout.addWidget(visual_caption_label)
            layout.addWidget(visual)
            content = QVBoxLayout()
            content.setContentsMargins(0, 14, 0, 10)
            content.addStretch(1)
            line = QLabel(kicker)
            line.setObjectName("Eyebrow")
            headline = QLabel(title)
            headline.setObjectName("Hero")
            headline.setWordWrap(True)
            body = QLabel(description)
            body.setObjectName("Body")
            body.setWordWrap(True)
            body.setAlignment(Qt.AlignmentFlag.AlignTop)
            headline.setStyleSheet("font-size:30px")
            content.addWidget(line)
            content.addSpacing(10)
            content.addWidget(headline)
            content.addSpacing(10)
            content.addWidget(body)
            content.addStretch(2)
            layout.addLayout(content, 1)
            self.pages.addWidget(page)
        root.addWidget(self.pages, 1)
        bottom = QHBoxLayout()
        self.step_label = QLabel("01 / 04")
        self.step_label.setObjectName("Muted")
        bottom.addWidget(self.step_label)
        bottom.addStretch(1)
        skip = QPushButton("Skip")
        skip.setObjectName("QuietButton")
        skip.clicked.connect(self.accept)
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("QuietButton")
        self.back_button.clicked.connect(self.previous_page)
        self.next_button = QPushButton("Continue  →")
        self.next_button.setObjectName("PrimaryButton")
        self.next_button.clicked.connect(self.next_page)
        bottom.addWidget(skip)
        bottom.addWidget(self.back_button)
        bottom.addWidget(self.next_button)
        root.addLayout(bottom)
        self.pages.currentChanged.connect(self.update_navigation)
        self.setStyleSheet(WORKSPACE_STYLE)
        self.update_navigation(0)

    def update_navigation(self, index: int) -> None:
        self.step_label.setText(f"{index + 1:02d} / {self.pages.count():02d}")
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("Open my workspace  →" if index == self.pages.count() - 1 else "Continue  →")

    def previous_page(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))

    def next_page(self) -> None:
        if self.pages.currentIndex() == self.pages.count() - 1:
            self.accept()
        else:
            self.pages.setCurrentIndex(self.pages.currentIndex() + 1)


WORKSPACE_STYLE = """
QWidget { background:#111720; color:#edf3fa; font-family:'Segoe UI'; font-size:14px; }
QFrame#Card, QFrame#Panel { background:#19222d; border:1px solid #2b3745; border-radius:16px; }
QFrame#OnboardVisual { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1d3742,stop:1 #19222d); border:1px solid #314958; border-radius:18px; }
QLabel { background:transparent; }
QLabel#BrandMark { background:#43d8e7; color:#09232b; border-radius:13px; font-size:22px; font-weight:800; min-width:46px; max-width:46px; min-height:46px; max-height:46px; qproperty-alignment:AlignCenter; }
QLabel#Brand { font-size:20px; font-weight:750; color:#f4f8fd; }
QLabel#Eyebrow { color:#60dbe8; font-size:11px; font-weight:700; letter-spacing:2px; }
QLabel#Hero { color:#f4f8fd; font-size:34px; font-weight:700; }
QLabel#Body { color:#abb9c9; font-size:15px; line-height:1.5; }
QLabel#Muted { color:#8292a4; }
QLabel#OnboardSymbol { color:#60dbe8; font-size:76px; font-weight:700; qproperty-alignment:AlignCenter; }
QLabel#VisualHeading { color:#f4f8fd; font-size:20px; font-weight:700; }
QPushButton { color:#dce6f1; background:#222d39; border:1px solid #344252; border-radius:9px; padding:10px 15px; font-weight:600; }
QPushButton:hover { background:#2a3745; border-color:#52657a; }
QPushButton:disabled { color:#637183; }
QPushButton#PrimaryButton { color:#08252b; background:#46d9e7; border:0; font-weight:750; }
QPushButton#PrimaryButton:hover { background:#6be5ef; }
QPushButton#QuietButton { background:transparent; border-color:transparent; }
QPushButton#ProfileButton { color:#08252b; background:#46d9e7; border:0; border-radius:19px; padding:0; font-size:15px; font-weight:800; }
QPushButton#ProfileButton:hover { background:#6be5ef; }
QLineEdit, QPlainTextEdit, QListWidget, QComboBox { color:#eef4fa; background:#121a23; border:1px solid #344252; border-radius:9px; padding:11px; selection-background-color:#1c7180; }
QListWidget { padding:8px; }
QListWidget::item { padding:13px; border-radius:8px; }
QListWidget::item:selected { background:#203d49; }
QScrollBar:vertical { background:transparent; width:10px; }
QScrollBar::handle:vertical { background:#3b4a5a; border-radius:4px; min-height:28px; }
"""


class TextFileWindow(QMainWindow):
    """Small local editor for plain text, Markdown, and code files."""

    def __init__(self, path: Path, on_saved=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowIcon(robustnotes_icon())
        self.path = Path(path)
        self.on_saved = on_saved
        self.setWindowTitle(f"{self.path.name} — RobustNotes")
        self.resize(1000, 720)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("TextEditor")
        self.editor.setTabStopDistance(32)
        if self.path.suffix.lower() in {".py", ".js", ".ts", ".json", ".html", ".css"}:
            self.editor.setStyleSheet("QPlainTextEdit { font-family:Consolas,'Cascadia Code',monospace; font-size:14px; line-height:1.5; }")
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 16)
        title = QLabel(self.path.name)
        title.setObjectName("Brand")
        layout.addWidget(title)
        layout.addWidget(self.editor, 1)
        self.setCentralWidget(central)
        if self.path.exists():
            self.editor.setPlainText(self.path.read_text(encoding="utf-8"))
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.save_file)
        self.editor.textChanged.connect(self.save_timer.start)
        self.statusBar().showMessage("Saved locally")
        self.setStyleSheet(WORKSPACE_STYLE)

    def save_file(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(self.editor.toPlainText(), encoding="utf-8")
            os.replace(temp, self.path)
            self.statusBar().showMessage("Saved locally", 1500)
            if self.on_saved:
                self.on_saved()
        except OSError as error:
            self.statusBar().showMessage(f"Save failed: {error}")

    def closeEvent(self, event):  # noqa: N802
        self.save_timer.stop()
        self.save_file()
        super().closeEvent(event)


class WorkspaceWindow(QMainWindow):
    """Local sign-in, first-run guide, and document dashboard."""

    def __init__(self):
        super().__init__()
        self.setWindowIcon(robustnotes_icon())
        self.local_data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.config_dir = self.local_data_dir / "Security"
        self.profiles_dir = self.config_dir / "profiles"
        self.remembered_login_path = self.config_dir / "remembered_login.bin"
        old_config_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)) / "RobustNotes"
        self.legacy_account_file = old_config_dir / "local_profile.json"
        self.account_file = self.legacy_account_file
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_workspace_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / "RobustNotes"
        self.base_workspace_dir = self.local_data_dir / "Profiles" / "Workspaces"
        self.workspace_dir = self.base_workspace_dir / "_locked"
        self.workspace_index_path = self.workspace_dir / ".robustnotes-index.json"
        self.workspace_index: dict[str, dict[str, Any]] = {}
        self.settings = QSettings(str(self.config_dir / "workspace.ini"), QSettings.Format.IniFormat)
        self.profile: Optional[dict[str, str]] = None
        self.profile_key = ""
        self.legacy_profile_key = ""
        self._allow_exit_without_confirmation = False
        self.open_windows: list[QMainWindow] = []
        self.setWindowTitle("RobustNotes — Workspace")
        self.resize(1240, 820)
        self.setMinimumSize(960, 680)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.auth_page = self.build_auth_page()
        self.dashboard_page = self.build_dashboard()
        self.document_page = self.build_document_page()
        self.stack.addWidget(self.auth_page)
        self.stack.addWidget(self.dashboard_page)
        self.stack.addWidget(self.document_page)
        self.setStyleSheet(WORKSPACE_STYLE)
        self.migrate_legacy_profile()
        self.show_auth()
        if sys.platform == "win32":
            QTimer.singleShot(0, self.try_remembered_login)

    def build_document_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DocumentWorkspace")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("DocumentWorkspaceHeader")
        header.setStyleSheet("background:#151d27; border-bottom:1px solid #2b3948;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 18, 10)
        back = QPushButton("←  Workspace")
        back.setObjectName("QuietButton")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setToolTip("Save and return to your workspace")
        back.clicked.connect(self.return_to_dashboard)
        self.document_header_title = QLabel("Document")
        self.document_header_title.setObjectName("Brand")
        header_layout.addWidget(back)
        header_layout.addSpacing(12)
        header_layout.addWidget(self.document_header_title)
        header_layout.addStretch(1)
        layout.addWidget(header)

        self.document_stack = QStackedWidget()
        self.document_stack.setObjectName("DocumentStack")
        layout.addWidget(self.document_stack, 1)
        return page

    def return_to_dashboard(self) -> None:
        current = self.document_stack.currentWidget()
        if isinstance(current, NotebookWindow):
            current.save_document()
        elif isinstance(current, TextFileWindow):
            current.save_file()
        self.stack.setCurrentWidget(self.dashboard_page)
        self.refresh_documents()

    def show_document(self, window: QMainWindow, title: str) -> None:
        self.document_header_title.setText(title)
        self.document_stack.setCurrentWidget(window)
        self.stack.setCurrentWidget(self.document_page)

    @staticmethod
    def profile_key_for(name: str) -> str:
        normalized_name = " ".join(name.strip().casefold().split())
        return hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()[:24]

    def profile_file_for(self, name: str) -> Path:
        return self.profiles_dir / f"{self.profile_key_for(name)}.json"

    @staticmethod
    def read_profile_file(path: Path) -> Optional[dict[str, str]]:
        try:
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            name = payload.get("name")
            salt = payload.get("salt")
            password_hash = payload.get("password_hash")
            if (
                not isinstance(name, str)
                or not isinstance(salt, str)
                or not isinstance(password_hash, str)
            ):
                return None
            profile: dict[str, str] = {
                "name": name,
                "salt": salt,
                "password_hash": password_hash,
            }
            for key in ("workspace_migrated",):
                value = payload.get(key)
                if isinstance(value, str):
                    profile[key] = value
            return profile
        except (OSError, json.JSONDecodeError):
            return None

    def read_profile(self, name: str) -> Optional[dict[str, str]]:
        profile = self.read_profile_file(self.profile_file_for(name))
        if profile is None or self.profile_key_for(profile["name"]) != self.profile_key_for(name):
            return None
        return profile

    def migrate_legacy_profile(self) -> None:
        legacy = self.read_profile_file(self.legacy_account_file)
        if legacy is None:
            return
        key = self.profile_key_for(legacy["name"])
        profile_path = self.profile_file_for(legacy["name"])
        self.legacy_profile_key = key
        try:
            if not profile_path.exists():
                temporary_path = profile_path.with_suffix(".tmp")
                temporary_path.write_text(json.dumps(legacy), encoding="utf-8")
                os.replace(temporary_path, profile_path)
            self.legacy_account_file.unlink(missing_ok=True)
        except OSError:
            # Keep the old profile intact and allow sign-in against it.
            return

    def build_auth_page(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(38, 38, 38, 38)
        outer.setSpacing(24)
        story = QFrame()
        story.setObjectName("Panel")
        story_layout = QVBoxLayout(story)
        story_layout.setContentsMargins(40, 38, 40, 38)
        story_layout.addWidget(full_logo_label(250, 64), 0, Qt.AlignmentFlag.AlignLeft)
        story_layout.addStretch(1)
        kicker = QLabel("A CLEARER SPACE TO THINK")
        kicker.setObjectName("Eyebrow")
        story_layout.addWidget(kicker)
        headline = QLabel("Your ideas,\nbeautifully in order.")
        headline.setObjectName("Hero")
        story_layout.addWidget(headline)
        story_body = QLabel("Build a home for handwritten notebooks, focused text files, code, and open-ended whiteboards.")
        story_body.setObjectName("Body")
        story_body.setWordWrap(True)
        story_layout.addWidget(story_body)
        story_layout.addStretch(2)
        privacy = QLabel("◈  Saved on this device  ·  No online account required")
        privacy.setObjectName("Muted")
        story_layout.addWidget(privacy)
        outer.addWidget(story, 1)

        panel = QFrame()
        panel.setObjectName("Card")
        form = QVBoxLayout(panel)
        form.setContentsMargins(38, 38, 38, 38)
        form.addStretch(1)
        self.auth_heading = QLabel("Welcome back")
        self.auth_heading.setObjectName("Brand")
        form.addWidget(self.auth_heading)
        self.auth_description = QLabel("Sign in to open your local workspace.")
        self.auth_description.setObjectName("Muted")
        form.addWidget(self.auth_description)
        form.addSpacing(18)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Username")
        form.addWidget(self.name_input)
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addWidget(self.password_input)
        self.remember_login_checkbox = QCheckBox("Keep me signed in on this Windows account")
        self.remember_login_checkbox.setChecked(sys.platform == "win32")
        self.remember_login_checkbox.setVisible(sys.platform == "win32")
        form.addWidget(self.remember_login_checkbox)
        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText("Confirm password")
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addWidget(self.confirm_input)
        self.auth_error = QLabel("")
        self.auth_error.setWordWrap(True)
        self.auth_error.setStyleSheet("color:#ff8c9a")
        form.addWidget(self.auth_error)
        self.auth_button = QPushButton("Create local profile  →")
        self.auth_button.setObjectName("PrimaryButton")
        self.auth_button.clicked.connect(self.submit_auth)
        form.addWidget(self.auth_button)
        self.auth_hint = QLabel(
            "On Windows, you can stay signed in securely on this Windows account."
            if sys.platform == "win32"
            else "Your password is protected on this device."
        )
        self.auth_hint.setObjectName("Muted")
        self.auth_hint.setWordWrap(True)
        form.addWidget(self.auth_hint)
        self.auth_mode_button = QPushButton("")
        self.auth_mode_button.setObjectName("QuietButton")
        self.auth_mode_button.clicked.connect(self.toggle_auth_mode)
        form.addWidget(self.auth_mode_button)
        form.addStretch(1)
        outer.addWidget(panel, 1)
        return page

    def show_auth(self):
        self.close_user_session()
        has_profiles = any(self.profiles_dir.glob("*.json"))
        self.auth_mode = "login" if has_profiles else "signup"
        self.update_auth_mode()
        self.auth_hint.setText("Files and settings are separated by profile in this device's local storage. No cloud account is used.")
        self.stack.setCurrentWidget(self.auth_page)

    def update_auth_mode(self) -> None:
        signup = self.auth_mode == "signup"
        self.auth_heading.setText("Create a local profile" if signup else "Welcome back")
        self.auth_description.setText(
            "Each profile gets its own local workspace and settings." if signup
            else "Sign in with the username and password for this device."
        )
        self.name_input.setPlaceholderText("Choose a username" if signup else "Username")
        self.confirm_input.setVisible(signup)
        self.remember_login_checkbox.setVisible(sys.platform == "win32")
        self.auth_button.setText("Create local profile  →" if signup else "Sign in  →")
        self.auth_mode_button.setText(
            "Already have a profile?  Sign in" if signup
            else "New to this device?  Create a profile"
        )
        self.auth_error.clear()

    def toggle_auth_mode(self) -> None:
        self.auth_mode = "login" if self.auth_mode == "signup" else "signup"
        self.update_auth_mode()

    def submit_auth(self):
        name = self.name_input.text().strip()
        if not name:
            self.auth_error.setText("Enter your username to continue.")
            return
        password = self.password_input.text()
        account_file = self.profile_file_for(name)
        if self.auth_mode == "signup":
            if len(password) < 8:
                self.auth_error.setText("Use a password of at least 8 characters.")
                return
            if password != self.confirm_input.text():
                self.auth_error.setText("Those passwords do not match.")
                return
            if account_file.exists():
                self.auth_error.setText("That username already has a profile on this device. Sign in instead.")
                return
            salt = secrets.token_bytes(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
            profile = {"name": name, "salt": salt.hex(), "password_hash": digest.hex()}
            try:
                self.profiles_dir.mkdir(parents=True, exist_ok=True)
                temporary = account_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(profile), encoding="utf-8")
                os.replace(temporary, account_file)
            except OSError as error:
                self.auth_error.setText(f"Could not save the local profile: {error}")
                return
            if not self.activate_profile(profile, account_file):
                return
            self.save_remembered_login(name, password)
            self.password_input.clear()
            self.confirm_input.clear()
            self.open_dashboard()
            if not self.settings.value("onboardingComplete", False, type=bool):
                OnboardingDialog(self).exec()
                self.settings.setValue("onboardingComplete", True)
            return
        profile = self.read_profile(name)
        if profile is None and self.profile_key_for(name) == self.legacy_profile_key:
            profile = self.read_profile_file(self.legacy_account_file)
            if profile is not None:
                account_file = self.legacy_account_file
        if profile is None:
            self.auth_error.setText("No local profile exists with that username. Check the name or create a profile.")
            return
        try:
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(profile["salt"]), 310_000)
            valid = secrets.compare_digest(candidate.hex(), profile["password_hash"])
        except (KeyError, ValueError):
            valid = False
        if not valid:
            self.auth_error.setText("That password did not match this local profile.")
            return
        if not self.activate_profile(profile, account_file):
            return
        self.save_remembered_login(name, password)
        self.password_input.clear()
        self.open_dashboard()

    def save_remembered_login(self, name: str, password: str) -> None:
        """Remember credentials only as DPAPI ciphertext bound to this Windows user."""
        if sys.platform != "win32":
            return
        if not self.remember_login_checkbox.isChecked():
            self.remembered_login_path.unlink(missing_ok=True)
            return
        payload = json.dumps({"name": name, "password": password}, ensure_ascii=False).encode("utf-8")
        try:
            encrypted = protect_remembered_login(payload)
            temporary = self.remembered_login_path.with_suffix(".tmp")
            temporary.write_bytes(encrypted)
            os.replace(temporary, self.remembered_login_path)
        except (OSError, ValueError) as error:
            self.remembered_login_path.unlink(missing_ok=True)
            self.auth_hint.setText(f"Could not enable this-device sign-in: {error}")

    def try_remembered_login(self) -> None:
        if not self.remembered_login_path.is_file() or self.auth_mode != "login":
            return
        try:
            payload = json.loads(unprotect_remembered_login(self.remembered_login_path.read_bytes()).decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("name"), str) or not isinstance(payload.get("password"), str):
                raise ValueError("Invalid saved sign-in data")
            self.name_input.setText(payload["name"])
            self.password_input.setText(payload["password"])
            self.remember_login_checkbox.setChecked(True)
            self.submit_auth()
            if self.profile is None:
                self.remembered_login_path.unlink(missing_ok=True)
                self.password_input.clear()
                self.auth_error.clear()
                self.auth_hint.setText("Saved sign-in could not be used. Please sign in again.")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.remembered_login_path.unlink(missing_ok=True)
            self.auth_hint.setText("Saved sign-in is unavailable. Please sign in again.")

    def activate_profile(self, profile: dict[str, str], account_file: Path) -> bool:
        profile_key = self.profile_key_for(profile["name"])
        workspace_dir = self.base_workspace_dir / profile_key
        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.auth_error.setText(f"Could not open this profile's local workspace: {error}")
            return False
        self.profile = profile
        self.account_file = account_file
        self.profile_key = profile_key
        self.workspace_dir = workspace_dir
        self.workspace_index_path = self.workspace_dir / ".robustnotes-index.json"
        self.load_workspace_index()
        self.settings = QSettings(str(self.workspace_dir / "settings.ini"), QSettings.Format.IniFormat)
        if self.profile_key == self.legacy_profile_key and profile.get("workspace_migrated") != "true":
            self.migrate_legacy_workspace(profile)
        return True

    def migrate_legacy_workspace(self, profile: dict[str, str]) -> None:
        """Move prior single-profile documents into that profile's private folder."""
        if not self.legacy_workspace_dir.exists():
            profile["workspace_migrated"] = "true"
            return
        try:
            legacy_entries = list(self.legacy_workspace_dir.iterdir())
            supported_suffixes = {".rnote", ".rboard", ".txt", ".md", ".py", ".js", ".ts", ".json", ".html", ".css"}
            for source in legacy_entries:
                if not source.is_symlink() and source.suffix.lower() in supported_suffixes and source.is_file():
                    shutil.move(str(source), str(self.workspace_dir / source.name))
            for source in legacy_entries:
                if not source.is_symlink() and source.is_dir() and source.name.endswith("_assets"):
                    document_stem = source.name[:-7]
                    if any((self.workspace_dir / f"{document_stem}{suffix}").is_file()
                           for suffix in (".rnote", ".rboard")):
                        shutil.move(str(source), str(self.workspace_dir / source.name))
            profile["workspace_migrated"] = "true"
            temporary = self.account_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(profile), encoding="utf-8")
            os.replace(temporary, self.account_file)
        except OSError as error:
            self.statusBar().showMessage(f"Legacy files remain in the previous folder; migration will retry: {error}", 7000)

    def close_user_session(self) -> None:
        for window in list(self.open_windows):
            if isinstance(window, NotebookWindow):
                music = window.music_dialog
                if music is not None:
                    music.end_session()
            window.close()
            if hasattr(self, "document_stack"):
                self.document_stack.removeWidget(window)
        self.open_windows.clear()
        self.profile = None
        self.profile_key = ""
        self.account_file = self.legacy_account_file
        self.workspace_dir = self.base_workspace_dir / "_locked"
        self.settings = QSettings(str(self.config_dir / "workspace.ini"), QSettings.Format.IniFormat)
        if hasattr(self, "name_input"):
            self.name_input.clear()
            self.password_input.clear()
            self.confirm_input.clear()

    def build_dashboard(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(42, 30, 42, 28)
        root.setSpacing(24)
        header = QHBoxLayout()
        header.addWidget(full_logo_label(190, 48))
        header.addStretch(1)
        self.profile_label = QLabel("Local workspace")
        self.profile_label.setObjectName("Muted")
        header.addWidget(self.profile_label)
        self.profile_button = QPushButton("?")
        self.profile_button.setObjectName("ProfileButton")
        self.profile_button.setFixedSize(38, 38)
        self.profile_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.profile_button.setToolTip("Profile and security")
        self.profile_menu = QMenu(self.profile_button)
        self.profile_menu.setStyleSheet(
            "QMenu { background:#19222d; color:#edf3fa; border:1px solid #344252; padding:6px; }"
            "QMenu::item { padding:8px 24px; border-radius:5px; }"
            "QMenu::item:selected { background:#263746; }"
        )
        self.profile_name_action = self.profile_menu.addAction("Local profile")
        self.profile_name_action.setEnabled(False)
        self.profile_menu.addSeparator()
        change_password_action = self.profile_menu.addAction("Change password…")
        change_password_action.triggered.connect(self.change_password)
        lock_action = self.profile_menu.addAction("Lock workspace")
        lock_action.triggered.connect(self.show_auth)
        self.profile_button.setMenu(self.profile_menu)
        command_button = QPushButton("⌘  Ctrl+K")
        command_button.setCursor(Qt.CursorShape.PointingHandCursor)
        command_button.setToolTip("Open the workspace command palette")
        command_button.clicked.connect(self.open_command_palette)
        header.addWidget(command_button)
        header.addWidget(self.profile_button)
        root.addLayout(header)
        self.command_palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.command_palette_shortcut.activated.connect(self.open_command_palette)

        welcome_row = QHBoxLayout()
        welcome_text = QVBoxLayout()
        self.welcome_label = QLabel("Good to see you.")
        self.welcome_label.setObjectName("Hero")
        welcome_text.addWidget(self.welcome_label)
        subtitle = QLabel("What would you like to make today?")
        subtitle.setObjectName("Body")
        welcome_text.addWidget(subtitle)
        welcome_row.addLayout(welcome_text, 1)
        help_button = QPushButton("Workspace guide")
        help_button.clicked.connect(lambda: OnboardingDialog(self).exec())
        welcome_row.addWidget(help_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(welcome_row)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search notes, PDFs, tags, and folders…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setToolTip("Search document titles and local text, OCR, and PDF content")
        self.search_input.textChanged.connect(self.refresh_documents)
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.activated.connect(self.search_input.setFocus)
        search_row.addWidget(self.search_input, 1)
        daily_button = QPushButton("Daily note")
        daily_button.setToolTip("Open or create today's private daily note")
        daily_button.clicked.connect(self.open_daily_note)
        search_row.addWidget(daily_button)
        capture_button = QPushButton("Quick capture")
        capture_button.setObjectName("PrimaryButton")
        capture_button.setToolTip("Save a thought to your local workspace inbox")
        capture_button.clicked.connect(self.quick_capture)
        search_row.addWidget(capture_button)
        backup_button = QPushButton("Encrypted backup…")
        backup_button.setToolTip("Create a password-protected copy of this local workspace")
        backup_button.clicked.connect(self.show_backup_menu)
        search_row.addWidget(backup_button)
        root.addLayout(search_row)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        actions = [
            ("✎", "New notebook", "Handwritten pages, PDFs, and sketches", self.create_notebook),
            ("‹/›", "New text file", "Notes, Markdown, or a code file", self.create_text_file),
            ("◇", "New whiteboard", "A roomy canvas for visual thinking", self.create_whiteboard),
        ]
        for symbol, title, desc, callback in actions:
            card = QFrame()
            card.setObjectName("Card")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(20, 18, 20, 18)
            icon = QLabel(symbol)
            icon.setStyleSheet("color:#5fe0eb; font-size:26px; font-weight:700")
            layout.addWidget(icon)
            label = QLabel(title)
            label.setStyleSheet("font-size:17px; font-weight:700")
            layout.addWidget(label)
            text = QLabel(desc)
            text.setObjectName("Muted")
            text.setWordWrap(True)
            layout.addWidget(text)
            layout.addStretch(1)
            button = QPushButton("Create  →")
            button.setObjectName("PrimaryButton")
            button.clicked.connect(callback)
            layout.addWidget(button)
            cards.addWidget(card, 1)
        root.addLayout(cards)

        section = QHBoxLayout()
        section_title = QLabel("Recent work")
        section_title.setStyleSheet("font-size:20px; font-weight:700")
        section.addWidget(section_title)
        section.addStretch(1)
        self.document_count = QLabel("")
        self.document_count.setObjectName("Muted")
        section.addWidget(self.document_count)
        refresh = QPushButton()
        refresh.setObjectName("IconButton")
        refresh.setFixedSize(40, 40)
        refresh.setIcon(NotebookWindow.make_action_icon("REFRESH"))
        refresh.setIconSize(QSize(18, 18))
        refresh.setAccessibleName("Refresh recent work")
        refresh.setToolTip("Refresh recent work")
        refresh.clicked.connect(self.refresh_documents)
        section.addWidget(refresh)
        root.addLayout(section)
        self.documents_list = QListWidget()
        self.documents_list.itemDoubleClicked.connect(self.open_selected_document)
        self.documents_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.documents_list.customContextMenuRequested.connect(self.show_document_context_menu)
        root.addWidget(self.documents_list, 1)
        footer = QHBoxLayout()
        folder_label = QLabel("Stored locally on this device  ·  separate workspace for each profile")
        folder_label.setObjectName("Muted")
        footer.addWidget(folder_label)
        footer.addStretch(1)
        root.addLayout(footer)
        return page

    def open_dashboard(self):
        name = self.profile.get("name", "there") if self.profile else "there"
        self.welcome_label.setText(f"Good to see you, {name}.")
        self.profile_label.setText(f"{name}  ·  Local workspace")
        self.profile_button.setText(name.strip()[:1].upper() or "?")
        self.profile_name_action.setText(name)
        self.stack.setCurrentWidget(self.dashboard_page)
        self.refresh_documents()

    def open_daily_note(self) -> None:
        """Open or create one dated Markdown note in this profile's workspace."""
        today = datetime.now().astimezone().date()
        path = self.workspace_dir / f"Daily note {today.isoformat()}.md"
        if not path.exists():
            body = (
                f"# {today.strftime('%A, %B')} {today.day}, {today.year}\n\n"
                "## Priorities\n\n- \n\n"
                "## Notes\n\n\n"
                "## Reflection\n\n"
            )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "Could not create daily note", f"The note could not be saved.\n\n{error}")
                return
        self.open_text_file(path)

    def quick_capture(self) -> None:
        """Capture a short thought into a timestamped local Markdown inbox file."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Quick capture")
        dialog.setMinimumSize(460, 360)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        heading = QLabel("Save a thought for later")
        heading.setObjectName("Brand")
        layout.addWidget(heading)
        title_input = QLineEdit()
        title_input.setPlaceholderText("A short title (optional)")
        layout.addWidget(title_input)
        body_input = QTextEdit()
        body_input.setPlaceholderText("Type a thought, reminder, or idea…")
        layout.addWidget(body_input, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.addButton("Save capture", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setObjectName("PrimaryButton")
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.setStyleSheet(WORKSPACE_STYLE)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        body = body_input.toPlainText().strip()
        if not body:
            QMessageBox.information(self, "Nothing to capture", "Write a thought before saving it.")
            return
        title = " ".join(title_input.text().split()) or "Untitled capture"
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H-%M-%S")
        path = self.unique_path(f"Capture {timestamp} - {title}", ".md")
        try:
            path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Could not save capture", f"The capture could not be saved.\n\n{error}")
            return
        self.open_text_file(path)

    def open_command_palette(self) -> None:
        """Search workspace actions and recent files from one keyboard-first dialog."""
        if self.stack.currentWidget() != self.dashboard_page:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Workspace commands")
        dialog.setMinimumSize(560, 480)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        search = QLineEdit()
        search.setPlaceholderText("Search actions or recent files…")
        search.setClearButtonEnabled(True)
        layout.addWidget(search)
        results = QListWidget()
        results.setObjectName("CommandResults")
        layout.addWidget(results, 1)
        actions = [
            ("Quick capture", "Save a thought to your local inbox", self.quick_capture),
            ("Today's daily note", "Open or create today's Markdown note", self.open_daily_note),
            ("New notebook", "Create handwritten pages and annotate PDFs", self.create_notebook),
            ("New text file", "Create notes, Markdown, or code", self.create_text_file),
            ("New whiteboard", "Open a freeform visual canvas", self.create_whiteboard),
        ]
        entries: list[tuple[str, str, object]] = []
        for title, detail, callback in actions:
            entries.append((title, detail, callback))
        for path in self.document_files()[:100]:
            entries.append((path.stem, f"Open recent · {path.suffix[1:].upper()}", path))

        def populate(query: str = "") -> None:
            results.clear()
            terms = query.casefold().split()
            for title, detail, target in entries:
                searchable = f"{title} {detail}".casefold()
                if terms and not all(term in searchable for term in terms):
                    continue
                item = QListWidgetItem(f"{title}\n{detail}")
                item.setData(Qt.ItemDataRole.UserRole, target)
                results.addItem(item)
            if results.count():
                results.setCurrentRow(0)

        def run_selected() -> None:
            item = results.currentItem()
            target = item.data(Qt.ItemDataRole.UserRole) if item else None
            if callable(target):
                dialog.accept()
                target()
            elif isinstance(target, Path):
                dialog.accept()
                self.open_selected_document_for_path(target)

        search.textChanged.connect(populate)
        results.itemActivated.connect(lambda _item: run_selected())
        search.returnPressed.connect(run_selected)
        dialog.setStyleSheet(WORKSPACE_STYLE)
        populate()
        search.setFocus()
        dialog.exec()

    def change_password(self):
        profile = self.profile
        if profile is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Change password")
        dialog.setMinimumWidth(390)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(26, 24, 26, 22)
        heading = QLabel("Update your password")
        heading.setObjectName("Brand")
        layout.addWidget(heading)
        explanation = QLabel("Choose a new password for this local profile.")
        explanation.setObjectName("Muted")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        current = QLineEdit()
        current.setPlaceholderText("Current password")
        current.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(current)
        new_password = QLineEdit()
        new_password.setPlaceholderText("New password · at least 8 characters")
        new_password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(new_password)
        confirm = QLineEdit()
        confirm.setPlaceholderText("Confirm new password")
        confirm.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(confirm)
        error_label = QLabel("")
        error_label.setStyleSheet("color:#ff8c9a")
        error_label.setWordWrap(True)
        layout.addWidget(error_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        save = QPushButton("Save password")
        save.setObjectName("PrimaryButton")
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        def save_new_password():
            try:
                old_hash = hashlib.pbkdf2_hmac(
                    "sha256", current.text().encode("utf-8"),
                    bytes.fromhex(profile["salt"]), 310_000,
                )
                current_matches = secrets.compare_digest(old_hash.hex(), profile["password_hash"])
            except (KeyError, ValueError):
                current_matches = False
            if not current_matches:
                error_label.setText("Your current password is incorrect.")
                return
            if len(new_password.text()) < 8:
                error_label.setText("Use at least 8 characters for the new password.")
                return
            if new_password.text() != confirm.text():
                error_label.setText("The new passwords do not match.")
                return
            salt = secrets.token_bytes(16)
            updated: dict[str, str] = dict(profile)
            updated["salt"] = salt.hex()
            updated["password_hash"] = hashlib.pbkdf2_hmac(
                "sha256", new_password.text().encode("utf-8"), salt, 310_000
            ).hex()
            try:
                temporary = self.account_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(updated), encoding="utf-8")
                os.replace(temporary, self.account_file)
            except OSError as error:
                error_label.setText(f"Could not save the new password: {error}")
                return
            self.profile = updated
            self.save_remembered_login(updated["name"], new_password.text())
            dialog.accept()
            QMessageBox.information(self, "Password changed", "Your local profile password has been updated.")

        save.clicked.connect(save_new_password)
        dialog.setStyleSheet(WORKSPACE_STYLE)
        dialog.exec()

    def unique_path(self, title: str, suffix: str) -> Path:
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", " ".join(title.strip().split()))
        safe = safe.strip(" .")[:100] or "Untitled"
        if safe.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            safe = f"_{safe}"
        candidate = self.workspace_dir / f"{safe}{suffix}"
        count = 2
        while candidate.exists():
            candidate = self.workspace_dir / f"{safe} ({count}){suffix}"
            count += 1
        return candidate

    def ask_title(self, default: str) -> Optional[str]:
        title, accepted = QInputDialog.getText(self, "Name your work", "Title", QLineEdit.EchoMode.Normal, default)
        return title.strip() if accepted and title.strip() else None

    def load_workspace_index(self) -> None:
        try:
            payload = json.loads(self.workspace_index_path.read_text(encoding="utf-8"))
            self.workspace_index = payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.workspace_index = {}

    def save_workspace_index(self) -> None:
        try:
            temporary = self.workspace_index_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.workspace_index, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.workspace_index_path)
        except OSError as error:
            self.statusBar().showMessage(f"Could not save workspace organization: {error}", 5000)

    def document_metadata(self, path: Path) -> dict[str, Any]:
        raw = self.workspace_index.get(path.name, {})
        return raw if isinstance(raw, dict) else {}

    def edit_document_organization(self, path: Path) -> None:
        current = self.document_metadata(path)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Organize · {path.stem}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.addWidget(QLabel("Tags (comma separated)"))
        tags = QLineEdit(", ".join(current.get("tags", [])))
        layout.addWidget(tags)
        layout.addWidget(QLabel("Folder"))
        folder = QLineEdit(str(current.get("folder", "")))
        folder.setPlaceholderText("For example: School / Biology")
        layout.addWidget(folder)
        favorite = QCheckBox("Add to favorites")
        favorite.setChecked(bool(current.get("favorite", False)))
        layout.addWidget(favorite)
        layout.addWidget(QLabel("Related documents (one filename per line)"))
        related = QPlainTextEdit("\n".join(current.get("links", [])))
        related.setPlaceholderText("Example: Chemistry notes.rnote")
        related.setMaximumHeight(110)
        layout.addWidget(related)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        dialog.setStyleSheet(WORKSPACE_STYLE)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.workspace_index[path.name] = {
            "tags": sorted({tag.strip().lstrip("#") for tag in tags.text().split(",") if tag.strip()}),
            "folder": folder.text().strip().strip("/\\"),
            "favorite": favorite.isChecked(),
            "links": [line.strip() for line in related.toPlainText().splitlines() if line.strip()],
        }
        self.save_workspace_index()
        self.refresh_documents()

    def show_document_context_menu(self, position) -> None:
        item = self.documents_list.itemAt(position)
        raw_path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not raw_path:
            return
        path = Path(raw_path)
        metadata = self.document_metadata(path)
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        favorite = menu.addAction("Remove from favorites" if metadata.get("favorite") else "Add to favorites")
        organize = menu.addAction("Edit tags, folder, and links…")
        history = menu.addAction("Version history…") if path.suffix.lower() in {".rnote", ".rboard"} else None
        available = {candidate.name: candidate for candidate in self.document_files()}
        related_names = list(metadata.get("links", []))
        related_names.extend(
            name for name, record in self.workspace_index.items()
            if isinstance(record, dict) and path.name in record.get("links", [])
        )
        related_menu = menu.addMenu("Open related document")
        related_actions = {}
        for name in dict.fromkeys(related_names):
            related_path = available.get(name)
            if related_path and related_path != path:
                action = related_menu.addAction(related_path.stem)
                related_actions[action] = related_path
        related_menu.setEnabled(bool(related_actions))
        chosen = menu.exec(self.documents_list.mapToGlobal(position))
        if chosen == rename:
            self.rename_document(path)
        elif chosen == favorite:
            metadata["favorite"] = not bool(metadata.get("favorite"))
            self.workspace_index[path.name] = metadata
            self.save_workspace_index()
            self.refresh_documents()
        elif chosen == organize:
            self.edit_document_organization(path)
        elif history is not None and chosen == history:
            self.restore_document_version(path)
        elif chosen in related_actions:
            self.open_selected_document_for_path(related_actions[chosen])

    def rename_document(self, path: Path) -> None:
        """Rename a workspace document and keep its local assets and links together."""
        title, accepted = QInputDialog.getText(
            self, "Rename document", "New name", QLineEdit.EchoMode.Normal, path.stem
        )
        if not accepted or not title.strip():
            return
        if title.strip() == path.stem:
            return
        target = self.unique_path(title, path.suffix)
        if target == path:
            return
        old_asset_dir = path.with_name(f"{path.stem}_assets")
        new_asset_dir = target.with_name(f"{target.stem}_assets")
        history_dir = self.workspace_dir / ".robustnotes-recovery"
        if new_asset_dir.exists():
            QMessageBox.warning(self, "Name already in use", "A document or its saved files already use that name.")
            return

        matching_windows = [
            window for window in self.open_windows
            if (isinstance(window, NotebookWindow) and window.document_path == path)
            or (isinstance(window, TextFileWindow) and window.path == path)
        ]
        for window in matching_windows:
            if isinstance(window, NotebookWindow):
                window.save_document()
                if window.document_save_timer.isActive():
                    window.document_save_timer.stop()
            else:
                window.save_timer.stop()
                window.save_file()

        snapshots = sorted(history_dir.glob(f"{path.name}.*.zip")) if history_dir.exists() else []
        snapshot_moves = [(item, item.with_name(target.name + item.name[len(path.name):])) for item in snapshots]
        if any(destination.exists() for _, destination in snapshot_moves):
            QMessageBox.warning(self, "Name already in use", "Saved history already exists for that name.")
            return

        moved: list[tuple[Path, Path]] = []
        try:
            path.rename(target)
            moved.append((target, path))
            if old_asset_dir.exists():
                old_asset_dir.rename(new_asset_dir)
                moved.append((new_asset_dir, old_asset_dir))
            for source, destination in snapshot_moves:
                source.rename(destination)
                moved.append((destination, source))

            # Stored image references are workspace-relative and include the asset directory name.
            if target.suffix.lower() in {".rnote", ".rboard"}:
                payload = json.loads(target.read_text(encoding="utf-8"))
                old_prefix = f"{path.stem}_assets/"
                new_prefix = f"{target.stem}_assets/"
                for page in payload.get("pages", []):
                    page_image_ref = page.get("image")
                    if isinstance(page_image_ref, str):
                        normalized_ref = page_image_ref.replace("\\", "/")
                        if normalized_ref.startswith(old_prefix):
                            page["image"] = new_prefix + normalized_ref[len(old_prefix):]
                    for drawing in page.get("drawings", []):
                        image_ref = drawing.get("image")
                        if isinstance(image_ref, str):
                            normalized_ref = image_ref.replace("\\", "/")
                            if normalized_ref.startswith(old_prefix):
                                drawing["image"] = new_prefix + normalized_ref[len(old_prefix):]
                temp_path = target.with_suffix(target.suffix + ".tmp")
                temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temp_path, target)

            record = self.workspace_index.pop(path.name, None)
            if isinstance(record, dict):
                self.workspace_index[target.name] = record
            for record in self.workspace_index.values():
                if isinstance(record, dict) and isinstance(record.get("links"), list):
                    record["links"] = [target.name if name == path.name else name for name in record["links"]]

            for window in matching_windows:
                if isinstance(window, NotebookWindow):
                    window.document_path = target
                    window.document_title = target.stem
                    window.setWindowTitle(f"{target.stem} — RobustNotes Studio")
                    window._saved_page_image_keys.clear()
                    window._saved_canvas_image_keys.clear()
                else:
                    window.path = target
                    window.setWindowTitle(f"{target.name} — RobustNotes")
                    title_labels = window.findChildren(QLabel, "Brand")
                    if title_labels:
                        title_labels[0].setText(target.name)
            self.save_workspace_index()
            self.refresh_documents()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            for source, destination in reversed(moved):
                try:
                    if source.exists() and not destination.exists():
                        source.rename(destination)
                except OSError:
                    pass
            QMessageBox.critical(self, "Rename failed", f"Could not rename the document.\n\n{error}")

    def open_selected_document_for_path(self, path: Path) -> None:
        if path.suffix.lower() in {".rnote", ".rboard"}:
            self.open_notebook(path, "whiteboard" if path.suffix.lower() == ".rboard" else "notebook")
        else:
            self.open_text_file(path)

    def restore_document_version(self, path: Path) -> None:
        history_dir = self.workspace_dir / ".robustnotes-recovery"
        snapshots = sorted(history_dir.glob(f"{path.name}.*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not snapshots:
            QMessageBox.information(self, "No saved versions", "A recovery snapshot will be created after the next edit is saved.")
            return
        labels = [datetime.fromtimestamp(snapshot.stat().st_mtime).strftime("%b %d, %Y  ·  %I:%M %p") for snapshot in snapshots]
        label, accepted = QInputDialog.getItem(self, "Restore a saved version", "Available snapshots:", labels, 0, False)
        if not accepted:
            return
        snapshot = snapshots[labels.index(label)]
        answer = QMessageBox.question(
            self,
            "Restore version",
            f"Replace {path.name} and its page backgrounds with the selected saved version?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for window in list(self.open_windows):
            if isinstance(window, NotebookWindow) and window.document_path == path:
                window.close()
        try:
            with zipfile.ZipFile(snapshot) as archive:
                restore_workspace_archive(archive, self.workspace_dir)
            self.refresh_documents()
            QMessageBox.information(self, "Version restored", "The selected version is ready to reopen.")
        except (OSError, zipfile.BadZipFile, ValueError) as error:
            QMessageBox.critical(self, "Restore failed", f"Could not restore the selected version.\n\n{error}")

    def create_encrypted_backup(self) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            QMessageBox.warning(self, "Backup unavailable", "Install the cryptography package to create encrypted backups.")
            return
        destination = self.settings.value("backupDestination", "")
        folder = QFileDialog.getExistingDirectory(self, "Choose encrypted backup folder", str(destination or self.workspace_dir))
        if not folder:
            return
        password, accepted = QInputDialog.getText(self, "Encrypt backup", "Backup password:", QLineEdit.EchoMode.Password)
        if not accepted or not password:
            return
        confirm, accepted = QInputDialog.getText(self, "Confirm backup password", "Enter the password again:", QLineEdit.EchoMode.Password)
        if not accepted:
            return
        if password != confirm or len(password) < 8:
            QMessageBox.warning(self, "Password not accepted", "Passwords must match and contain at least 8 characters.")
            return
        try:
            buffer = io.BytesIO()
            supported = {".rnote", ".rboard", ".txt", ".md", ".py", ".js", ".ts", ".json", ".html", ".css", ".png", ".pdf"}
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in self.workspace_dir.rglob("*"):
                    if path.is_symlink() or not path.is_file() or path.name == "settings.ini":
                        continue
                    if path.suffix.lower() in supported or path.name == ".robustnotes-index.json":
                        archive.write(path, path.relative_to(self.workspace_dir))
            salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000, dklen=32)
            encrypted = AESGCM(key).encrypt(nonce, buffer.getvalue(), b"RobustNotesBackup1")
            output = Path(folder) / f"RobustNotes-{datetime.now().strftime('%Y%m%d-%H%M%S')}.rnbackup"
            output.write_bytes(b"RNBACKUP1" + salt + nonce + encrypted)
            self.settings.setValue("backupDestination", folder)
            QMessageBox.information(self, "Backup created", f"Encrypted workspace backup saved to:\n{output}")
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            QMessageBox.critical(self, "Backup failed", f"Could not create the encrypted backup.\n\n{error}")

    def restore_encrypted_backup(self) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            QMessageBox.warning(self, "Restore unavailable", "Install the cryptography package to restore encrypted backups.")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Restore encrypted backup", "", "RobustNotes backups (*.rnbackup)")
        if not filename:
            return
        password, accepted = QInputDialog.getText(self, "Unlock backup", "Backup password:", QLineEdit.EchoMode.Password)
        if not accepted:
            return
        try:
            blob = Path(filename).read_bytes()
            if not blob.startswith(b"RNBACKUP1") or len(blob) < 37:
                raise ValueError("This is not a recognized RobustNotes backup.")
            salt, nonce, ciphertext = blob[9:25], blob[25:37], blob[37:]
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000, dklen=32)
            archive_data = AESGCM(key).decrypt(nonce, ciphertext, b"RobustNotesBackup1")
            if self.open_windows:
                answer = QMessageBox.question(
                    self,
                    "Close open documents",
                    "Open documents must close and save before this backup can be restored. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                for window in list(self.open_windows):
                    window.close()
            with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
                restore_workspace_archive(archive, self.workspace_dir)
            self.load_workspace_index()
            self.refresh_documents()
            QMessageBox.information(self, "Restore complete", "The backup contents were restored to this profile's local workspace.")
        except Exception as error:
            QMessageBox.critical(self, "Restore failed", f"Could not unlock or restore this backup.\n\n{error}")

    def show_backup_menu(self) -> None:
        sender = self.sender()
        if not isinstance(sender, QWidget):
            return
        menu = QMenu(self)
        create_action = menu.addAction("Create encrypted backup…")
        restore_action = menu.addAction("Restore encrypted backup…")
        chosen = menu.exec(sender.mapToGlobal(sender.rect().bottomLeft()))
        if chosen == create_action:
            self.create_encrypted_backup()
        elif chosen == restore_action:
            self.restore_encrypted_backup()

    def create_notebook(self):
        title = self.ask_title("My notebook")
        if not title:
            return
        templates = ["Dot grid", "Cornell notes", "Meeting notes", "Weekly planner"]
        template, accepted = QInputDialog.getItem(self, "Choose a notebook template", "Start with:", templates, 0, False)
        if accepted:
            self.open_notebook(self.unique_path(title, ".rnote"), "notebook", template)

    def create_whiteboard(self):
        title = self.ask_title("My whiteboard")
        if title:
            self.open_notebook(self.unique_path(title, ".rboard"), "whiteboard")

    def open_notebook(self, path: Path, document_type: str, template_name: str = "Dot grid"):
        for existing in self.open_windows:
            if isinstance(existing, NotebookWindow) and existing.document_path == path:
                self.show_document(existing, existing.document_title)
                return
        window = NotebookWindow(
            path, document_type, self.refresh_documents, self.workspace_dir / "settings.ini", template_name,
            parent=self.document_stack,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.document_stack.addWidget(window)
        self.open_windows.append(window)
        window.destroyed.connect(lambda: self.release_window(window))
        window.show()
        self.show_document(window, window.document_title)
        self.refresh_documents()

    def create_text_file(self):
        title = self.ask_title("Untitled notes")
        if not title:
            return
        suffixes = [".md", ".txt", ".py"]
        suffix, accepted = QInputDialog.getItem(self, "Choose a file type", "Format", suffixes, 0, False)
        if accepted:
            self.open_text_file(self.unique_path(title, suffix))

    def open_text_file(self, path: Path):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        except OSError as error:
            QMessageBox.warning(self, "Could not create file", f"The text file could not be opened.\n\n{error}")
            return
        for existing in self.open_windows:
            if isinstance(existing, TextFileWindow) and existing.path == path:
                self.show_document(existing, existing.path.name)
                return
        window = TextFileWindow(path, self.refresh_documents, parent=self.document_stack)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.document_stack.addWidget(window)
        self.open_windows.append(window)
        window.destroyed.connect(lambda: self.release_window(window))
        window.show()
        self.show_document(window, window.path.name)
        self.refresh_documents()

    def release_window(self, window):
        if window in self.open_windows:
            self.open_windows.remove(window)

    def document_files(self):
        supported = {".rnote", ".rboard", ".txt", ".md", ".py", ".js", ".ts", ".json", ".html", ".css"}
        if not self.workspace_dir.is_dir():
            return []
        return sorted((p for p in self.workspace_dir.iterdir()
                       if not p.is_symlink() and p.is_file() and p.suffix.lower() in supported),
                      key=lambda p: (not bool(self.document_metadata(p).get("favorite")), -p.stat().st_mtime))

    @staticmethod
    def searchable_document_text(path: Path) -> str:
        pieces = [path.stem, path.name]
        try:
            if path.suffix.lower() in {".rnote", ".rboard"}:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for page in payload.get("pages", []):
                    pieces.extend((str(page.get("title", "")), str(page.get("source_text", "")), str(page.get("ocr_text", ""))))
            else:
                pieces.append(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            pass
        return "\n".join(pieces)

    def refresh_documents(self):
        if not hasattr(self, "documents_list"):
            return
        self.documents_list.clear()
        query = self.search_input.text().casefold().strip() if hasattr(self, "search_input") else ""
        terms = [term for term in query.split() if term]
        for path in self.document_files():
            metadata = self.document_metadata(path)
            searchable = self.searchable_document_text(path)
            metadata_text = " ".join(metadata.get("tags", [])) + " " + str(metadata.get("folder", ""))
            if terms and not all(term in searchable.casefold() or term in metadata_text.casefold() for term in terms):
                continue
            kind = "Whiteboard" if path.suffix == ".rboard" else "Notebook" if path.suffix == ".rnote" else {".py": "Python file", ".md": "Markdown", ".txt": "Text file"}.get(path.suffix.lower(), "Text file")
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%b %d  ·  %I:%M %p")
            except OSError:
                modified = ""
            tags = ", ".join(metadata.get("tags", []))
            folder = str(metadata.get("folder", ""))
            favorite_marker = "★  " if metadata.get("favorite") else ""
            details = "     ·     ".join(part for part in (kind, folder, ("#" + "  #".join(metadata.get("tags", []))) if tags else "", modified) if part)
            backlinks = [name for name, record in self.workspace_index.items() if path.name in record.get("links", [])]
            if backlinks:
                details += f"     ·     linked from {len(backlinks)}"
            item = QListWidgetItem(f"{favorite_marker}{path.stem}\n{details}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.documents_list.addItem(item)
        count = self.documents_list.count()
        self.document_count.setText(f"{count} result{'s' if count != 1 else ''}" if terms else f"{count} item{'s' if count != 1 else ''}")
        if count == 0:
            message = "Your workspace is ready. Create a notebook, text file, or whiteboard to begin." if not terms else "No local notes matched that search."
            self.documents_list.addItem(message)

    def open_selected_document(self, item):
        raw_path = item.data(Qt.ItemDataRole.UserRole)
        if not raw_path:
            return
        path = Path(raw_path)
        if path.suffix.lower() in {".rnote", ".rboard"}:
            self.open_notebook(path, "whiteboard" if path.suffix.lower() == ".rboard" else "notebook")
        else:
            self.open_text_file(path)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # The editors live inside this window now, so flush their pending work
        # before the parent closes and Qt tears down the embedded widgets.
        open_documents = list(self.open_windows)
        for window in open_documents:
            if isinstance(window, NotebookWindow):
                if window.music_dialog is not None:
                    window.music_dialog.end_session()
                window.save_document()
            elif isinstance(window, TextFileWindow):
                window.save_timer.stop()
                window.save_file()
        if open_documents and not self._allow_exit_without_confirmation:
            answer = QMessageBox.question(
                self,
                "Close RobustNotes?",
                "You still have documents open. Their latest changes have been saved. Close the workspace?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("RobustNotes Studio")
    app.setWindowIcon(robustnotes_icon())
    app.setStyle("Fusion")
    window = WorkspaceWindow()
    window.show()
    sys.exit(app.exec())
