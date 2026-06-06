"""PySide6/Qt6 frontend adapter for the URL Extractor MD pipeline.

This adapter bridges the Qt event loop with the async ScrapePipeline
through a QThread-based worker that maps pipeline Stage enums to
Qt signals for real-time UI updates.

Requires: PySide6 (not a core dependency — import only when needed).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, QSize, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
    QTextCursor,
    QGuiApplication,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..application.pipeline import CallbackBridge, ScrapePipeline
from ..config import SystemConfig
from ..domain.contracts import (
    CaptchaResolverPort,
    ExtractorPort,
    NetworkPort,
    ProxySelectorPort,
    StoragePort,
    ValidatorPort,
    WAFDetectorPort,
)
from ..domain.models import ProgressEvent, ScrapingResult, Stage


# ────────────────────────────────────────────────────────
#  Theme
# ────────────────────────────────────────────────────────


class Theme:
    """Immutable color system for the Elite Scraper UI."""

    BG_PRIMARY = "#F8F9FA"
    BG_SECONDARY = "#FFFFFF"
    BG_TERTIARY = "#F3F4F6"
    SURFACE_HOVER = "#E5E7EB"
    SURFACE_ACTIVE = "#D1D5DB"
    ACCENT = "#2563EB"
    ACCENT_HOVER = "#1D4ED8"
    ACCENT_LIGHT = "rgba(37, 99, 235, 0.08)"
    ACCENT_MEDIUM = "rgba(37, 99, 235, 0.14)"
    TEXT_PRIMARY = "#111827"
    TEXT_SECONDARY = "#4B5563"
    TEXT_TERTIARY = "#9CA3AF"
    TEXT_ON_ACCENT = "#FFFFFF"
    SUCCESS = "#10B981"
    SUCCESS_LIGHT = "rgba(16, 185, 129, 0.08)"
    WARNING = "#F59E0B"
    WARNING_LIGHT = "rgba(245, 158, 11, 0.08)"
    ERROR = "#EF4444"
    ERROR_LIGHT = "rgba(239, 68, 68, 0.08)"
    BORDER = "#E5E7EB"
    BORDER_FOCUS = "#2563EB"
    RADIUS_SM = "8px"
    RADIUS_MD = "12px"
    RADIUS_LG = "16px"
    RADIUS_XL = "20px"


# ────────────────────────────────────────────────────────
#  Log levels
# ────────────────────────────────────────────────────────


class LogLevel:
    """Structured log level constants with color and prefix mappings."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    DEBUG = "debug"

    COLORS: dict[str, str] = {
        "info": Theme.TEXT_SECONDARY,
        "success": Theme.SUCCESS,
        "warning": Theme.WARNING,
        "error": Theme.ERROR,
        "debug": Theme.TEXT_TERTIARY,
    }

    PREFIXES: dict[str, str] = {
        "info": "",
        "success": "\u2713 ",
        "warning": "\u26A0 ",
        "error": "\u2717 ",
        "debug": "\u203A ",
    }


# ────────────────────────────────────────────────────────
#  Status map
# ────────────────────────────────────────────────────────

STATUS_MAP: dict[str, tuple[str, str]] = {
    "idle": ("Listo", Theme.TEXT_SECONDARY),
    "connecting": ("Conectando...", Theme.ACCENT),
    "downloading": ("Descargando HTML...", Theme.ACCENT),
    "waf_detected": ("Resolviendo proteccion antibot...", Theme.WARNING),
    "parsing": ("Limpiando ruido...", Theme.ACCENT),
    "saving": ("Guardando documento .md...", Theme.ACCENT),
    "done": ("Extraccion completada", Theme.SUCCESS),
    "error": ("Error", Theme.ERROR),
}


# ────────────────────────────────────────────────────────
#  Stylesheet
# ────────────────────────────────────────────────────────

