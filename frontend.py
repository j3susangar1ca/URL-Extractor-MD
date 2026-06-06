#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  Elite Scraper — Premium Qt6 Interface                           ║
║  Design Language: Material You + Apple HIG Fusion               ║
║  Engineered for mission-critical UX standards                   ║
║                                                                  ║
║  Integrates with EliteScraperBackend through an async bridge     ║
║  that maps backend Stage enums to GUI states with real-time      ║
║  progress signals emitted from a dedicated QThread.              ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import sys
import os
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QProgressBar,
    QTextEdit, QFrame, QGraphicsDropShadowEffect, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QSize, Slot
from PySide6.QtGui import (
    QIcon, QColor, QPainter, QPixmap, QTextCursor, QGuiApplication,
    QFont,
)

# Backend integration
from scraper_backend import (
    EliteScraperBackend, ProgressEvent, ScrapingResult, Stage,
    BackendError, ValidationError, NetworkError, SecurityError, StorageError,
)


# ─────────────────────────────────────────────────────
#  COLOR SYSTEM — Premium palette
# ─────────────────────────────────────────────────────
class Theme:
    """Immutable color system for the Elite Scraper UI.

    Attributes:
        BG_PRIMARY: Main window background.
        BG_SECONDARY: Card surface background.
        BG_TERTIARY: Input field resting background.
        SURFACE_HOVER: Hover state overlay.
        SURFACE_ACTIVE: Pressed/active state overlay.
        ACCENT: Primary accent (actions, links, progress).
        ACCENT_HOVER: Accent hover state.
        ACCENT_LIGHT: Accent with 8% opacity.
        ACCENT_MEDIUM: Accent with 14% opacity.
        TEXT_PRIMARY: Main body text.
        TEXT_SECONDARY: Secondary/muted text.
        TEXT_TERTIARY: Hint/placeholder text.
        TEXT_ON_ACCENT: Text on accent backgrounds.
        SUCCESS: Success status color.
        SUCCESS_LIGHT: Success with 8% opacity.
        WARNING: Warning status color.
        WARNING_LIGHT: Warning with 8% opacity.
        ERROR: Error status color.
        ERROR_LIGHT: Error with 8% opacity.
        BORDER: Default border color.
        BORDER_FOCUS: Focused border color.
        SHADOW_LIGHT: Light shadow (4% opacity).
        SHADOW_MEDIUM: Medium shadow (8% opacity).
        SHADOW_HEAVY: Heavy shadow (12% opacity).
        RADIUS_SM: Small border radius.
        RADIUS_MD: Medium border radius.
        RADIUS_LG: Large border radius.
        RADIUS_XL: Extra-large border radius.
    """

    # Surfaces
    BG_PRIMARY       = "#F8F9FA"  # Premium clean off-white
    BG_SECONDARY     = "#FFFFFF"  # Card surface (pure white)
    BG_TERTIARY      = "#F3F4F6"  # Input box resting color
    SURFACE_HOVER    = "#E5E7EB"
    SURFACE_ACTIVE   = "#D1D5DB"

    # Accent
    ACCENT           = "#2563EB"  # Premium Royal Blue
    ACCENT_HOVER     = "#1D4ED8"
    ACCENT_LIGHT     = "rgba(37, 99, 235, 0.08)"
    ACCENT_MEDIUM    = "rgba(37, 99, 235, 0.14)"

    # Text
    TEXT_PRIMARY      = "#111827"  # Near-black
    TEXT_SECONDARY    = "#4B5563"  # Muted neutral grey
    TEXT_TERTIARY     = "#9CA3AF"  # Hint/placeholder grey
    TEXT_ON_ACCENT    = "#FFFFFF"  # Text on blue buttons

    # Status
    SUCCESS          = "#10B981"  # Emerald
    SUCCESS_LIGHT    = "rgba(16, 185, 129, 0.08)"
    WARNING          = "#F59E0B"  # Amber
    WARNING_LIGHT    = "rgba(245, 158, 11, 0.08)"
    ERROR            = "#EF4444"  # Coral
    ERROR_LIGHT      = "rgba(239, 68, 68, 0.08)"

    # Borders & shadows
    BORDER           = "#E5E7EB"  # Clean light border
    BORDER_FOCUS     = "#2563EB"  # Accent focus
    SHADOW_LIGHT     = "rgba(0, 0, 0, 0.02)"
    SHADOW_MEDIUM    = "rgba(0, 0, 0, 0.04)"
    SHADOW_HEAVY     = "rgba(0, 0, 0, 0.06)"

    # Radius
    RADIUS_SM        = "8px"
    RADIUS_MD        = "12px"
    RADIUS_LG        = "16px"
    RADIUS_XL        = "20px"


