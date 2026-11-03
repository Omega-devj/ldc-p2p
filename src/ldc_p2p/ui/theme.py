"""Design system « Liquid Glass » : graphite profond, accents violets, surfaces translucides.

Qt Widgets ne sait pas flouter ce qui est derrière une fenêtre : l'effet verre est obtenu en peignant
un fond « aurore » dans l'application puis des panneaux translucides à reflet par-dessus. Sous
Windows 11, un vrai flou (Mica/Acrylique) peut être activé en option dans les paramètres.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

# --- palette ---------------------------------------------------------------------------
BG = QColor("#08080E")
BG_2 = QColor("#0E0E18")
ACCENT = QColor("#8B5CF6")
ACCENT_2 = QColor("#A78BFA")
ACCENT_DEEP = QColor("#6D28D9")
TEXT = QColor("#F3F3F8")
TEXT_2 = QColor(235, 235, 245, 158)
TEXT_3 = QColor(235, 235, 245, 96)
SUCCESS = QColor("#34D399")
WARN = QColor("#FBBF24")
DANGER = QColor("#F87171")
GLASS = QColor(255, 255, 255, 14)
GLASS_HOVER = QColor(255, 255, 255, 24)
GLASS_STRONG = QColor(255, 255, 255, 22)
STROKE = QColor(255, 255, 255, 26)

FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", Arial, sans-serif'

PRESENCE_COLORS = {"online": "#34D399", "away": "#FBBF24", "dnd": "#F87171", "offline": "#6B7280", "invisible": "#6B7280"}
PRESENCE_LABELS = {"online": "En ligne", "away": "Absent", "dnd": "Ne pas déranger", "offline": "Hors ligne", "invisible": "Invisible"}

_AVATAR_PAIRS = [("#8B5CF6", "#EC4899"), ("#6366F1", "#22D3EE"), ("#F59E0B", "#EF4444"), ("#10B981", "#3B82F6"),
                 ("#EC4899", "#F59E0B"), ("#14B8A6", "#8B5CF6"), ("#3B82F6", "#A855F7"), ("#F43F5E", "#FB923C")]


def avatar_colors(key: str) -> tuple[QColor, QColor]:
    h = hashlib.sha256(key.encode()).digest()[0] % len(_AVATAR_PAIRS)
    a, b = _AVATAR_PAIRS[h]
    return QColor(a), QColor(b)


def qcolor_css(c: QColor) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha() / 255:.3f})"


def base_font(size: float = 10.0) -> QFont:
    f = QFont()
    for fam in ("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Arial"):
        if fam in QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSizeF(size)
    f.setHintingPreference(QFont.PreferFullHinting if sys.platform == "win32" else QFont.PreferDefaultHinting)
    return f


# --- ressources générées (cases à cocher, flèches) ----------------------------------------
_asset_dir: Path | None = None


def asset_dir() -> Path:
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = Path(tempfile.mkdtemp(prefix="ldc-ui-"))
    return _asset_dir


def _svg_png(name: str, svg: str, size: int = 32) -> str:
    p = asset_dir() / f"{name}.png"
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    pt = QPainter(img)
    pt.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(svg.encode()).render(pt, QRectF(0, 0, size, size))
    pt.end()
    img.save(str(p))
    return p.as_posix()


def build_qss() -> str:
    chk = _svg_png("check", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 12.5 10 17.5 19 7"/></svg>')
    down = _svg_png("down", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#B9B9C8" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>')
    return f"""