STYLESHEET = f"""
QMainWindow {{ background-color: {Theme.BG_PRIMARY}; }}
QFrame#card {{ background-color: {Theme.BG_SECONDARY}; border: 1px solid {Theme.BORDER}; border-radius: {Theme.RADIUS_LG}; }}
QFrame#separator {{ background-color: {Theme.BORDER}; max-height: 1px; min-height: 1px; }}
QLabel#titleLabel {{ color: {Theme.TEXT_PRIMARY}; font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }}
QLabel#subtitleLabel {{ color: {Theme.TEXT_SECONDARY}; font-size: 13px; font-weight: 400; margin-top: 2px; }}
QLabel#sectionLabel {{ color: {Theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 700; letter-spacing: 0.8px; text-transform: uppercase; margin-bottom: 2px; }}
QLabel#helperLabel {{ color: {Theme.TEXT_TERTIARY}; font-size: 11px; margin-top: 3px; }}
QLabel#statusLabel {{ color: {Theme.TEXT_SECONDARY}; font-size: 13px; font-weight: 500; }}
QLabel#progressLabel {{ color: {Theme.TEXT_PRIMARY}; font-size: 24px; font-weight: 700; }}
QLabel#progressUnit {{ color: {Theme.TEXT_TERTIARY}; font-size: 13px; font-weight: 500; }}
QLineEdit {{ background-color: {Theme.BG_TERTIARY}; border: 1.5px solid {Theme.BORDER}; border-radius: {Theme.RADIUS_MD}; padding: 12px 16px; font-size: 14px; color: {Theme.TEXT_PRIMARY}; min-height: 20px; }}
QLineEdit:focus {{ border-color: {Theme.BORDER_FOCUS}; background-color: {Theme.BG_SECONDARY}; border-width: 2px; padding: 11px 15px; }}
QLineEdit::placeholder {{ color: {Theme.TEXT_TERTIARY}; }}
QPushButton#primaryBtn {{ background-color: {Theme.ACCENT}; color: {Theme.TEXT_ON_ACCENT}; border: none; border-radius: {Theme.RADIUS_MD}; padding: 12px 28px; font-size: 14px; font-weight: 700; min-height: 20px; }}
QPushButton#primaryBtn:hover {{ background-color: {Theme.ACCENT_HOVER}; }}
QPushButton#primaryBtn:disabled {{ background-color: #E5E7EB; color: #9CA3AF; }}
QPushButton#secondaryBtn {{ background-color: transparent; color: {Theme.TEXT_PRIMARY}; border: 1.5px solid {Theme.BORDER}; border-radius: {Theme.RADIUS_MD}; padding: 11px 20px; font-size: 13px; font-weight: 600; min-height: 20px; }}
QPushButton#secondaryBtn:hover {{ background-color: {Theme.SURFACE_HOVER}; }}
QPushButton#dangerBtn {{ background-color: transparent; color: {Theme.ERROR}; border: 1.5px solid rgba(239, 68, 68, 0.3); border-radius: {Theme.RADIUS_MD}; padding: 11px 20px; font-size: 13px; font-weight: 600; min-height: 20px; }}
QPushButton#dangerBtn:hover {{ background-color: {Theme.ERROR_LIGHT}; border-color: {Theme.ERROR}; }}
QProgressBar {{ background-color: {Theme.SURFACE_ACTIVE}; border: none; border-radius: 6px; min-height: 10px; max-height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #2563EB,stop:1 #60A5FA); border-radius: 6px; }}
QTextEdit#logConsole {{ background-color: #F9FAFB; border: 1px solid {Theme.BORDER}; border-radius: {Theme.RADIUS_MD}; padding: 14px 16px; font-family: 'JetBrains Mono','Consolas',monospace; font-size: 12px; color: {Theme.TEXT_SECONDARY}; }}
"""


# ────────────────────────────────────────────────────────
#  Icons
# ────────────────────────────────────────────────────────


class Icons:
    """Factory for SVG-based QIcon instances with color substitution."""

    @staticmethod
    def _svg_icon(svg_data: str, color: str = Theme.TEXT_PRIMARY) -> QIcon:
        svg = svg_data.replace("{{COLOR}}", color)
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def link() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>'
        try:
            return Icons._svg_icon(svg, Theme.TEXT_TERTIARY)
        except Exception:
            return QIcon()

    @staticmethod
    def file() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'
        try:
            return Icons._svg_icon(svg, Theme.TEXT_TERTIARY)
        except Exception:
            return QIcon()

    @staticmethod
    def folder() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()

    @staticmethod
    def download() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>'
        try:
            return Icons._svg_icon(svg, Theme.TEXT_ON_ACCENT)
        except Exception:
            return QIcon()

    @staticmethod
    def stop() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="{{COLOR}}" stroke="none"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()

    @staticmethod
    def preview() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()

    @staticmethod
    def clear() -> QIcon:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>'
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()