# ─────────────────────────────────────────────────────
#  GLOBAL STYLESHEET
# ─────────────────────────────────────────────────────
STYLESHEET = f"""
    /* ═══════════════════════════════════════════════
       MAIN WINDOW
       ═══════════════════════════════════════════════ */
    QMainWindow {{
        background-color: {Theme.BG_PRIMARY};
    }}

    /* ═══════════════════════════════════════════════
       CARD FRAME
       ═══════════════════════════════════════════════ */
    QFrame#card {{
        background-color: {Theme.BG_SECONDARY};
        border: 1px solid {Theme.BORDER};
        border-radius: {Theme.RADIUS_LG};
    }}

    QFrame#cardInner {{
        background-color: transparent;
        border: none;
    }}

    /* ═══════════════════════════════════════════════
       SEPARATOR
       ═══════════════════════════════════════════════ */
    QFrame#separator {{
        background-color: {Theme.BORDER};
        max-height: 1px;
        min-height: 1px;
    }}

    /* ═══════════════════════════════════════════════
       LABELS
       ═══════════════════════════════════════════════ */
    QLabel#titleLabel {{
        color: {Theme.TEXT_PRIMARY};
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.5px;
        padding: 0px;
    }}

    QLabel#subtitleLabel {{
        color: {Theme.TEXT_SECONDARY};
        font-size: 13px;
        font-weight: 400;
        padding: 0px;
        margin-top: 2px;
    }}

    QLabel#sectionLabel {{
        color: {Theme.TEXT_PRIMARY};
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.8px;
        text-transform: uppercase;
        padding: 0px;
        margin-bottom: 2px;
    }}

    QLabel#helperLabel {{
        color: {Theme.TEXT_TERTIARY};
        font-size: 11px;
        font-weight: 400;
        padding: 0px;
        margin-top: 3px;
    }}

    QLabel#statusLabel {{
        color: {Theme.TEXT_SECONDARY};
        font-size: 13px;
        font-weight: 500;
        padding: 0px;
    }}

    QLabel#progressLabel {{
        color: {Theme.TEXT_PRIMARY};
        font-size: 24px;
        font-weight: 700;
        letter-spacing: -0.5px;
    }}

    QLabel#progressUnit {{
        color: {Theme.TEXT_TERTIARY};
        font-size: 13px;
        font-weight: 500;
    }}

    /* ═══════════════════════════════════════════════
       LINE EDIT (INPUT FIELDS)
       ═══════════════════════════════════════════════ */
    QLineEdit {{
        background-color: {Theme.BG_TERTIARY};
        border: 1.5px solid {Theme.BORDER};
        border-radius: {Theme.RADIUS_MD};
        padding: 12px 16px;
        font-size: 14px;
        font-weight: 400;
        color: {Theme.TEXT_PRIMARY};
        selection-background-color: {Theme.ACCENT_MEDIUM};
        selection-color: {Theme.TEXT_PRIMARY};
        min-height: 20px;
    }}

    QLineEdit:hover {{
        border-color: #CBD5E1;
        background-color: {Theme.BG_SECONDARY};
    }}

    QLineEdit:focus {{
        border-color: {Theme.BORDER_FOCUS};
        background-color: {Theme.BG_SECONDARY};
        border-width: 2px;
        padding: 11px 15px;
    }}

    QLineEdit::placeholder {{
        color: {Theme.TEXT_TERTIARY};
        font-style: normal;
    }}

    /* ═══════════════════════════════════════════════
       PRIMARY BUTTON (ACCENT)
       ═══════════════════════════════════════════════ */
    QPushButton#primaryBtn {{
        background-color: {Theme.ACCENT};
        color: {Theme.TEXT_ON_ACCENT};
        border: none;
        border-radius: {Theme.RADIUS_MD};
        padding: 12px 28px;
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 0.2px;
        min-height: 20px;
    }}

    QPushButton#primaryBtn:hover {{
        background-color: {Theme.ACCENT_HOVER};
    }}

    QPushButton#primaryBtn:pressed {{
        background-color: #1E40AF;
    }}

    QPushButton#primaryBtn:disabled {{
        background-color: #E5E7EB;
        color: #9CA3AF;
        border: 1px solid #E5E7EB;
    }}

    /* ═══════════════════════════════════════════════
       SECONDARY BUTTON (OUTLINED)
       ═══════════════════════════════════════════════ */
    QPushButton#secondaryBtn {{
        background-color: transparent;
        color: {Theme.TEXT_PRIMARY};
        border: 1.5px solid {Theme.BORDER};
        border-radius: {Theme.RADIUS_MD};
        padding: 11px 20px;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 0.1px;
        min-height: 20px;
    }}

    QPushButton#secondaryBtn:hover {{
        background-color: {Theme.SURFACE_HOVER};
        border-color: #CBD5E1;
    }}

    QPushButton#secondaryBtn:pressed {{
        background-color: {Theme.SURFACE_ACTIVE};
    }}

    /* ═══════════════════════════════════════════════
       ICON BUTTON (FLAT)
       ═══════════════════════════════════════════════ */
    QPushButton#iconBtn {{
        background-color: transparent;
        border: 1.5px solid {Theme.BORDER};
        border-radius: {Theme.RADIUS_MD};
        padding: 10px;
        min-width: 46px;
        max-width: 46px;
        min-height: 46px;
        max-height: 46px;
    }}

    QPushButton#iconBtn:hover {{
        background-color: {Theme.SURFACE_HOVER};
        border-color: #CBD5E1;
    }}

    QPushButton#iconBtn:pressed {{
        background-color: {Theme.SURFACE_ACTIVE};
    }}

    /* ═══════════════════════════════════════════════
       DANGER BUTTON
       ═══════════════════════════════════════════════ */
    QPushButton#dangerBtn {{
        background-color: transparent;
        color: {Theme.ERROR};
        border: 1.5px solid rgba(239, 68, 68, 0.3);
        border-radius: {Theme.RADIUS_MD};
        padding: 11px 20px;
        font-size: 13px;
        font-weight: 600;
        min-height: 20px;
    }}

    QPushButton#dangerBtn:hover {{
        background-color: {Theme.ERROR_LIGHT};
        border-color: {Theme.ERROR};
    }}

    /* ═══════════════════════════════════════════════
       PROGRESS BAR
       ═══════════════════════════════════════════════ */
    QProgressBar {{
        background-color: {Theme.SURFACE_ACTIVE};
        border: none;
        border-radius: 6px;
        min-height: 10px;
        max-height: 10px;
        text-align: center;
    }}

    QProgressBar::chunk {{
        background: qlineargradient(
            x1:0, y1:0, x2:1, y2:0,
            stop:0 #2563EB,
            stop:1 #60A5FA
        );
        border-radius: 6px;
    }}

    /* ═══════════════════════════════════════════════
       LOG TEXT EDIT
       ═══════════════════════════════════════════════ */
    QTextEdit#logConsole {{
        background-color: #F9FAFB;
        border: 1px solid {Theme.BORDER};
        border-radius: {Theme.RADIUS_MD};
        padding: 14px 16px;
        font-family: 'JetBrains Mono', 'SF Mono', 'Fira Code', 'Consolas', monospace;
        font-size: 12px;
        color: {Theme.TEXT_SECONDARY};
        selection-background-color: {Theme.ACCENT_MEDIUM};
        line-height: 1.65;
    }}

    QTextEdit#logConsole QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 4px 2px 4px 0px;
        border-radius: 3px;
    }}

    QTextEdit#logConsole QScrollBar::handle:vertical {{
        background: #D1D5DB;
        border-radius: 3px;
        min-height: 30px;
    }}

    QTextEdit#logConsole QScrollBar::handle:vertical:hover {{
        background: #9CA3AF;
    }}

    QTextEdit#logConsole QScrollBar::add-line:vertical,
    QTextEdit#logConsole QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QTextEdit#logConsole QScrollBar::add-page:vertical,
    QTextEdit#logConsole QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    /* ═══════════════════════════════════════════════
       STATUS CHIP
       ═══════════════════════════════════════════════ */
    QFrame#statusChip {{
        border-radius: 14px;
        padding: 4px 12px;
    }}

    /* ═══════════════════════════════════════════════
       SCROLLBAR GLOBAL
       ═══════════════════════════════════════════════ */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical {{
        background: #D1D5DB;
        border-radius: 4px;
        min-height: 40px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: #9CA3AF;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
"""


