"""Fenêtre d'appel (vocal/vidéo) et pop-up d'appel entrant."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import icons, sounds, theme
from .widgets.glass import AuroraBackground, Avatar, GlassPanel, label

QUALITY = {"good": ("Connexion excellente", "#34D399"), "medium": ("Connexion moyenne", "#FBBF24"), "poor": ("Connexion faible", "#F87171")}


def array_to_qimage(arr) -> QImage | None:
    try:
        h, w, _ = arr.shape
        return QImage(arr.tobytes(), w, h, 3 * w, QImage.Format_RGB888).copy()
    except Exception:
        return None


class VideoView(QWidget):
    """Image distante plein cadre (ratio conservé, coins arrondis) + aperçu local en incrustation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remote: QImage | None = None
        self.local: QImage | None = None
        self.name, self.key = "", ""
        self.placeholder = ""
        self._last = 0.0
        self.setMinimumSize(520, 340)

    def set_remote(self, arr) -> None:
        now = time.monotonic()
        if now - self._last < 0.033:
            return
        self._last = now
        img = array_to_qimage(arr)
        if img is not None:
            self.remote = img
            self.update()

    def set_local(self, arr) -> None:
        img = array_to_qimage(arr)
        if img is not None:
            self.local = img.mirrored(True, False)   # aperçu « miroir », comme un vrai miroir
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 22, 22)
        p.setClipPath(clip)
        p.fillRect(r, QColor("#0A0A12") if not theme.is_light() else QColor("#1B1B2A"))
        if self.remote is not None:
            sz = self.remote.size().scaled(self.size(), Qt.KeepAspectRatio)
            p.drawImage(QRectF((r.width() - sz.width()) / 2, (r.height() - sz.height()) / 2, sz.width(), sz.height()), self.remote)
        else:
            from .widgets.glass import draw_avatar
            d = min(160, r.height() * 0.4)
            draw_avatar(p, QRectF(r.center().x() - d / 2, r.center().y() - d / 2 - 30, d, d), self.key, self.name)
            p.setPen(QColor("white"))
            f = p.font()
            f.setPointSizeF(15)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(0, r.center().y() + d / 2 - 10, r.width(), 36), Qt.AlignCenter, self.name)
            f.setPointSizeF(10.5)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QRectF(0, r.center().y() + d / 2 + 24, r.width(), 28), Qt.AlignCenter, self.placeholder)
        if self.local is not None:
            w = max(150, int(r.width() * 0.22))
            h = int(w * self.local.height() / max(1, self.local.width()))
            pip = QRectF(r.right() - w - 16, r.bottom() - h - 16, w, h)
            pc = QPainterPath()
            pc.addRoundedRect(pip, 12, 12)
            p.save()
            p.setClipPath(pc)
            p.drawImage(pip, self.local)
            p.restore()
            p.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(pip, 12, 12)