# ────────────────────────────────────────────────────────
#  Shadow helpers
# ────────────────────────────────────────────────────────


def apply_card_shadow(
    widget: QWidget, blur: int = 32, oy: int = 4, alpha: float = 0.06
) -> None:
    """Apply a subtle card shadow."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, oy)
    shadow.setColor(QColor(0, 0, 0, int(255 * alpha)))
    widget.setGraphicsEffect(shadow)


# ────────────────────────────────────────────────────────
#  Scrape Worker
# ────────────────────────────────────────────────────────


class ScrapeWorker(QObject):
    """Worker that executes the scraping pipeline in a QThread via asyncio bridge.

    Signals:
        progress_updated: Current percentage (0-100).
        stage_changed: (stage_name, message) on transitions.
        log_message: (message, level) for log console.
        status_changed: Status key from STATUS_MAP.
        finished: ScrapingResult or None.
        warning_state: True when WAF/CAPTCHA active.
    """

    progress_updated = Signal(int)
    stage_changed = Signal(str, str)
    log_message = Signal(str, str)
    status_changed = Signal(str)
    finished = Signal(object)
    warning_state = Signal(bool)

    def __init__(
        self,
        pipeline: ScrapePipeline,
        url: str,
        filename: str,
        save_path: str,
        preview_only: bool = False,
    ) -> None:
        super().__init__()
        self._pipeline = pipeline
        self.url = url
        self.filename = filename
        self.save_path = save_path
        self.preview_only = preview_only
        self._is_cancelled = False
        self._cancel_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def cancel(self) -> None:
        """Request cancellation."""
        self._is_cancelled = True
        if self._loop and self._cancel_event:
            self._loop.call_soon_threadsafe(self._cancel_event.set)

    def run(self) -> None:
        """Entry point for QThread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._execute())
        except Exception as exc:
            self.log_message.emit(f"Error fatal: {exc}", LogLevel.ERROR)
            self.status_changed.emit("error")
            self.finished.emit(None)
        finally:
            self._loop.close()
            self._loop = None

    async def _execute(self) -> None:
        """Execute pipeline with progress callback bridge."""
        self._cancel_event = asyncio.Event()
        try:
            result = await self._pipeline.execute(
                url=self.url,
                output_filename=self.filename,
                output_directory=self.save_path,
                callback=self._on_progress,
                cancel_event=self._cancel_event,
                preview_only=self.preview_only,
            )
            if result.success:
                self.progress_updated.emit(100)
                self.status_changed.emit("done")
                msg = (
                    "Previsualizacion finalizada."
                    if self.preview_only
                    else f"Extraccion completada: {result.output_path}"
                )
                self.log_message.emit(msg, LogLevel.SUCCESS)
            else:
                self.status_changed.emit("error")
                self.log_message.emit(f"Fallo: {result.error}", LogLevel.ERROR)
            self.finished.emit(result)
        except asyncio.CancelledError:
            self.status_changed.emit("error")
            self.log_message.emit("Operacion cancelada.", LogLevel.WARNING)
            self.finished.emit(
                ScrapingResult(
                    success=False, url=self.url, error="Cancelado por el usuario"
                )
            )
        except Exception as exc:
            self.status_changed.emit("error")
            self.log_message.emit(f"Error: {exc}", LogLevel.ERROR)
            self.finished.emit(None)

    def _on_progress(self, event: ProgressEvent) -> None:
        """Map pipeline ProgressEvent to Qt signals."""
        self.progress_updated.emit(event.percent)
        match event.stage:
            case Stage.CONNECTING:
                self.status_changed.emit("connecting")
                self.log_message.emit(event.message, LogLevel.INFO)
            case Stage.DOWNLOADING:
                self.status_changed.emit("downloading")
                self.log_message.emit(event.message, LogLevel.DEBUG)
            case Stage.WAF_DETECTED:
                self.status_changed.emit("waf_detected")
                self.warning_state.emit(True)
                self.log_message.emit("Proteccion antibot detectada.", LogLevel.WARNING)
            case Stage.CAPTCHA_SOLVING:
                self.status_changed.emit("waf_detected")
                self.warning_state.emit(True)
                self.log_message.emit("Resolviendo CAPTCHA...", LogLevel.WARNING)
            case Stage.PARSING:
                self.status_changed.emit("parsing")
                self.warning_state.emit(False)
                self.log_message.emit("Limpiando ruido...", LogLevel.INFO)
            case Stage.SAVING:
                self.status_changed.emit("saving")
                self.log_message.emit("Guardando .md...", LogLevel.INFO)
            case Stage.COMPLETED:
                self.log_message.emit(event.message, LogLevel.SUCCESS)
            case Stage.ERROR:
                self.status_changed.emit("error")
                self.log_message.emit(event.message, LogLevel.ERROR)
            case _:
                self.log_message.emit(event.message, LogLevel.INFO)


