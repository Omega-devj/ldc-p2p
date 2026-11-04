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

# --- palette dynamique (thème sombre/clair + couleur d'accent) --------------------------------
ACCENTS = {   # principal, clair, profond, pâle
    "violet": ("#8B5CF6", "#A78BFA", "#6D28D9", "#C4B5FD"),
    "bleu": ("#3B82F6", "#60A5FA", "#1D4ED8", "#BFDBFE"),
    "cyan": ("#06B6D4", "#22D3EE", "#0E7490", "#A5F3FC"),
    "vert": ("#10B981", "#34D399", "#047857", "#A7F3D0"),
    "rose": ("#EC4899", "#F472B6", "#BE185D", "#FBCFE8"),
    "orange": ("#F97316", "#FB923C", "#C2410C", "#FED7AA"),
}
THEMES = ("sombre", "clair")
_state = {"mode": "sombre", "accent": "violet"}


def configure(mode: str = "sombre", accent: str = "violet") -> None:
    _state["mode"] = mode if mode in THEMES else "sombre"
    _state["accent"] = accent if accent in ACCENTS else "violet"


def current() -> tuple[str, str]:
    return _state["mode"], _state["accent"]


def is_light() -> bool:
    return _state["mode"] == "clair"


def accent(i: int = 0, alpha: int = 255) -> QColor:
    c = QColor(ACCENTS[_state["accent"]][i])
    c.setAlpha(alpha)
    return c


def accent_hex(i: int = 0) -> str:
    return ACCENTS[_state["accent"]][i]


def ov(alpha: int) -> QColor:
    """Voile pour surfaces/traits : blanc translucide en sombre, noir translucide en clair."""
    return QColor(0, 0, 0, int(alpha * 0.55)) if is_light() else QColor(255, 255, 255, alpha)


def on_accent(alpha: int = 255) -> QColor:
    """Blanc, pour ce qui est dessiné sur une surface d'accent (identique dans les deux thèmes)."""
    return QColor(255, 255, 255, alpha)


def ink(alpha: int = 255) -> QColor:
    c = QColor("#14141F") if is_light() else QColor("#F3F3F8")
    c.setAlpha(alpha)
    return c


def ink_hex() -> str:
    return "#14141F" if is_light() else "#F3F3F8"


def icon_hex(level: int = 1) -> str:
    """Gris d'icônes : 0 discret, 1 normal, 2 appuyé."""
    return {0: "#8E8EA0", 1: "#C9C9D6", 2: "#E9E3FF"}[level] if not is_light() else {0: "#7A7A90", 1: "#4A4A5E", 2: "#1B1B2A"}[level]


def glass_fill_alphas(strong: bool) -> tuple[int, int]:
    return ((30, 14) if strong else (20, 9)) if not is_light() else ((235, 190) if strong else (190, 150))


def __getattr__(name: str):  # TEXT, TEXT_2, TEXT_3, ACCENT… évalués au moment de l'usage
    if name == "TEXT":
        return ink()
    if name == "TEXT_2":
        return QColor(20, 20, 40, 160) if is_light() else QColor(235, 235, 245, 158)
    if name == "TEXT_3":
        return QColor(20, 20, 40, 110) if is_light() else QColor(235, 235, 245, 96)
    if name == "ACCENT":
        return accent(0)
    if name == "ACCENT_2":
        return accent(1)
    raise AttributeError(name)


SUCCESS = QColor("#34D399")
WARN = QColor("#FBBF24")
DANGER = QColor("#F87171")

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


def _rgb(h: str) -> str:
    c = QColor(h)
    return f"{c.red()},{c.green()},{c.blue()}"