class RoundButton(QPushButton):
    def __init__(self, icon: str, tip: str, danger: bool = False, size: int = 56):
        super().__init__()
        self.icon_name, self.danger = icon, danger
        self.setFixedSize(size, size)
        self.setIconSize(QSize(size // 2 - 2, size // 2 - 2))
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.set_icon(icon)
        r = size // 2
        bg = "#EF4444" if danger else "rgba(255,255,255,0.14)"
        hv = "#DC2626" if danger else "rgba(255,255,255,0.24)"
        self.setStyleSheet(f"QPushButton {{ background: {bg}; border: 1px solid rgba(255,255,255,0.18); border-radius: {r}px; padding: 0; }}"
                           f"QPushButton:hover {{ background: {hv}; }}")

    def set_icon(self, name: str) -> None:
        self.icon_name = name
        self.setIcon(icons.icon(name, "#FFFFFF", 26, 1.9))


class CallWindow(AuroraBackground):
    """Fenêtre d'appel : vidéo, minuteur, qualité, micro/caméra/raccrocher."""
    hangup_clicked = Signal()
    mute_toggled = Signal(bool)
    camera_toggled = Signal(bool)

    def __init__(self, name: str, peer_id: str, video: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Appel — {name}")
        self.resize(900 if video else 560, 640 if video else 560)
        self.setMinimumSize(520, 460)
        self.video_call = video
        self.started: float | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        top = QHBoxLayout()
        self.lock = QLabel()
        self.lock.setPixmap(icons.pixmap("lock", "#34D399", 16))
        self.lock_txt = label("Chiffré de bout en bout", "faint")
        self.quality = label("", "faint")
        top.addWidget(self.lock)
        top.addWidget(self.lock_txt)
        top.addStretch(1)
        top.addWidget(self.quality)
        lay.addLayout(top)
        self.view = VideoView()
        self.view.name, self.view.key = name, peer_id
        lay.addWidget(self.view, 1)
        self.status = label("Appel en cours…", "h3")
        self.status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status)
        self.hint = label("Astuce : utilisez un casque pour éviter l'écho.", "faint")
        self.hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.hint)
        bar = QHBoxLayout()
        bar.setSpacing(16)
        bar.addStretch(1)
        self.btn_mute = RoundButton("mic", "Couper le micro")
        self.btn_cam = RoundButton("video", "Couper la caméra")
        self.btn_end = RoundButton("phone-off", "Raccrocher", danger=True, size=64)
        self.btn_cam.setVisible(video)
        for b in (self.btn_mute, self.btn_cam, self.btn_end):
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        self._muted = self._cam_on = False
        self._cam_on = True
        self.btn_mute.clicked.connect(self._toggle_mute)
        self.btn_cam.clicked.connect(self._toggle_cam)
        self.btn_end.clicked.connect(self.hangup_clicked)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self.set_state("ringing", "")

    def closeEvent(self, e) -> None:
        self._timer.stop()
        self.hangup_clicked.emit()   # fermer la fenêtre = raccrocher (sans effet si l'appel est déjà terminé)
        super().closeEvent(e)

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        self.btn_mute.set_icon("mic-off" if self._muted else "mic")
        self.mute_toggled.emit(self._muted)

    def _toggle_cam(self) -> None:
        self._cam_on = not self._cam_on
        self.btn_cam.set_icon("video" if self._cam_on else "video-off")
        self.camera_toggled.emit(self._cam_on)

    def set_state(self, state: str, extra: str) -> None:
        txt = {"ringing": "Sonnerie…", "connecting": "Connexion…", "active": "", "ended": extra or "Appel terminé"}[state]
        self.view.placeholder = txt or "En ligne"
        self.status.setText(txt)
        if state == "active" and self.started is None:
            self.started = time.time()
        if state == "ended":
            self._timer.stop()
            self.btn_end.setEnabled(False)
        self.view.update()

    def set_quality(self, q: str) -> None:
        txt, col = QUALITY.get(q, ("", "#8E8EA0"))
        self.quality.setText("● " + txt if txt else "")
        self.quality.setStyleSheet(f"color: {col};")

    def _tick(self) -> None:
        if self.started:
            d = int(time.time() - self.started)
            self.status.setText(f"{d // 60:02d}:{d % 60:02d}")


class IncomingCallPopup(GlassPanel):
    """Pop-up d'appel entrant, toujours visible (même si la fenêtre principale est réduite)."""
    accepted = Signal(bool)     # True = avec vidéo
    declined = Signal()

    def __init__(self, name: str, peer_id: str, video: bool):
        super().__init__(None, radius=24, strong=True)
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(360, 190 if video else 170)
        l = QVBoxLayout(self)
        l.setContentsMargins(22, 18, 22, 18)
        head = QHBoxLayout()
        head.addWidget(Avatar(peer_id, name, 52))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(name, "h3"))
        col.addWidget(label("Appel vidéo entrant" if video else "Appel vocal entrant", "muted"))
        head.addLayout(col, 1)
        l.addLayout(head)
        l.addSpacing(8)
        row = QHBoxLayout()
        no = QPushButton("  Refuser")
        no.setProperty("kind", "danger")
        no.setIcon(icons.icon("phone-off", "#FCA5A5", 18))
        yes = QPushButton("  Décrocher")
        yes.setProperty("kind", "primary")
        yes.setIcon(icons.icon("phone", "#FFFFFF", 18))
        no.clicked.connect(self.declined)
        yes.clicked.connect(lambda: self.accepted.emit(False))
        row.addWidget(no)
        row.addWidget(yes)
        l.addLayout(row)
        if video:
            vb = QPushButton("  Décrocher avec la caméra")
            vb.setProperty("kind", "primary")
            vb.setIcon(icons.icon("video", "#FFFFFF", 18))
            vb.clicked.connect(lambda: self.accepted.emit(True))
            l.addWidget(vb)
            yes.setText("  Audio seul")
        self._ring = QTimer(self)
        self._ring.timeout.connect(lambda: sounds.play("sonnerie"))
        self._ring.start(2200)
        sounds.play("sonnerie")

    def stop_ring(self) -> None:
        self._ring.stop()