# ────────────────────────────────────────────────────────
#  Main Window
# ────────────────────────────────────────────────────────


class EliteScraperUI(QMainWindow):
    """Main application window for the Elite Scraper GUI."""

    def __init__(self, pipeline: ScrapePipeline) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._worker: ScrapeWorker | None = None
        self._thread: QThread | None = None
        self._is_extracting = False

        self._setup_window()
        self._build_ui()
        self._connect_signals()
        self._apply_initial_state()

    def _setup_window(self) -> None:
        self.setWindowTitle("Elite Scraper")
        self.setMinimumSize(720, 840)
        self.resize(760, 880)
        self.setStyleSheet(STYLESHEET)
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                (geo.width() - self.width()) // 2,
                (geo.height() - self.height()) // 2,
            )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(0)

        # Header
        header = QWidget()
        hl = QVBoxLayout(header)
        hl.setContentsMargins(4, 0, 4, 0)
        hl.setSpacing(4)
        title = QLabel("Elite Scraper")
        title.setObjectName("titleLabel")
        hl.addWidget(title)
        subtitle = QLabel(
            "Extrae contenido web con motor anti-WAF y salida RAG optimizada."
        )
        subtitle.setObjectName("subtitleLabel")
        hl.addWidget(subtitle)
        root.addWidget(header)
        root.addSpacing(14)

        # Card
        card = QFrame()
        card.setObjectName("card")
        apply_card_shadow(card)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 20, 20, 20)
        cl.setSpacing(0)

        # URL
        url_sec = QVBoxLayout()
        url_sec.setSpacing(6)
        url_lbl = QLabel("URL")
        url_lbl.setObjectName("sectionLabel")
        url_sec.addWidget(url_lbl)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://ejemplo.com/articulo")
        self.url_input.setMinimumHeight(42)
        self.url_input.addAction(
            Icons.link(), QLineEdit.ActionPosition.LeadingPosition
        )
        url_sec.addWidget(self.url_input)
        url_help = QLabel("Soporta HTTP/HTTPS. Motor anti-WAF integrado.")
        url_help.setObjectName("helperLabel")
        url_sec.addWidget(url_help)
        cl.addLayout(url_sec)
        cl.addSpacing(12)

        # Filename
        file_sec = QVBoxLayout()
        file_sec.setSpacing(6)
        file_lbl = QLabel("FILENAME")
        file_lbl.setObjectName("sectionLabel")
        file_sec.addWidget(file_lbl)
        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText("auto-detectado")
        self.filename_input.setMinimumHeight(42)
        self.filename_input.addAction(
            Icons.file(), QLineEdit.ActionPosition.LeadingPosition
        )
        file_sec.addWidget(self.filename_input)
        cl.addLayout(file_sec)
        cl.addSpacing(12)

        # Destination
        dest_sec = QVBoxLayout()
        dest_sec.setSpacing(6)
        dest_lbl = QLabel("SAVE TO")
        dest_lbl.setObjectName("sectionLabel")
        dest_sec.addWidget(dest_lbl)
        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select a folder...")
        self.path_input.setReadOnly(True)
        self.path_input.setMinimumHeight(42)
        self.path_input.addAction(
            Icons.folder(), QLineEdit.ActionPosition.LeadingPosition
        )
        default_path = str(Path.home() / "Downloads")
        self.path_input.setText(default_path)
        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("secondaryBtn")
        self.browse_btn.setIcon(Icons.folder())
        self.browse_btn.setIconSize(QSize(20, 20))
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        dest_row.addWidget(self.path_input, 1)
        dest_row.addWidget(self.browse_btn)
        dest_sec.addLayout(dest_row)
        cl.addLayout(dest_sec)
        cl.addSpacing(14)

        # Separator
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        cl.addWidget(sep)
        cl.addSpacing(14)

        # Progress
        prog_sec = QVBoxLayout()
        prog_sec.setSpacing(10)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        prog_sec.addWidget(self.progress_bar)

        self.status_label = QLabel("Listo")
        self.status_label.setObjectName("statusLabel")
        prog_sec.addWidget(self.status_label)
        cl.addLayout(prog_sec)
        cl.addSpacing(14)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.extract_btn = QPushButton("Extraer")
        self.extract_btn.setObjectName("primaryBtn")
        self.extract_btn.setIcon(Icons.download())
        self.extract_btn.setCursor(Qt.PointingHandCursor)

        self.preview_btn = QPushButton("Previsualizar")
        self.preview_btn.setObjectName("secondaryBtn")
        self.preview_btn.setIcon(Icons.preview())
        self.preview_btn.setCursor(Qt.PointingHandCursor)

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setObjectName("dangerBtn")
        self.cancel_btn.setIcon(Icons.stop())
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setEnabled(False)

        btn_row.addWidget(self.extract_btn, 1)
        btn_row.addWidget(self.preview_btn, 1)
        btn_row.addWidget(self.cancel_btn, 1)
        cl.addLayout(btn_row)
        cl.addSpacing(14)

        # Log console
        self.log_console = QTextEdit()
        self.log_console.setObjectName("logConsole")
        self.log_console.setReadOnly(True)
        cl.addWidget(self.log_console)

        root.addWidget(card)

    def _connect_signals(self) -> None:
        self.browse_btn.clicked.connect(self._browse_folder)
        self.extract_btn.clicked.connect(self._start_extraction)
        self.preview_btn.clicked.connect(self._start_preview)
        self.cancel_btn.clicked.connect(self._cancel_extraction)

    def _apply_initial_state(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.extract_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            self.path_input.setText(folder)

    def _start_extraction(self, preview_only: bool = False) -> None:
        url = self.url_input.text().strip()
        if not url:
            self._log("Ingrese una URL valida.", LogLevel.WARNING)
            return
        filename = self.filename_input.text().strip() or "extracted"
        save_path = self.path_input.text().strip()

        self._thread = QThread()
        self._worker = ScrapeWorker(
            pipeline=self._pipeline,
            url=url,
            filename=filename,
            save_path=save_path,
            preview_only=preview_only,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.log_message.connect(self._on_log)
        self._worker.status_changed.connect(self._on_status)
        self._worker.warning_state.connect(self._on_warning)
        self._worker.finished.connect(self._on_finished)
        self._is_extracting = True
        self.extract_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self._thread.start()

    def _start_preview(self) -> None:
        self._start_extraction(preview_only=True)

    def _cancel_extraction(self) -> None:
        if self._worker:
            self._worker.cancel()

    def _on_progress(self, pct: int) -> None:
        self.progress_bar.setValue(pct)

    def _on_log(self, msg: str, level: str) -> None:
        prefix = LogLevel.PREFIXES.get(level, "")
        self.log_console.append(f"{prefix}{msg}")

    def _on_status(self, key: str) -> None:
        label, _ = STATUS_MAP.get(key, ("", Theme.TEXT_SECONDARY))
        self.status_label.setText(label)

    def _on_warning(self, active: bool) -> None:
        pass  # Could change progress bar color

    def _on_finished(self, result: object) -> None:
        self._is_extracting = False
        self.extract_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        self._worker = None


def _log(self, msg: str, level: str) -> None:
    """Append a log message to the console."""
    prefix = LogLevel.PREFIXES.get(level, "")
    self.log_console.append(f"{prefix}{msg}")
