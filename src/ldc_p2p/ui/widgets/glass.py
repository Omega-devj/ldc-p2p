"""Briques visuelles Liquid Glass réutilisables."""
from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QWidget

from .. import icons, theme


class AuroraBackground(QWidget):
    """Fond graphite profond avec lueurs violettes diffuses (ce que le verre « réfracte »)."""

    def __init__(self, parent=None, opaque: bool = True):
        super().__init__(parent)
        self.opaque = opaque
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        light = theme.is_light()
        if self.opaque:
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0, QColor("#F3F4FB" if light else "#0B0A14"))
            g.setColorAt(1, QColor("#E3E6F3" if light else "#07070C"))
            p.fillRect(self.rect(), g)
        k = 0.55 if light else 1.0
        for cx, cy, r, col in ((0.12, 0.05, 0.75, theme.accent(0, int(78 * k))),
                               (0.92, 0.95, 0.70, theme.accent(2, int(58 * k))),
                               (0.55, 1.05, 0.55, QColor(34, 211, 238, int(20 * k))),
                               (0.95, 0.10, 0.40, QColor(236, 72, 153, int(22 * k)))):
            rg = QRadialGradient(QPointF(w * cx, h * cy), max(w, h) * r)
            c0, c1 = QColor(col), QColor(col)
            c1.setAlpha(0)
            rg.setColorAt(0, c0)
            rg.setColorAt(1, c1)
            p.fillRect(self.rect(), rg)


class GlassPanel(QFrame):
    """Panneau translucide : remplissage verre, bordure lumineuse dégradée, reflet en haut."""

    def __init__(self, parent=None, radius: int = 20, strong: bool = False, border: bool = True):
        super().__init__(parent)
        self.radius, self.strong, self.border = radius, strong, border
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        fill = QLinearGradient(r.topLeft(), r.bottomLeft())
        a1, a2 = theme.glass_fill_alphas(self.strong)
        base = (255, 255, 255)
        fill.setColorAt(0, QColor(*base, a1))
        fill.setColorAt(1, QColor(*base, a2))
        p.fillPath(path, fill)
        if self.border:
            edge = QLinearGradient(r.topLeft(), r.bottomRight())
            if theme.is_light():
                edge.setColorAt(0, QColor(255, 255, 255, 230))
                edge.setColorAt(0.5, QColor(20, 20, 60, 28))
                edge.setColorAt(1, theme.accent(0, 55))
            else:
                edge.setColorAt(0, QColor(255, 255, 255, 70))
                edge.setColorAt(0.5, QColor(255, 255, 255, 20))
                edge.setColorAt(1, theme.accent(1, 40))
            p.setPen(QPen(QBrush(edge), 1))
            p.drawPath(path)


class PresenceDot(QWidget):
    def __init__(self, status: str = "offline", size: int = 12, ring: QColor | None = None, parent=None):
        super().__init__(parent)
        self.status, self.ring = status, ring or QColor("#0C0C14")
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        p.setPen(Qt.NoPen)
        p.setBrush(self.ring)
        p.drawEllipse(0, 0, s, s)
        p.setBrush(QColor(theme.PRESENCE_COLORS.get(self.status, "#6B7280")))
        m = max(2, s // 6)
        p.drawEllipse(m, m, s - 2 * m, s - 2 * m)


def draw_avatar(p: QPainter, rect: QRectF, key: str, label: str, group: bool = False) -> None:
    c1, c2 = theme.avatar_colors(key)
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0, c1)
    g.setColorAt(1, c2)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    if group:
        p.drawRoundedRect(rect, rect.width() * 0.32, rect.height() * 0.32)
    else:
        p.drawEllipse(rect)
    sh = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    sh.setColorAt(0, QColor(255, 255, 255, 70))
    sh.setColorAt(0.5, QColor(255, 255, 255, 0))
    p.setBrush(sh)
    if group:
        p.drawRoundedRect(rect, rect.width() * 0.32, rect.height() * 0.32)
    else:
        p.drawEllipse(rect)
    p.setPen(QColor("white"))
    f = QFont(p.font())
    f.setPixelSize(int(rect.height() * 0.42))
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    initials = "".join(w[0] for w in label.split()[:2]).upper() or "?"
    p.drawText(rect, Qt.AlignCenter, initials[:2])
    p.restore()


class Avatar(QWidget):
    def __init__(self, key: str = "", label: str = "?", size: int = 40, status: str | None = None, group: bool = False, parent=None):
        super().__init__(parent)
        self.key, self.label, self.group, self._size = key, label, group, size
        self.setFixedSize(size, size)
        self.dot: PresenceDot | None = None
        if status is not None:
            self.dot = PresenceDot(status, max(11, size // 3), parent=self)
            self._place_dot()

    def _place_dot(self) -> None:
        if self.dot:
            d = self.dot.width()
            self.dot.move(self.width() - d + 1, self.height() - d + 1)

    def set(self, key: str, label: str, status: str | None = None, group: bool = False) -> None:
        self.key, self.label, self.group = key, label, group
        if self.dot is not None and status is not None:
            self.dot.set_status(status)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        draw_avatar(p, QRectF(0, 0, self.width(), self.height()), self.key, self.label, self.group)


class Switch(QAbstractButton):
    """Interrupteur animé (remplace les cases à cocher pour les réglages on/off)."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos_", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_pos(self) -> float:
        return self._pos

    def set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    pos_ = Property(float, get_pos, set_pos)

    def sizeHint(self) -> QSize:
        return QSize(46, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off, on = theme.ov(60 if theme.is_light() else 36), theme.accent(0)
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t), int(off.alpha() + (on.alpha() - off.alpha()) * t))
        p.setPen(QPen(theme.ov(40), 1))
        p.setBrush(col if self.isEnabled() else theme.ov(18))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * t
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white") if self.isEnabled() else QColor(255, 255, 255, 120))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Toast(GlassPanel):
    """Message éphémère non bloquant en bas de la fenêtre."""

    def __init__(self, parent: QWidget):
        super().__init__(parent, radius=16, strong=True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 18, 10)
        lay.setSpacing(10)
        self.ico = QLabel()
        self.ico.setFixedSize(20, 20)
        self.lbl = QLabel()
        lay.addWidget(self.ico)
        lay.addWidget(self.lbl)
        self.hide()
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, kind: str = "info", ms: int = 3500) -> None:
        color = {"info": theme.accent_hex(1), "ok": "#34D399", "error": "#F87171"}.get(kind, theme.accent_hex(1))
        name = {"info": "info", "ok": "check", "error": "alert"}.get(kind, "info")
        self.ico.setPixmap(icons.pixmap(name, color, 20))
        self.lbl.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._timer.start(ms)


def divider(vertical: bool = False) -> QFrame:
    f = QFrame()
    f.setProperty("role", "vdivider" if vertical else "divider")
    return f


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    l = QLabel(text)
    if role:
        l.setProperty("role", role)
    l.setWordWrap(wrap)
    return l