* {{ outline: none; }}
QWidget {{ color: #F3F3F8; font-family: {FONT_FAMILY}; font-size: 10pt; }}
QMainWindow, QDialog {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 22pt; font-weight: 700; letter-spacing: -0.5px; }}
QLabel[role="h2"] {{ font-size: 14pt; font-weight: 650; }}
QLabel[role="h3"] {{ font-size: 11pt; font-weight: 600; }}
QLabel[role="muted"] {{ color: rgba(235,235,245,0.62); }}
QLabel[role="faint"] {{ color: rgba(235,235,245,0.40); font-size: 9pt; }}
QLabel[role="section"] {{ color: rgba(235,235,245,0.45); font-size: 8.5pt; font-weight: 700; letter-spacing: 1.2px; }}
QLabel[role="mono"] {{ font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 10pt; }}
QLabel[role="error"] {{ color: #F87171; }}
QLabel[role="ok"] {{ color: #34D399; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px; padding: 9px 12px; selection-background-color: rgba(139,92,246,0.55);
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ background: rgba(255,255,255,0.085); }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid rgba(167,139,250,0.85); background: rgba(255,255,255,0.09);
}}
QLineEdit[invalid="true"] {{ border: 1px solid rgba(248,113,113,0.85); }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({down}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: #15151F; border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
    padding: 4px; selection-background-color: rgba(139,92,246,0.45); outline: none; }}

QPushButton {{
    background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px; padding: 9px 18px; font-weight: 600;
}}
QPushButton:hover {{ background: rgba(255,255,255,0.13); }}
QPushButton:pressed {{ background: rgba(255,255,255,0.06); }}
QPushButton:disabled {{ color: rgba(235,235,245,0.30); background: rgba(255,255,255,0.04); }}
QPushButton[kind="primary"] {{
    border: 1px solid rgba(196,181,253,0.45);
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #8B5CF6, stop:1 #6D28D9);
}}
QPushButton[kind="primary"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #9D74F8, stop:1 #7C3AED); }}
QPushButton[kind="primary"]:disabled {{ background: rgba(139,92,246,0.25); border: 1px solid rgba(255,255,255,0.06); color: rgba(255,255,255,0.45); }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[kind="ghost"]:hover {{ background: rgba(255,255,255,0.08); }}
QPushButton[kind="danger"] {{ background: rgba(248,113,113,0.14); border: 1px solid rgba(248,113,113,0.38); color: #FCA5A5; }}
QPushButton[kind="danger"]:hover {{ background: rgba(248,113,113,0.24); }}
QPushButton[kind="icon"] {{ background: transparent; border: none; border-radius: 10px; padding: 8px; }}
QPushButton[kind="icon"]:hover {{ background: rgba(255,255,255,0.10); }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-radius: 14px; padding: 12px; }}
QPushButton[kind="nav"]:hover {{ background: rgba(255,255,255,0.08); }}
QPushButton[kind="nav"]:checked {{ background: rgba(139,92,246,0.28); border: 1px solid rgba(167,139,250,0.35); }}
QPushButton[kind="chip"] {{ padding: 5px 12px; border-radius: 14px; font-weight: 500; font-size: 9pt; }}
QPushButton[kind="chip"]:checked {{ background: rgba(139,92,246,0.35); border: 1px solid rgba(167,139,250,0.55); }}

QCheckBox {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.25); background: rgba(255,255,255,0.06); }}
QCheckBox::indicator:hover {{ border-color: rgba(167,139,250,0.8); }}
QCheckBox::indicator:checked {{ background: #8B5CF6; border-color: #A78BFA; image: url({chk}); }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.16); border-radius: 3px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.30); }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QListView, QListWidget {{ background: transparent; border: none; outline: none; }}

QMenu {{ background: #15151F; border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 18px 8px 14px; border-radius: 8px; }}
QMenu::item:selected {{ background: rgba(139,92,246,0.35); }}
QMenu::separator {{ height: 1px; background: rgba(255,255,255,0.08); margin: 5px 8px; }}
QToolTip {{ background: #1B1B28; color: #F3F3F8; border: 1px solid rgba(255,255,255,0.14); padding: 6px 9px; border-radius: 8px; }}
QMessageBox {{ background: #12121C; }}
QFrame[role="divider"] {{ background: rgba(255,255,255,0.08); max-height: 1px; min-height: 1px; border: none; }}
QFrame[role="vdivider"] {{ background: rgba(255,255,255,0.08); max-width: 1px; min-width: 1px; border: none; }}
"""