# ─────────────────────────────────────────────────────
#  SHADOW HELPERS
# ─────────────────────────────────────────────────────
def apply_card_shadow(
    widget: QWidget,
    blur: int = 32,
    ox: int = 0,
    oy: int = 4,
    alpha: float = 0.06,
) -> None:
    """Apply a subtle card shadow to the given widget.

    Args:
        widget: Target widget to receive the shadow effect.
        blur: Blur radius in pixels.
        ox: Horizontal offset in pixels.
        oy: Vertical offset in pixels.
        alpha: Shadow opacity (0.0–1.0).
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(ox, oy)
    shadow.setColor(QColor(0, 0, 0, int(255 * alpha)))
    widget.setGraphicsEffect(shadow)


def apply_elevated_shadow(widget: QWidget) -> None:
    """Apply an elevated (heavier) shadow for modal or prominent surfaces.

    Args:
        widget: Target widget to receive the shadow effect.
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(48)
    shadow.setOffset(0, 8)
    shadow.setColor(QColor(0, 0, 0, 20))
    widget.setGraphicsEffect(shadow)


# ─────────────────────────────────────────────────────
#  SVG ICON GENERATOR
# ─────────────────────────────────────────────────────
class Icons:
    """Factory for SVG-based QIcon instances with color substitution."""

    @staticmethod
    def _svg_icon(svg_data: str, color: str = Theme.TEXT_PRIMARY) -> QIcon:
        """Create a QIcon from an SVG string with color substitution.

        Args:
            svg_data: Raw SVG markup with ``{{COLOR}}`` placeholders.
            color: CSS color value to substitute.

        Returns:
            Rendered QIcon at 64x64 resolution.
        """
        svg = svg_data.replace("{{COLOR}}", color)
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtCore import QByteArray
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def folder() -> QIcon:
        """Return a folder icon in the primary text color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()

    @staticmethod
    def download() -> QIcon:
        """Return a download/extraction icon in the on-accent color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2.2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg, Theme.TEXT_ON_ACCENT)
        except Exception:
            return QIcon()

    @staticmethod
    def stop() -> QIcon:
        """Return a stop icon in the primary text color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18"
            viewBox="0 0 24 24" fill="{{COLOR}}" stroke="none">
            <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()

    @staticmethod
    def link() -> QIcon:
        """Return a link/URL icon in the tertiary text color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg, Theme.TEXT_TERTIARY)
        except Exception:
            return QIcon()

    @staticmethod
    def file() -> QIcon:
        """Return a file icon in the tertiary text color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg, Theme.TEXT_TERTIARY)
        except Exception:
            return QIcon()

    @staticmethod
    def clear() -> QIcon:
        """Return a clear/trash icon in the primary text color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg)
        except Exception:
            return QIcon()

    @staticmethod
    def check() -> QIcon:
        """Return a checkmark icon in the success color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2.5"
            stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg, Theme.SUCCESS)
        except Exception:
            return QIcon()

    @staticmethod
    def alert() -> QIcon:
        """Return an alert/warning icon in the error color."""
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"
            viewBox="0 0 24 24" fill="none" stroke="{{COLOR}}" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>'''
        try:
            return Icons._svg_icon(svg, Theme.ERROR)
        except Exception:
            return QIcon()


# ─────────────────────────────────────────────────────
#  LOG LEVELS
# ─────────────────────────────────────────────────────
class LogLevel:
    """Structured log level constants with color and prefix mappings.

    Attributes:
        INFO: Informational messages.
        SUCCESS: Successful operation messages.
        WARNING: Warning messages.
        ERROR: Error messages.
        DEBUG: Debug/trace messages.
        COLORS: Mapping of level name to CSS color.
        PREFIXES: Mapping of level name to display prefix.
    """

    INFO    = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR   = "error"
    DEBUG   = "debug"

    COLORS: dict[str, str] = {
        "info":    Theme.TEXT_SECONDARY,
        "success": Theme.SUCCESS,
        "warning": Theme.WARNING,
        "error":   Theme.ERROR,
        "debug":   Theme.TEXT_TERTIARY,
    }

    PREFIXES: dict[str, str] = {
        "info":    "",
        "success": "\u2713 ",
        "warning": "\u26A0 ",
        "error":   "\u2717 ",
        "debug":   "\u203A ",
    }


# ─────────────────────────────────────────────────────
#  STATUS MAP — Backend stage → GUI label + color
# ─────────────────────────────────────────────────────
STATUS_MAP: dict[str, tuple[str, str]] = {
    "idle":        ("Listo", Theme.TEXT_SECONDARY),
    "connecting":  ("Conectando...", Theme.ACCENT),
    "downloading": ("Descargando HTML...", Theme.ACCENT),
    "waf_detected": ("Resolviendo protecci\u00f3n antibot...", Theme.WARNING),
    "parsing":     ("Limpiando ruido y aislando contenido principal...", Theme.ACCENT),
    "saving":      ("Guardando documento .md...", Theme.ACCENT),
    "done":        ("Extracci\u00f3n completada", Theme.SUCCESS),
    "error":       ("Error", Theme.ERROR),
}

# Stage name displayed next to the progress percentage
STAGE_LABELS: dict[str, str] = {
    "idle":        "",
    "connecting":  "Conectando...",
    "downloading": "Descargando...",
    "waf_detected": "Resolviendo WAF...",
    "parsing":     "Parseando...",
    "saving":      "Guardando...",
    "done":        "Completado",
    "error":       "Error",
}


# ─────────────────────────────────────────────────────
#  SCRAPE WORKER — Async bridge between Qt and backend
# ─────────────────────────────────────────────────────
class ScrapeWorker(QObject):
    """Worker that executes the scraping pipeline in a QThread via asyncio bridge.

    Bridges Qt signals with the async ``EliteScraperBackend`` by creating a
    dedicated ``asyncio`` event loop inside the worker thread. The backend's
    ``ProgressEvent`` callbacks are mapped to Qt signals that safely cross
    the thread boundary.

    Signals:
        progress_updated: Emitted with the current percentage (0–100).
        stage_changed: Emitted with ``(stage_name, message)`` on stage transitions.
        log_message: Emitted with ``(message, level)`` for log console updates.
        status_changed: Emitted with a status key from ``STATUS_MAP``.
        finished: Emitted with the ``ScrapingResult`` or ``None`` on completion.
        warning_state: Emitted with ``True`` when WAF/CAPTCHA is active.
    """

    progress_updated = Signal(int)           # percentage 0-100
    stage_changed    = Signal(str, str)       # stage_name, message
    log_message      = Signal(str, str)       # message, level
    status_changed   = Signal(str)            # STATUS_MAP key
    finished         = Signal(object)         # ScrapingResult or None
    warning_state    = Signal(bool)           # True = show warning color

    def __init__(self, url: str, filename: str, save_path: str, preview_only: bool = False) -> None:
        """Initialize the scrape worker.

        Args:
            url: Target URL to scrape.
            filename: Output filename (without extension).
            save_path: Directory where the output will be saved.
            preview_only: Whether to perform a dry-run metadata preview.
        """
        super().__init__()
        self.url: str = url
        self.filename: str = filename
        self.save_path: str = save_path
        self.preview_only: bool = preview_only
        self._is_cancelled: bool = False
        self._backend: Optional[EliteScraperBackend] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def cancel(self) -> None:
        """Request cancellation of the in-progress scraping pipeline."""
        self._is_cancelled = True
        if self._loop and self._cancel_event:
            self._loop.call_soon_threadsafe(self._cancel_event.set)

    def run(self) -> None:
        """Entry point for the QThread.

        Creates a local asyncio event loop and runs the scraping pipeline
        to completion, handling all exceptions and ensuring the loop is
        properly closed.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._execute_pipeline())
        except Exception as exc:
            self.log_message.emit(f"Error fatal: {exc}", LogLevel.ERROR)
            self.status_changed.emit("error")
            self.finished.emit(None)
        finally:
            self._loop.close()
            self._loop = None

    async def _execute_pipeline(self) -> None:
        """Execute the full scraping pipeline using ``EliteScraperBackend``.

        Instantiates a backend, invokes ``scrape_single`` with the progress
        callback bridge, and emits the appropriate terminal signals based
        on the result.
        """
        self._backend = EliteScraperBackend()
        self._cancel_event = asyncio.Event()
        try:
            result = await self._backend.scrape_single(
                url=self.url,
                output_filename=self.filename,
                output_directory=self.save_path,
                callback=self._on_progress_event,
                cancel_event=self._cancel_event,
                preview_only=self.preview_only,
            )
            if result.success:
                self.progress_updated.emit(100)
                self.status_changed.emit("done")
                if self.preview_only:
                    self.log_message.emit(
                        "Previsualizaci\u00f3n finalizada con \u00e9xito (los datos se mostraron en la consola).",
                        LogLevel.SUCCESS,
                    )
                else:
                    self.log_message.emit(
                        f"Extracci\u00f3n completada: {result.output_path}",
                        LogLevel.SUCCESS,
                    )
            else:
                self.status_changed.emit("error")
                self.log_message.emit(f"Fallo: {result.error}", LogLevel.ERROR)
            self.finished.emit(result)
        except asyncio.CancelledError:
            self.status_changed.emit("error")
            self.log_message.emit("Operaci\u00f3n cancelada por el usuario.", LogLevel.WARNING)
            res = ScrapingResult(
                success=False,
                url=self.url,
                status_code=None,
                error="Cancelado por el usuario",
            )
            self.finished.emit(res)
        except Exception as exc:
            self.status_changed.emit("error")
            self.log_message.emit(f"Error: {exc}", LogLevel.ERROR)
            self.finished.emit(None)
        finally:
            await self._backend.close()

    def _on_progress_event(self, event: ProgressEvent) -> None:
        """Callback bridge: maps backend ``ProgressEvent`` to Qt signals.

        This method is invoked synchronously from within the asyncio event
        loop running in the worker thread. Qt signals are thread-safe and
        will be delivered to the main thread's event loop.

        Args:
            event: The progress event emitted by the backend pipeline.
        """
        # Update progress bar
        self.progress_updated.emit(event.percent)

        # Map Stage to status string and log message
        stage = event.stage
        if stage == Stage.CONNECTING:
            self.status_changed.emit("connecting")
            self.log_message.emit(event.message, LogLevel.INFO)
        elif stage == Stage.DOWNLOADING:
            self.status_changed.emit("downloading")
            self.log_message.emit(event.message, LogLevel.DEBUG)
        elif stage == Stage.WAF_DETECTED:
            self.status_changed.emit("waf_detected")
            self.warning_state.emit(True)
            self.log_message.emit(
                "Protecci\u00f3n antibot detectada. Resolviendo...",
                LogLevel.WARNING,
            )
        elif stage == Stage.CAPTCHA_SOLVING:
            self.status_changed.emit("waf_detected")
            self.warning_state.emit(True)
            self.log_message.emit("Resolviendo CAPTCHA...", LogLevel.WARNING)
        elif stage == Stage.PARSING:
            self.status_changed.emit("parsing")
            self.warning_state.emit(False)
            self.log_message.emit(
                "Limpiando ruido y aislando contenido principal...",
                LogLevel.INFO,
            )
        elif stage == Stage.SAVING:
            self.status_changed.emit("saving")
            self.log_message.emit(
                "Guardando documento .md...", LogLevel.INFO
            )
        elif stage == Stage.COMPLETED:
            self.log_message.emit(event.message, LogLevel.SUCCESS)
        elif stage == Stage.ERROR:
            self.status_changed.emit("error")
            self.log_message.emit(event.message, LogLevel.ERROR)
        else:
            self.log_message.emit(event.message, LogLevel.INFO)


