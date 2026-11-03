"""Zone de rédaction : Entrée envoie, Maj+Entrée saute une ligne, réponse citée, emojis, pièce jointe."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
                               QWidget)

from .. import icons, theme
from .glass import GlassPanel

EMOJIS = ("😀 😂 🥹 😊 😍 😘 😎 🤔 😅 😭 😡 🥳 🙏 👍 👎 👏 🙌 💪 🔥 ✨ 🎉 ❤️ 💜 💔 💯 ✅ ❌ ⚡ 🌙 ☀️ 🍕 ☕ 🎮 🎧 🚀 👀 🤝 😴 🤯 🫡").split()


class EmojiPopup(QFrame):
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet("EmojiPopup { background: #15151F; border: 1px solid rgba(255,255,255,0.14); border-radius: 14px; }")
        g = QGridLayout(self)
        g.setContentsMargins(10, 10, 10, 10)
        g.setSpacing(2)
        for i, e in enumerate(EMOJIS):
            b = QPushButton(e)
            b.setProperty("kind", "icon")
            b.setFixedSize(38, 38)
            b.setStyleSheet("font-size: 16pt; padding: 0;")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, em=e: (self.picked.emit(em), self.close()))
            g.addWidget(b, i // 8, i % 8)


class InputEdit(QPlainTextEdit):
    send = Signal()
    typing = Signal()
    escape = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Écrire un message chiffré…")
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QPlainTextEdit { background: transparent; border: none; padding: 8px 4px; }")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().contentsChanged.connect(self._resize)
        self._resize()

    def _resize(self) -> None:
        fm = QFontMetrics(self.font())
        h = int(self.document().size().height() * 1.0) + 18
        self.setFixedHeight(max(40, min(h, fm.lineSpacing() * 6 + 20)))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        if e.key() == Qt.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(e)
        if e.text():
            self.typing.emit()

    def insertFromMimeData(self, source) -> None:
        self.insertPlainText(source.text())  # jamais de texte riche collé


class Composer(QWidget):
    send_text = Signal(str)
    attach = Signal()
    typing = Signal()
    cancel_reply = Signal()
    cancel_edit = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        # bandeau réponse / modification
        self.banner = GlassPanel(radius=12)
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.banner_icon = QLabel()
        self.banner_text = QLabel()
        self.banner_text.setProperty("role", "muted")
        close = QPushButton()
        close.setProperty("kind", "icon")
        close.setIcon(icons.icon("x", "#B9B9C8", 16))
        close.clicked.connect(self._cancel_banner)
        bl.addWidget(self.banner_icon)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(close)
        self.banner.hide()
        outer.addWidget(self.banner)
        # barre de saisie
        self.bar = GlassPanel(radius=22, strong=True)
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(10, 6, 8, 6)
        h.setSpacing(2)
        self.btn_attach = self._icon_btn("paperclip", "Joindre un fichier")
        self.btn_emoji = self._icon_btn("smile", "Emojis")
        self.edit = InputEdit()
        self.btn_send = QPushButton()
        self.btn_send.setProperty("kind", "primary")
        self.btn_send.setIcon(icons.icon("send", "#FFFFFF", 18))
        self.btn_send.setFixedSize(42, 40)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.setToolTip("Envoyer (Entrée)")
        h.addWidget(self.btn_attach, 0, Qt.AlignBottom)
        h.addWidget(self.btn_emoji, 0, Qt.AlignBottom)
        h.addWidget(self.edit, 1)
        h.addWidget(self.btn_send, 0, Qt.AlignBottom)
        outer.addWidget(self.bar)
        self.mode = None  # None | "reply" | "edit"
        self.target: str | None = None
        self.popup = EmojiPopup(self)
        self.popup.picked.connect(lambda e: (self.edit.insertPlainText(e), self.edit.setFocus()))
        self.btn_emoji.clicked.connect(self._show_emojis)
        self.btn_attach.clicked.connect(self.attach)
        self.btn_send.clicked.connect(self._send)
        self.edit.send.connect(self._send)
        self.edit.typing.connect(self.typing)
        self.edit.escape.connect(self._cancel_banner)

    def _icon_btn(self, name: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setProperty("kind", "icon")
        b.setIcon(icons.icon(name, "#B9B9C8", 20))
        b.setIconSize(QSize(20, 20))
        b.setFixedSize(38, 38)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _show_emojis(self) -> None:
        self.popup.adjustSize()
        pos = self.btn_emoji.mapToGlobal(QPoint(0, -self.popup.sizeHint().height() - 8))
        self.popup.move(pos)
        self.popup.show()

    def _send(self) -> None:
        text = self.edit.toPlainText().strip()
        if text:
            self.send_text.emit(text)

    def clear(self) -> None:
        self.edit.clear()

    def set_enabled_state(self, enabled: bool, placeholder: str = "") -> None:
        for w in (self.edit, self.btn_send, self.btn_attach, self.btn_emoji):
            w.setEnabled(enabled)
        self.edit.setPlaceholderText(placeholder or "Écrire un message chiffré…")

    def start_reply(self, mid: str, who: str, text: str) -> None:
        self.mode, self.target = "reply", mid
        self.banner_icon.setPixmap(icons.pixmap("reply", "#A78BFA", 18))
        fm = QFontMetrics(self.font())
        self.banner_text.setText(f"Répondre à {who} : " + fm.elidedText(text.replace("\n", " "), Qt.ElideRight, 380))
        self.banner.show()
        self.edit.setFocus()

    def start_edit(self, mid: str, text: str) -> None:
        self.mode, self.target = "edit", mid
        self.banner_icon.setPixmap(icons.pixmap("edit", "#A78BFA", 18))
        self.banner_text.setText("Modification du message")
        self.edit.setPlainText(text)
        self.banner.show()
        self.edit.setFocus()

    def _cancel_banner(self) -> None:
        was = self.mode
        self.mode, self.target = None, None
        self.banner.hide()
        if was == "edit":
            self.edit.clear()
            self.cancel_edit.emit()
        elif was == "reply":
            self.cancel_reply.emit()

    def reset_mode(self) -> None:
        self.mode, self.target = None, None
        self.banner.hide()