def build_qss() -> str:
    chk = _svg_png("check", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 12.5 10 17.5 19 7"/></svg>')
    down = _svg_png("down", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>' % ("#555566" if is_light() else "#B9B9C8"))
    light = is_light()
    q = _QSS
    for k, v in {
        "@FONT@": FONT_FAMILY, "@DOWN@": down, "@CHK@": chk,
        "@OV@": "0,0,0" if light else "255,255,255",
        "@INK@": ink_hex(), "@INK2@": "rgba(20,20,40,0.62)" if light else "rgba(235,235,245,0.62)",
        "@INK3@": "rgba(20,20,40,0.45)" if light else "rgba(235,235,245,0.42)",
        "@AR@": _rgb(accent_hex(0)), "@AR2@": _rgb(accent_hex(1)), "@AR3@": _rgb(accent_hex(3)),
        "@A@": accent_hex(0), "@A2@": accent_hex(1), "@AD@": accent_hex(2),
        "@MENU@": "#FFFFFF" if light else "#15151F", "@DANGER@": "#B91C1C" if light else "#FCA5A5",
    }.items():
        q = q.replace(k, v)
    return q


_QSS = """
* { outline: none; }
QWidget { color: @INK@; font-family: @FONT@; font-size: 10pt; }
QMainWindow, QDialog { background: transparent; }
QLabel { background: transparent; }
QLabel[role="title"] { font-size: 22pt; font-weight: 700; letter-spacing: -0.5px; }
QLabel[role="h2"] { font-size: 14pt; font-weight: 650; }
QLabel[role="h3"] { font-size: 11pt; font-weight: 600; }
QLabel[role="muted"] { color: @INK2@; }
QLabel[role="faint"] { color: @INK3@; font-size: 9pt; }
QLabel[role="section"] { color: @INK3@; font-size: 8.5pt; font-weight: 700; letter-spacing: 1.2px; }
QLabel[role="mono"] { font-family: "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 10pt; }
QLabel[role="error"] { color: #F87171; }
QLabel[role="ok"] { color: #34D399; }

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {
    background: rgba(@OV@,0.06); border: 1px solid rgba(@OV@,0.10);
    border-radius: 12px; padding: 9px 12px; selection-background-color: rgba(@AR@,0.55);
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { background: rgba(@OV@,0.085); }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(@AR2@,0.85); background: rgba(@OV@,0.09);
}
QLineEdit[invalid="true"] { border: 1px solid rgba(248,113,113,0.85); }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: url(@DOWN@); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: @MENU@; border: 1px solid rgba(@OV@,0.12); border-radius: 10px;
    padding: 4px; selection-background-color: rgba(@AR@,0.45); outline: none; }

QPushButton {
    background: rgba(@OV@,0.08); border: 1px solid rgba(@OV@,0.10);
    border-radius: 12px; padding: 9px 18px; font-weight: 600;
}
QPushButton:hover { background: rgba(@OV@,0.13); }
QPushButton:pressed { background: rgba(@OV@,0.06); }
QPushButton:disabled { color: @INK3@; background: rgba(@OV@,0.04); }
QPushButton[kind="primary"] {
    border: 1px solid rgba(@AR3@,0.45);
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 @A@, stop:1 @AD@);
}
QPushButton[kind="primary"]:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 @A2@, stop:1 @AD@); }
QPushButton[kind="primary"]:disabled { background: rgba(@AR@,0.25); border: 1px solid rgba(@OV@,0.06); color: rgba(@OV@,0.45); }
QPushButton[kind="ghost"] { background: transparent; border: 1px solid transparent; }
QPushButton[kind="ghost"]:hover { background: rgba(@OV@,0.08); }
QPushButton[kind="danger"] { background: rgba(248,113,113,0.14); border: 1px solid rgba(248,113,113,0.38); color: @DANGER@; }
QPushButton[kind="danger"]:hover { background: rgba(248,113,113,0.24); }
QPushButton[kind="icon"] { background: transparent; border: none; border-radius: 10px; padding: 8px; }
QPushButton[kind="icon"]:hover { background: rgba(@OV@,0.10); }
QPushButton[kind="nav"] { background: transparent; border: none; border-radius: 14px; padding: 12px; }
QPushButton[kind="nav"]:hover { background: rgba(@OV@,0.08); }
QPushButton[kind="nav"]:checked { background: rgba(@AR@,0.28); border: 1px solid rgba(@AR2@,0.35); }
QPushButton[kind="chip"] { padding: 5px 12px; border-radius: 14px; font-weight: 500; font-size: 9pt; }
QPushButton[kind="chip"]:checked { background: rgba(@AR@,0.35); border: 1px solid rgba(@AR2@,0.55); }

QCheckBox { spacing: 10px; background: transparent; }
QCheckBox::indicator { width: 20px; height: 20px; border-radius: 6px; border: 1px solid rgba(@OV@,0.25); background: rgba(@OV@,0.06); }
QCheckBox::indicator:hover { border-color: rgba(@AR2@,0.8); }
QCheckBox::indicator:checked { background: @A@; border-color: #A78BFA; image: url(@CHK@); }

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(@OV@,0.16); border-radius: 3px; min-height: 36px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: rgba(@OV@,0.30); }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { height: 0; background: transparent; }
QScrollBar:horizontal { height: 0; }
QListView, QListWidget { background: transparent; border: none; outline: none; }

QMenu { background: @MENU@; border: 1px solid rgba(@OV@,0.12); border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 18px 8px 14px; border-radius: 8px; }
QMenu::item:selected { background: rgba(@AR@,0.35); }
QMenu::separator { height: 1px; background: rgba(@OV@,0.08); margin: 5px 8px; }
QToolTip { background: @MENU@; color: @INK@; border: 1px solid rgba(@OV@,0.14); padding: 6px 9px; border-radius: 8px; }
QMessageBox { background: @MENU@; }
QFrame[role="divider"] { background: rgba(@OV@,0.08); max-height: 1px; min-height: 1px; border: none; }
QFrame[role="vdivider"] { background: rgba(@OV@,0.08); max-width: 1px; min-width: 1px; border: none; }
"""