# ─────────────────────────────────────────────────────
#  MAIN WINDOW
# ─────────────────────────────────────────────────────
class EliteScraperUI(QMainWindow):
    """Main application window for the Elite Scraper GUI.

    Provides URL input, filename configuration, destination selection,
    real-time progress tracking with stage-aware status indicators,
    and an activity log console. Integrates with ``EliteScraperBackend``
    via the ``ScrapeWorker`` async bridge.

    Attributes:
        _worker: The current ``ScrapeWorker`` instance, or ``None``.
        _thread: The current ``QThread`` hosting the worker, or ``None``.
        _is_extracting: Whether an extraction is currently in progress.
    """

    def __init__(self) -> None:
        """Initialize the Elite Scraper main window and all sub-widgets."""
        super().__init__()
        self._worker: Optional[ScrapeWorker] = None
        self._thread: Optional[QThread] = None
        self._is_extracting: bool = False

        self._setup_window()
        self._build_ui()
        self._connect_signals()
        self._apply_initial_state()

    # ─── WINDOW CONFIG ───────────────────────────────
    def _setup_window(self) -> None:
        """Configure the window title, size, stylesheet, and screen centering."""
        self.setWindowTitle("Elite Scraper")
        self.setMinimumSize(720, 840)
        self.resize(760, 880)
        self.setStyleSheet(STYLESHEET)

        # Center on screen
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2
            y = (geo.height() - self.height()) // 2
            self.move(x, y)

    # ─── UI CONSTRUCTION ─────────────────────────────
    def _build_ui(self) -> None:
        """Construct the complete UI layout with all widgets."""
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(0)

        # ── HEADER ──
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(4)

        title = QLabel("Elite Scraper")
        title.setObjectName("titleLabel")
        header_layout.addWidget(title)

        subtitle = QLabel(
            "Extrae contenido web con motor anti-WAF y salida RAG optimizada."
        )
        subtitle.setObjectName("subtitleLabel")
        header_layout.addWidget(subtitle)

        root_layout.addWidget(header_widget)
        root_layout.addSpacing(14)

        # ── MAIN CARD ──
        card = QFrame()
        card.setObjectName("card")
        apply_card_shadow(card, blur=40, oy=6, alpha=0.07)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(0)

        # === URL Section ===
        url_section = QVBoxLayout()
        url_section.setSpacing(6)

        url_label = QLabel("URL")
        url_label.setObjectName("sectionLabel")
        url_section.addWidget(url_label)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://ejemplo.com/articulo")
        self.url_input.setMinimumHeight(42)
        self.url_input.addAction(Icons.link(), QLineEdit.ActionPosition.LeadingPosition)
        url_section.addWidget(self.url_input)

        url_helper = QLabel(
            "Soporta HTTP/HTTPS. Motor de extracci\u00f3n con evasi\u00f3n WAF integrada."
        )
        url_helper.setObjectName("helperLabel")
        url_section.addWidget(url_helper)

        card_layout.addLayout(url_section)
        card_layout.addSpacing(12)

        # === Filename Section ===
        file_section = QVBoxLayout()
        file_section.setSpacing(6)

        file_label = QLabel("FILENAME")
        file_label.setObjectName("sectionLabel")
        file_section.addWidget(file_label)

        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText("auto-detectado")
        self.filename_input.setMinimumHeight(42)
        self.filename_input.addAction(Icons.file(), QLineEdit.ActionPosition.LeadingPosition)
        file_section.addWidget(self.filename_input)

        card_layout.addLayout(file_section)
        card_layout.addSpacing(12)

        # === Destination Section ===
        dest_section = QVBoxLayout()
        dest_section.setSpacing(6)

        dest_label = QLabel("SAVE TO")
        dest_label.setObjectName("sectionLabel")
        dest_section.addWidget(dest_label)

        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Select a folder\u2026")
        self.path_input.setReadOnly(True)
        self.path_input.setMinimumHeight(42)
        self.path_input.addAction(Icons.folder(), QLineEdit.ActionPosition.LeadingPosition)

        default_path = str(Path.home() / "Downloads")
        self.path_input.setText(default_path)

        self.browse_btn = QPushButton()
        self.browse_btn.setObjectName("iconBtn")
        self.browse_btn.setIcon(Icons.folder())
        self.browse_btn.setIconSize(QSize(20, 20))
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        self.browse_btn.setToolTip("Browse folder")

        dest_row.addWidget(self.path_input, 1)
        dest_row.addWidget(self.browse_btn)

        dest_section.addLayout(dest_row)

        dest_helper = QLabel(f"Default: {default_path}")
        dest_helper.setObjectName("helperLabel")
        dest_section.addWidget(dest_helper)

        card_layout.addLayout(dest_section)
        card_layout.addSpacing(14)

        # === Separator ===
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        card_layout.addWidget(sep)
        card_layout.addSpacing(14)

        # === Progress Section ===
        progress_section = QVBoxLayout()
        progress_section.setSpacing(10)

        # Header row with status + percentage
        prog_header = QHBoxLayout()
        prog_header.setSpacing(12)

        progress_title = QLabel("PROGRESS")
        progress_title.setObjectName("sectionLabel")
        prog_header.addWidget(progress_title)

        prog_header.addStretch()

        self.progress_pct = QLabel("0%")
        self.progress_pct.setObjectName("progressLabel")
        prog_header.addWidget(self.progress_pct)

        self.speed_label = QLabel("")
        self.speed_label.setObjectName("progressUnit")
        prog_header.addWidget(self.speed_label)

        progress_section.addLayout(prog_header)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(10)
        self.progress_bar.setMaximumHeight(10)
        progress_section.addWidget(self.progress_bar)

        # Status row
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.status_label = QLabel("Listo")
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.status_label)

        status_row.addStretch()

        self.size_label = QLabel("")
        self.size_label.setObjectName("statusLabel")
        status_row.addWidget(self.size_label)

        progress_section.addLayout(status_row)

        card_layout.addLayout(progress_section)
        card_layout.addSpacing(14)

        # === Separator ===
        sep2 = QFrame()
        sep2.setObjectName("separator")
        sep2.setFrameShape(QFrame.HLine)
        card_layout.addWidget(sep2)
        card_layout.addSpacing(14)

        # === Log Section ===
        log_section = QVBoxLayout()
        log_section.setSpacing(8)

        log_header = QHBoxLayout()
        log_header.setSpacing(8)

        log_title = QLabel("ACTIVITY LOG")
        log_title.setObjectName("sectionLabel")
        log_header.addWidget(log_title)

        log_header.addStretch()

        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setObjectName("secondaryBtn")
        self.clear_log_btn.setCursor(Qt.PointingHandCursor)
        self.clear_log_btn.setFixedHeight(32)
        log_header.addWidget(self.clear_log_btn)

        log_section.addLayout(log_header)

        self.log_console = QTextEdit()
        self.log_console.setObjectName("logConsole")
        self.log_console.setReadOnly(True)
        self.log_console.setMinimumHeight(140)
        self.log_console.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_section.addWidget(self.log_console)

        card_layout.addLayout(log_section)

        root_layout.addWidget(card, 1)
        root_layout.addSpacing(12)

        # === Action Buttons ===
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 0, 4, 0)
        actions_layout.setSpacing(12)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("dangerBtn")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setFixedHeight(42)

        actions_layout.addWidget(self.cancel_btn)
        actions_layout.addStretch()

        self.download_btn = QPushButton("  Iniciar Extracci\u00f3n")
        self.download_btn.setObjectName("primaryBtn")
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setFixedHeight(42)
        self.download_btn.setMinimumWidth(180)
        self.download_btn.setIcon(Icons.download())
        self.download_btn.setIconSize(QSize(18, 18))

        actions_layout.addWidget(self.download_btn)

        root_layout.addWidget(actions_widget)

        # Initial log messages
        self._log(LogLevel.INFO, "Motor de extracci\u00f3n inicializado")
        self._log(LogLevel.INFO, "Listo \u2014 pega una URL para comenzar")

    # ─── SIGNAL CONNECTIONS ──────────────────────────
    def _connect_signals(self) -> None:
        """Wire all widget signals to their handler slots."""
        self.browse_btn.clicked.connect(self._browse_folder)
        self.download_btn.clicked.connect(self._toggle_extraction)
        self.cancel_btn.clicked.connect(self._cancel_extraction)
        self.clear_log_btn.clicked.connect(self._clear_log)

        self.url_input.textChanged.connect(self._validate_inputs)
        self.filename_input.textChanged.connect(self._validate_inputs)

    def _apply_initial_state(self) -> None:
        """Set the initial enabled/visible state of interactive widgets."""
        self.cancel_btn.setVisible(False)
        self.download_btn.setEnabled(False)

    # ─── LOGGING ─────────────────────────────────────
    def _log(self, level: str, message: str) -> None:
        """Append a timestamped, color-coded message to the log console.

        Args:
            level: Log level key from ``LogLevel``.
            message: The message text to display.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = LogLevel.COLORS.get(level, Theme.TEXT_SECONDARY)
        prefix = LogLevel.PREFIXES.get(level, "")

        html = (
            f'<div style="margin-bottom: 2px; line-height: 1.5;">'
            f'<span style="color: {Theme.TEXT_TERTIARY}; font-size: 11px;">'
            f'{timestamp}</span>'
            f'<span style="color: {color}; margin-left: 10px;">'
            f'{prefix}{message}</span>'
            f'</div>'
        )
        self.log_console.moveCursor(QTextCursor.End)
        self.log_console.insertHtml(html)
        self.log_console.insertHtml("<br>")
        self.log_console.moveCursor(QTextCursor.End)

    def _clear_log(self) -> None:
        """Clear the log console and emit a confirmation message."""
        self.log_console.clear()
        self._log(LogLevel.INFO, "Log cleared")

    # ─── VALIDATION ──────────────────────────────────
    def _validate_inputs(self) -> None:
        """Validate URL and filename inputs and update the button state."""
        url = self.url_input.text().strip()
        filename = self.filename_input.text().strip()
        has_input = len(url) > 0 and len(filename) > 0

        self.download_btn.setEnabled(has_input and not self._is_extracting)

        # Auto-generate filename from URL if empty
        if url and not filename:
            try:
                from urllib.parse import urlparse, unquote
                path = urlparse(url).path
                auto_name = unquote(path.split("/")[-1])
                if auto_name:
                    # Strip extension for use as base name (backend adds extensions)
                    base = auto_name.rsplit(".", 1)[0] if "." in auto_name else auto_name
                    self.filename_input.blockSignals(True)
                    self.filename_input.setText(base)
                    self.filename_input.blockSignals(False)
                    self.download_btn.setEnabled(True)
            except Exception:
                pass

    # ─── FOLDER BROWSER ──────────────────────────────
    def _browse_folder(self) -> None:
        """Open a native folder picker dialog and update the path input."""
        current = self.path_input.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder",
            current,
            QFileDialog.ShowDirsOnly,
        )
        if folder:
            self.path_input.setText(folder)
            self._log(LogLevel.INFO, f"Destination: {folder}")

    # ─── EXTRACTION CONTROL ──────────────────────────
    def _toggle_extraction(self) -> None:
        """Toggle between starting and cancelling an extraction."""
        if self._is_extracting:
            self._cancel_extraction()
            return
        self._start_extraction()

    def _start_extraction(self) -> None:
        """Validate inputs, lock the UI, and launch the scrape worker thread."""
        url = self.url_input.text().strip()
        filename = self.filename_input.text().strip()
        save_path = self.path_input.text().strip()

        if not all([url, filename, save_path]):
            self._log(LogLevel.ERROR, "Missing required fields")
            return

        self._is_extracting = True
        self.progress_bar.setValue(0)
        self.progress_pct.setText("0%")
        self.speed_label.setText("")
        self.size_label.setText("")

        # UI state: extracting
        self.download_btn.setText("  Extrayendo\u2026")
        self.download_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.url_input.setReadOnly(True)
        self.filename_input.setReadOnly(True)
        self.browse_btn.setEnabled(False)

        self.status_label.setText("Conectando...")
        self.status_label.setStyleSheet(f"color: {Theme.ACCENT};")

        self._log(LogLevel.INFO, "Iniciando extracci\u00f3n\u2026")

        # Worker thread
        self._thread = QThread()
        self._worker = ScrapeWorker(url, filename, save_path)
        self._worker.moveToThread(self._thread)

        # Connect signals
        self._thread.started.connect(self._worker.run)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.stage_changed.connect(self._on_stage_changed)
        self._worker.log_message.connect(self._log)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.warning_state.connect(self._on_warning_state)
        self._worker.finished.connect(self._on_finished)

        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _cancel_extraction(self) -> None:
        """Request cancellation of the running extraction worker."""
        if self._worker:
            self._worker.cancel()
            self._log(LogLevel.WARNING, "Cancelando extracci\u00f3n\u2026")

    # ─── SIGNAL HANDLERS ─────────────────────────────
    @Slot(int)
    def _on_progress(self, pct: int) -> None:
        """Update the progress bar and percentage label.

        Args:
            pct: Progress percentage from 0 to 100.
        """
        self.progress_bar.setValue(pct)
        self.progress_pct.setText(f"{pct}%")

    @Slot(str, str)
    def _on_stage_changed(self, stage_name: str, message: str) -> None:
        """Handle stage change events from the worker.

        Args:
            stage_name: The name of the new pipeline stage.
            message: A human-readable description of the stage.
        """
        stage_label = STAGE_LABELS.get(stage_name, "")
        if stage_label:
            self.speed_label.setText(stage_label)

    @Slot(str)
    def _on_status_changed(self, status: str) -> None:
        """Map a status key to a label and color, then update the status label.

        Args:
            status: A key from ``STATUS_MAP`` (e.g. ``"connecting"``,
                ``"waf_detected"``, ``"done"``).
        """
        text, color = STATUS_MAP.get(status, ("Desconocido", Theme.TEXT_SECONDARY))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

        # Update speed_label with stage name
        stage_label = STAGE_LABELS.get(status, "")
        if stage_label:
            self.speed_label.setText(stage_label)

    @Slot(bool)
    def _on_warning_state(self, is_warning: bool) -> None:
        """Handle warning state changes for WAF/CAPTCHA detection.

        When ``True``, applies the warning color to the progress bar chunk
        and status label. When ``False``, restores the default accent color.

        Args:
            is_warning: Whether a WAF/CAPTCHA warning is active.
        """
        if is_warning:
            self.status_label.setStyleSheet(f"color: {Theme.WARNING};")
            self.progress_bar.setStyleSheet(
                f"""
                QProgressBar {{
                    background-color: {Theme.SURFACE_ACTIVE};
                    border: none;
                    border-radius: 6px;
                    min-height: 10px;
                    max-height: 10px;
                    text-align: center;
                }}
                QProgressBar::chunk {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 {Theme.WARNING},
                        stop:1 #FFD54F
                    );
                    border-radius: 6px;
                }}
                """
            )
        else:
            # Restore default progress bar style
            self.progress_bar.setStyleSheet("")

    @Slot(object)
    def _on_finished(self, result: Optional[ScrapingResult]) -> None:
        """Handle completion of the scraping pipeline.

        Restores the UI to its idle state and, on success, displays the
        output ``.md`` file path in the size label.

        Args:
            result: The ``ScrapingResult`` from the backend, or ``None``
                if the pipeline failed with an unhandled exception.
        """
        self._is_extracting = False
        self._restore_ui_state()

        if result is not None and result.success:
            # Show the .md output file path
            if result.output_path:
                self.size_label.setText(f"\u2192 {result.output_path}")
            else:
                self.size_label.setText("Documento .md guardado")
        elif result is not None and not result.success:
            self.size_label.setText(f"Fallo: {result.error or 'Error desconocido'}")

    def _restore_ui_state(self) -> None:
        """Restore all interactive widgets to their idle/extraction-ready state."""
        self.download_btn.setText("  Iniciar Extracci\u00f3n")
        self.download_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.url_input.setReadOnly(False)
        self.filename_input.setReadOnly(False)
        self.browse_btn.setEnabled(True)
        self._validate_inputs()

        # Reset progress bar style in case it was in warning mode
        self.progress_bar.setStyleSheet("")


# ─────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────
def main() -> None:
    """Application entry point.

    Configures High-DPI scaling, applies the global font, instantiates
    the main window, and starts the Qt event loop.
    """
    # High-DPI support
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Elite Scraper")
    app.setOrganizationName("Engineered")

    # Global font
    font = QFont()
    font.setFamilies([
        "Segoe UI", "SF Pro Display", "Helvetica Neue",
        "Noto Sans", "Roboto", "sans-serif",
    ])
    font.setPointSize(10)
    font.setWeight(QFont.Normal)
    app.setFont(font)

    window = EliteScraperUI()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
