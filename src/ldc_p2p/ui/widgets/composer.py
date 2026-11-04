"""Zone de rédaction : Entrée envoie, Maj+Entrée saute une ligne, réponse citée, emojis, pièce jointe."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QImage, QKeyEvent
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from .. import icons, theme
from .glass import GlassPanel

EMOJIS = ("😀 😂 🥹 😊 😍 😘 😎 🤔 😅 😭 😡 🥳 🙏 👍 👎 👏 🙌 💪 🔥 ✨ 🎉 ❤️ 💜 💔 💯 ✅ ❌ ⚡ 🌙 ☀️ 🍕 ☕ 🎮 🎧 🚀 👀 🤝 😴 🤯 🫡").split()


class EmojiPopup(QFrame):
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet("EmojiPopup { background: %s; border: 1px solid rgba(128,128,150,0.35); border-radius: 14px; }" % ("#FFFFFF" if theme.is_light() else "#15151F"))
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
    image_pasted = Signal(object)
    files_dropped = Signal(list)
    mention_key = Signal(int)      # Haut/Bas/Tab/Entrée pendant l'autocomplétion
    mention_active = False

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
        if self.mention_active and e.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
            self.mention_key.emit(e.key())
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        if e.key() == Qt.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(e)
        if e.text():
            self.typing.emit()

    def canInsertFromMimeData(self, source) -> bool:
        return source.hasText() or source.hasImage() or source.hasUrls()

    def insertFromMimeData(self, source) -> None:
        if source.hasUrls() and any(u.isLocalFile() for u in source.urls()):
            self.files_dropped.emit([u.toLocalFile() for u in source.urls() if u.isLocalFile()])
        elif source.hasImage() and not source.hasText():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.image_pasted.emit(img)
        else:
            self.insertPlainText(source.text())  # jamais de texte riche collé


class Composer(QWidget):
    send_text = Signal(str)
    attach = Signal()
    typing = Signal()
    cancel_reply = Signal()
    cancel_edit = Signal()
    image_pasted = Signal(object)
    files_dropped = Signal(list)

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
        close.setIcon(icons.icon("x", theme.icon_hex(1), 16))
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
        self.members: list[tuple[str, str]] = []
        self.mention_list = QListWidget(self)
        self.mention_list.setFixedHeight(0)
        self.mention_list.hide()
        self.mention_list.setStyleSheet("QListWidget { background: %s; border: 1px solid rgba(128,128,150,0.4); border-radius: 12px; padding: 4px; }"
                                        "QListWidget::item { padding: 7px 10px; border-radius: 8px; }"
                                        "QListWidget::item:selected { background: %s; }" % ("#FFFFFF" if theme.is_light() else "#15151F", theme.accent_hex(0)))
        self.mention_list.itemClicked.connect(lambda it: self._insert_mention(it))
        self.edit.mention_key.connect(self._mention_key)
        self.edit.textChanged.connect(self._mention_scan)
        self.edit.image_pasted.connect(self.image_pasted)
        self.edit.files_dropped.connect(self.files_dropped)
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
        b.setIcon(icons.icon(name, theme.icon_hex(1), 20))
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

    # -- @mentions (groupes) ---------------------------------------------------------------------------
    def set_members(self, members: list[tuple[str, str]]) -> None:
        self.members = members
        self._hide_mentions()

    def mentions_in(self, text: str) -> list[str]:
        low = text.casefold()
        return [i for i, n in self.members if ("@" + n).casefold() in low]

    def _mention_scan(self) -> None:
        if not self.members:
            return
        cur = self.edit.textCursor()
        before = self.edit.toPlainText()[:cur.position()]
        import re
        m = re.search(r"(?:^|\s)@([^\s@]{0,20})$", before)
        if not m:
            return self._hide_mentions()
        q = m.group(1).casefold()
        found = [(i, n) for i, n in self.members if n.casefold().startswith(q)][:6]
        if not found:
            return self._hide_mentions()
        self.mention_list.clear()
        for i, n in found:
            it = QListWidgetItem("@" + n)
            it.setData(Qt.UserRole, (i, n, len(m.group(1)) + 1))
            self.mention_list.addItem(it)
        self.mention_list.setCurrentRow(0)
        h = min(6, len(found)) * 34 + 12
        self.mention_list.setFixedSize(240, h)
        self.mention_list.move(52, -h - 4)
        self.mention_list.show()
        self.mention_list.raise_()
        self.edit.mention_active = True

    def _hide_mentions(self) -> None:
        self.mention_list.hide()
        self.edit.mention_active = False

    def _mention_key(self, key: int) -> None:
        r, n = self.mention_list.currentRow(), self.mention_list.count()
        if key == Qt.Key_Up:
            self.mention_list.setCurrentRow(max(0, r - 1))
        elif key == Qt.Key_Down:
            self.mention_list.setCurrentRow(min(n - 1, r + 1))
        else:
            self._insert_mention(self.mention_list.currentItem())

    def _insert_mention(self, item) -> None:
        if item is None:
            return
        _, name, typed = item.data(Qt.UserRole)
        cur = self.edit.textCursor()
        for _ in range(typed):
            cur.deletePreviousChar()
        cur.insertText("@" + name + " ")
        self.edit.setTextCursor(cur)
        self._hide_mentions()
        self.edit.setFocus()

    def clear(self) -> None:
        self.edit.clear()

    def set_enabled_state(self, enabled: bool, placeholder: str = "") -> None:
        for w in (self.edit, self.btn_send, self.btn_attach, self.btn_emoji):
            w.setEnabled(enabled)
        self.edit.setPlaceholderText(placeholder or "Écrire un message chiffré…")

    def start_reply(self, mid: str, who: str, text: str) -> None:
        self.mode, self.target = "reply", mid
        self.banner_icon.setPixmap(icons.pixmap("reply", theme.accent_hex(1), 18))
        fm = QFontMetrics(self.font())
        self.banner_text.setText(f"Répondre à {who} : " + fm.elidedText(text.replace("\n", " "), Qt.ElideRight, 380))
        self.banner.show()
        self.edit.setFocus()

    def start_edit(self, mid: str, text: str) -> None:
        self.mode, self.target = "edit", mid
        self.banner_icon.setPixmap(icons.pixmap("edit", theme.accent_hex(1), 18))
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
