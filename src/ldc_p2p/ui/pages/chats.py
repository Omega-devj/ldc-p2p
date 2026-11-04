"""Page « Messages » : liste des conversations, discussion et panneau de détails."""
from __future__ import annotations

import os
from pathlib import Path

import tempfile
from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ...crypto.e2e import now_ms
from ...engine.files import DANGEROUS_EXT
from .. import icons, theme
from ..controller import UiController
from ..dialogs import (ConfirmDialog, MessageInfoDialog, PickMembersDialog, PreviewSendDialog, SafetyDialog, TextPrompt, button, copy_text)
from ...engine.core import ALLOWED_REACTIONS
from ..widgets.chat_view import ChatView, build_items
from ..widgets.composer import Composer
from ..widgets.conv_list import ConvList, preview_of
from ..widgets.glass import Avatar, GlassPanel, Switch, divider, label

TTL_CHOICES = [("Désactivés", 0), ("5 minutes", 300), ("1 heure", 3600), ("24 heures", 86400), ("7 jours", 604800)]


class ChatsPage(QWidget):
    def __init__(self, ctrl: UiController, parent=None):
        super().__init__(parent)
        self.ctrl, self.engine = ctrl, ctrl.engine
        self.conv: str | None = None
        self.focus_mid: str | None = None
        self.filter_unread = False
        self.typing_in: dict[str, QTimer] = {}
        self.details_open = False
        self._build()
        c = ctrl
        c.conv_changed.connect(self._on_conv_changed)
        c.message.connect(self._on_message)
        c.message_status.connect(self._on_status)
        c.message_updated.connect(self._on_updated)
        c.message_deleted.connect(self._on_deleted)
        c.contacts_changed.connect(self._on_contacts_changed)
        c.presence_changed.connect(self._on_presence)
        c.typing.connect(self._on_typing)
        c.file_state.connect(self._on_file_state)
        c.file_progress.connect(self._on_file_progress)
        c.connection.connect(lambda *_: self._update_banner())
        self.setAcceptDrops(True)
        self.unseen_below = 0
        self.scroll_btn = QPushButton(self.view.viewport())
        self.scroll_btn.setProperty("kind", "primary")
        self.scroll_btn.setIcon(icons.icon("arrow-up", "#FFFFFF", 18))
        self.scroll_btn.setFixedSize(46, 46)
        self.scroll_btn.setStyleSheet("QPushButton { border-radius: 23px; padding: 0; }")
        self.scroll_btn.setIconSize(QSize(18, 18))
        self.scroll_btn.setToolTip("Aller aux derniers messages")
        self.scroll_btn.clicked.connect(self._jump_bottom)
        self.scroll_btn.hide()
        self.view.scrolled.connect(self._update_scroll_btn)
        self.view.viewport().installEventFilter(self)
        self.refresh_list()
        self._show_conv()

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.view.viewport() and ev.type() == QEvent.Resize:
            self.scroll_btn.move(ev.size().width() - 66, ev.size().height() - 66)
        return False

    def _update_scroll_btn(self) -> None:
        far = not self.view.at_bottom()
        if not far:
            self.unseen_below = 0
        self.scroll_btn.setIcon(icons.icon("arrow-up", "#FFFFFF", 18) if True else QIcon())
        self.scroll_btn.setText(str(self.unseen_below) if self.unseen_below else "")
        self.scroll_btn.setVisible(far)
        self.scroll_btn.move(self.view.viewport().width() - 66, self.view.viewport().height() - 66)

    def _jump_bottom(self) -> None:
        self.view.scroll_bottom()
        self.unseen_below = 0
        QTimer.singleShot(80, self._update_scroll_btn)

    # -- glisser-déposer et images collées -----------------------------------------------------------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() and self.conv and not self._is_group():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self._offer_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _offer_files(self, paths: list[str]) -> None:
        import os
        paths = [p for p in paths if os.path.isfile(p)][:5]
        if not paths or not self.conv:
            return
        if self._is_group():
            self.ctrl.error.emit("Le partage de fichiers est disponible dans les conversations privées.")
            return
        lines = [f"{os.path.basename(p)} — {os.path.getsize(p) / 1048576:.1f} Mo" for p in paths]
        if PreviewSendDialog(self, f"Envoyer {len(paths)} fichier(s) à {self.h_title.text()} ?", lines).exec():
            for p in paths:
                self.ctrl.run(self.engine.send_file(self.conv, p))

    def _on_image_pasted(self, img) -> None:
        if not self.conv or self._is_group():
            self.ctrl.error.emit("Collez une image dans une conversation privée pour l'envoyer.")
            return
        from PySide6.QtGui import QPixmap
        path = Path(tempfile.mkdtemp(prefix="ldc-img-")) / f"image-{now_ms()}.png"
        img.save(str(path), "PNG")
        if PreviewSendDialog(self, f"Envoyer cette image à {self.h_title.text()} ?", [path.name], QPixmap.fromImage(img)).exec():
            self.ctrl.run(self.engine.send_file(self.conv, str(path)))

    # ------------------------------------------------------------------ construction
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        # colonne gauche
        self.left = GlassPanel(radius=24)
        self.left.setFixedWidth(344)
        ll = QVBoxLayout(self.left)
        ll.setContentsMargins(14, 18, 8, 12)
        ll.setSpacing(10)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 6, 0)
        head.addWidget(label("Messages", "h2"), 1)
        self.btn_group = self._tool("users", "Nouveau groupe")
        self.btn_add = self._tool("user-plus", "Ajouter un contact")
        head.addWidget(self.btn_group)
        head.addWidget(self.btn_add)
        ll.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher messages et contacts")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", theme.icon_hex(0), 18), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda: self.refresh_list())
        sw = QHBoxLayout()
        sw.setContentsMargins(0, 0, 6, 0)
        sw.addWidget(self.search)
        ll.addLayout(sw)
        chips = QHBoxLayout()
        chips.setContentsMargins(2, 0, 0, 0)
        self.chip_all, self.chip_unread = button("Toutes", "chip"), button("Non lues", "chip")
        for ch in (self.chip_all, self.chip_unread):
            ch.setCheckable(True)
            chips.addWidget(ch)
        self.chip_all.setChecked(True)
        self.chip_all.clicked.connect(lambda: self._set_filter(False))
        self.chip_unread.clicked.connect(lambda: self._set_filter(True))
        chips.addStretch(1)
        ll.addLayout(chips)
        self.list = ConvList()
        self.list.selected.connect(self._on_pick)
        ll.addWidget(self.list, 1)
        self.empty_list = label("Aucune conversation.\nAjoutez un contact avec son code d'invitation.", "muted", wrap=True)
        self.empty_list.setAlignment(Qt.AlignCenter)
        ll.addWidget(self.empty_list)
        root.addWidget(self.left)
        # centre
        self.center = GlassPanel(radius=24)
        cl = QVBoxLayout(self.center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_chat())
        root.addWidget(self.center, 1)
        # détails
        self.details = GlassPanel(radius=24)
        self.details.setFixedWidth(316)
        self.details_lay = QVBoxLayout(self.details)
        self.details_lay.setContentsMargins(20, 22, 20, 18)
        self.details_lay.setSpacing(10)
        self.details.hide()
        root.addWidget(self.details)

    def _tool(self, icon: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setProperty("kind", "icon")
        b.setIcon(icons.icon(icon, theme.icon_hex(1), 20))
        b.setIconSize(QSize(20, 20))
        b.setFixedSize(38, 38)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _build_empty(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setAlignment(Qt.AlignCenter)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(96))
        logo.setAlignment(Qt.AlignCenter)
        l.addWidget(logo)
        t = label("Vos messages sont chiffrés de bout en bout", "h2")
        t.setAlignment(Qt.AlignCenter)
        s = label("Sélectionnez une conversation ou ajoutez un contact.\nLes serveurs ne peuvent lire ni vos messages ni vos fichiers.", "muted")
        s.setAlignment(Qt.AlignCenter)
        l.addSpacing(8)
        l.addWidget(t)
        l.addWidget(s)
        return w

    def _build_chat(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(22, 14, 14, 12)
        hl.setSpacing(12)
        self.h_avatar = Avatar("", "?", 44, status="offline")
        hl.addWidget(self.h_avatar)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.h_title = label("", "h3")
        self.h_sub = label("", "faint")
        col.addWidget(self.h_title)
        col.addWidget(self.h_sub)
        hl.addLayout(col, 1)
        self.h_lock = QLabel()
        self.h_lock.setPixmap(icons.pixmap("lock", "#34D399", 16))
        self.h_lock.setToolTip("Chiffrement de bout en bout")
        hl.addWidget(self.h_lock)
        self.btn_find = self._tool("search", "Rechercher dans les messages")
        self.btn_details = self._tool("panel", "Détails de la conversation")
        hl.addWidget(self.btn_find)
        hl.addWidget(self.btn_details)
        v.addWidget(hdr)
        v.addWidget(divider())
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background: rgba(251,191,36,0.14); color: %s; padding: 8px 22px; font-size: 9pt;" % ("#92400E" if theme.is_light() else "#FCD34D"))
        self.banner.hide()
        v.addWidget(self.banner)
        self.view = ChatView()
        self.view.setContentsMargins(0, 8, 0, 8)
        v.addWidget(self.view, 1)
        self.typing_lbl = label("", "faint")
        self.typing_lbl.setContentsMargins(26, 0, 0, 2)
        self.typing_lbl.setFixedHeight(18)
        v.addWidget(self.typing_lbl)
        cw = QWidget()
        cwl = QVBoxLayout(cw)
        cwl.setContentsMargins(16, 0, 16, 14)
        self.composer = Composer()
        cwl.addWidget(self.composer)
        v.addWidget(cw)
        # signaux
        self.composer.send_text.connect(self._send)
        self.composer.attach.connect(self._attach)
        self.composer.image_pasted.connect(self._on_image_pasted)
        self.composer.files_dropped.connect(self._offer_files)
        self.composer.typing.connect(self._typing)
        self.view.action.connect(self._on_action)
        self.view.link.connect(self._open_link)
        self.view.context.connect(self._context_menu)
        self.view.load_more.connect(self._load_older)
        self.btn_details.clicked.connect(self.toggle_details)
        self.btn_find.clicked.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self._last_typing_sent = 0.0
        return w

    # ------------------------------------------------------------------ liste
    def _set_filter(self, unread: bool) -> None:
        self.filter_unread = unread
        self.chip_all.setChecked(not unread)
        self.chip_unread.setChecked(unread)
        self.refresh_list()

    def refresh_list(self) -> None:
        e = self.engine
        q = self.search.text().strip()
        items = []
        for c in e.conversations():
            c["preview"] = preview_of(c["last"], e.me, e.display_name, c["kind"])
            c["typing"] = c["conv"] in self.typing_in
            if self.filter_unread and not c["unread"]:
                continue
            if q and q.casefold() not in c["title"].casefold():
                continue
            items.append(c)
        if q:
            seen_titles = {i["conv"]: i["title"] for i in e.conversations()}
            for m in e.search(q)[:40]:
                if m["conv"] not in seen_titles:
                    continue
                snippet = (m["body"] or "").replace("\n", " ")
                idx = snippet.casefold().find(q.casefold())
                snippet = ("…" if idx > 25 else "") + snippet[max(0, idx - 25):][:80]
                items.append({"conv": m["conv"], "kind": "group" if m["conv"].startswith("grp:") else "dm",
                              "title": seen_titles[m["conv"]], "state": "accepted", "presence": "offline", "unread": 0,
                              "last": m, "pinned": 0, "muted": False, "result": snippet, "mid": m["mid"]})
        self.list.current_conv = self.conv
        self.list.set_items(items)
        self.empty_list.setVisible(not items)
        if q and not items:
            self.empty_list.setText("Aucun résultat.")
        elif not items:
            self.empty_list.setText("Aucune conversation.\nAjoutez un contact avec son code d'invitation.")
        self.total_unread = sum(c["unread"] for c in items if not c.get("result"))

    def _on_pick(self, item: dict) -> None:
        self.open_conv(item["conv"], item.get("mid"))

    # ------------------------------------------------------------------ ouverture
    def open_conv(self, conv: str, focus_mid: str | None = None) -> None:
        self.conv, self.focus_mid = conv, focus_mid
        self.view.highlight = self.search.text().strip() if focus_mid else ""
        self._show_conv()
        self.list.current_conv = conv
        self.list.reselect()

    def _summary(self) -> dict | None:
        return next((c for c in self.engine.conversations() if c["conv"] == self.conv), None)

    def _is_group(self) -> bool:
        return bool(self.conv and self.conv.startswith("grp:"))

    def _name_of(self, sid: str) -> str:
        return self.engine.display_name(sid)

    def _quote_of(self, mid: str):
        m = self.engine.db.get_message(mid)
        if not m:
            return {"name": "Message", "text": "Message supprimé"}
        return {"name": "Vous" if m["direction"] == "out" else self._name_of(m["sender"]), "text": m["body"] or ""}

    def _transfers(self, msgs: list[dict]) -> dict:
        return {m["mid"]: self.engine.transfer(m["mid"]) for m in msgs if m["kind"] == "file"}

    def _show_conv(self) -> None:
        s = self._summary() if self.conv else None
        if not s:
            self.conv = None
            self.stack.setCurrentIndex(0)
            self.details.hide()
            return
        self.stack.setCurrentIndex(1)
        self.h_avatar.set(s["conv"], s["title"], s["presence"] if s["kind"] == "dm" else None, group=s["kind"] == "group")
        if self.h_avatar.dot:
            self.h_avatar.dot.setVisible(s["kind"] == "dm" and s["state"] == "accepted")
        self.h_title.setText(s["title"])
        self._update_subtitle(s)
        limit = 300 if self.focus_mid else 80
        msgs = self.engine.messages(self.conv, limit=limit)
        self.loaded_all = len(msgs) < limit
        unread = self.engine.db.unread_mids(self.conv)
        items = build_items(msgs, self._name_of, self._is_group(), self._transfers(msgs), quote_of=self._quote_of,
                            reactions=self.engine.db.reactions_for([m["mid"] for m in msgs]), me=self.engine.me,
                            new_from=unread[0] if unread and unread[0] in {m["mid"] for m in msgs} else None)
        self.view.set_items(items)
        self.unseen_below = 0
        if self.focus_mid:
            self.view.scroll_to_mid(self.focus_mid)
        elif unread and self.view.model_.row_of("new-marker") >= 0:
            self.view.scroll_to_mid("new-marker", center=False)
        else:
            self.view.scroll_bottom()
        QTimer.singleShot(200, self._update_scroll_btn)
        self._configure_composer(s)
        self._update_banner()
        self._mark_read()
        if self.details_open:
            self.rebuild_details()
        self.refresh_list_light()

    def refresh_list_light(self) -> None:
        QTimer.singleShot(0, self.refresh_list)

    def _update_subtitle(self, s: dict) -> None:
        if s["kind"] == "group":
            g = self.engine.db.get_group(s["conv"][4:])
            n = len(g["members"]) if g else 0
            self.h_sub.setText(f"{n} membres · groupe chiffré")
        elif s["state"] == "accepted":
            e = self.engine
            mode = " · connexion directe" if e.is_direct(s["conv"]) else ""
            c = e.db.get_contact(s["conv"])
            ver = " · vérifié" if c and c["verified"] else ""
            self.h_sub.setText(theme.PRESENCE_LABELS.get(s["presence"], "") + mode + ver)
        elif s["state"] == "pending_in":
            self.h_sub.setText("Demande de contact reçue")
        else:
            self.h_sub.setText("En attente d'acceptation")
        self.h_lock.setVisible(s["state"] == "accepted")

    def _configure_composer(self, s: dict) -> None:
        if s["state"] == "pending_in":
            self.composer.set_enabled_state(False, "Acceptez la demande pour discuter")
        elif s["state"] == "pending_out":
            self.composer.set_enabled_state(False, "Vous pourrez écrire dès que la demande sera acceptée")
        else:
            self.composer.set_enabled_state(True)
        self.composer.btn_attach.setVisible(s["kind"] == "dm")
        if s["kind"] == "group":
            g = self.engine.db.get_group(s["conv"][4:])
            self.composer.set_members([(m, self.engine.display_name(m)) for m in (g["members"] if g else []) if m != self.engine.me])
        else:
            self.composer.set_members([])

    def _update_banner(self) -> None:
        e = self.engine
        s = e.settings
        if not self.conv:
            return
        if not s.server_host and not e.direct:
            self.banner.setText("Aucun serveur configuré : configurez-en un dans Paramètres › Réseau pour joindre des contacts hors de votre réseau local.")
        elif s.server_host and not (e.relay and e.relay.connected):
            self.banner.setText("Serveur injoignable : vos messages sont enregistrés et partiront automatiquement à la reconnexion.")
        else:
            self.banner.hide()
            return
        self.banner.show()

    def _mark_read(self) -> None:
        if self.conv and self.window().isActiveWindow() and self.engine.db.unread_mids(self.conv):
            self.ctrl.run(self.engine.mark_read(self.conv))

    def on_window_activated(self) -> None:
        self._mark_read()

    # ------------------------------------------------------------------ événements moteur
    def _on_conv_changed(self, conv: str) -> None:
        self.refresh_list()
        if conv and conv == self.conv or (self.conv and not conv):
            s = self._summary()
            if s is None:
                self._show_conv()
            else:
                self._update_subtitle(s)
                self._configure_composer(s)
                self.h_title.setText(s["title"])
                if self.details_open:
                    self.rebuild_details()

    def _on_message(self, conv: str, mid: str, direction: str) -> None:
        if conv != self.conv:
            return
        m = self.engine.db.get_message(mid)
        if not m:
            return
        model = self.view.model_
        prev = next((i for i in reversed(model.items) if i["type"] == "msg"), None)
        stick = self.view.at_bottom() or direction == "out"
        items = build_items([m], self._name_of, self._is_group(), self._transfers([m]),
                            prev_ts=prev["msg"]["ts"] if prev else None,
                            quote_of=self._quote_of, prev_sender=prev["sender"] if prev else None,
                            reactions=self.engine.db.reactions_for([mid]), me=self.engine.me)
        if prev is None and model.items and model.items[-1]["type"] == "date":
            items = [i for i in items if i["type"] != "date"]
        model.append(items)
        if stick:
            self.view.scroll_bottom()
        else:
            self.unseen_below += 1
            QTimer.singleShot(0, self._update_scroll_btn)
        if direction == "in":
            self._mark_read()

    def _refresh_item(self, mid: str) -> None:
        m = self.engine.db.get_message(mid)
        if m and self.view.model_.row_of(mid) >= 0:
            self.view.delegate.clear_cache()
            self.view.model_.replace(mid, msg=m, transfer=self.engine.transfer(mid) if m["kind"] == "file" else None,
                                     reactions=self.engine.db.reactions_for([mid]).get(mid, {}))

    def _on_status(self, mid: str, status: str) -> None:
        self._refresh_item(mid)

    def _on_updated(self, mid: str, conv: str) -> None:
        self._refresh_item(mid)

    def _on_deleted(self, mid: str, conv: str) -> None:
        if conv == self.conv:
            self.view.model_.remove(mid)
            self.view.delegate.clear_cache()

    def _on_contacts_changed(self) -> None:
        self.refresh_list()
        if self.conv:
            s = self._summary()
            if s is None:
                self.conv = None
            self._show_conv()

    def _on_presence(self) -> None:
        self.refresh_list()
        s = self._summary() if self.conv else None
        if s:
            self._update_subtitle(s)
            if s["kind"] == "dm" and self.h_avatar.dot:
                self.h_avatar.dot.set_status(s["presence"])

    def _on_typing(self, conv: str, peer: str) -> None:
        t = self.typing_in.get(conv)
        if t:
            t.stop()
        else:
            t = QTimer(self, singleShot=True)
            t.timeout.connect(lambda c=conv: self._typing_end(c))
            self.typing_in[conv] = t
        t.start(4500)
        if conv == self.conv:
            who = self._name_of(peer) if self._is_group() else ""
            self.typing_lbl.setText((who + " " if who else "") + "est en train d'écrire…")
        self.refresh_list()

    def _typing_end(self, conv: str) -> None:
        self.typing_in.pop(conv, None)
        if conv == self.conv:
            self.typing_lbl.setText("")
        self.refresh_list()

    def _on_file_state(self, mid: str, state: str) -> None:
        self._refresh_item(mid)

    def _on_file_progress(self, mid: str, done: int, total: int) -> None:
        row = self.view.model_.row_of(mid)
        if row >= 0:
            self.view.model_.replace(mid, transfer=self.engine.transfer(mid), progress=done / max(1, total))

    # ------------------------------------------------------------------ actions utilisateur
    def _send(self, text: str) -> None:
        if not self.conv:
            return
        mode, target = self.composer.mode, self.composer.target

        def sent() -> None:  # le message est déjà enregistré localement : on peut vider la zone de saisie
            self.composer.clear()
            self.composer.reset_mode()

        if mode == "edit" and target:
            self.ctrl.run(self.engine.edit_message(target, text), on_success=sent)
        else:
            self.ctrl.run(self.engine.send_text(self.conv, text, reply_to=target if mode == "reply" else None,
                                                mentions=self.composer.mentions_in(text)), on_success=sent)

    def _typing(self) -> None:
        import time
        now = time.monotonic()
        if self.conv and now - self._last_typing_sent > 3:
            self._last_typing_sent = now
            self.ctrl.run(self.engine.send_typing(self.conv))

    def _attach(self) -> None:
        if not self.conv:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choisir un fichier à envoyer")
        if path:
            self.ctrl.run(self.engine.send_file(self.conv, path))

    def _load_older(self) -> None:
        if not self.conv or getattr(self, "loaded_all", True):
            return
        model = self.view.model_
        first = next((i for i in model.items if i["type"] == "msg"), None)
        if not first:
            return
        m0 = first["msg"]
        older = self.engine.messages(self.conv, before=(m0["ts"], m0["id"]), limit=60)
        if len(older) < 60:
            self.loaded_all = True
        if not older:
            return
        sb = self.view.verticalScrollBar()
        before_max, before_val = sb.maximum(), sb.value()
        items = build_items(older, self._name_of, self._is_group(), self._transfers(older), quote_of=self._quote_of,
                            reactions=self.engine.db.reactions_for([m["mid"] for m in older]), me=self.engine.me)
        if model.items and model.items[0]["type"] == "date" and items and items[-1].get("mid") == model.items[0].get("mid"):
            model.remove(model.items[0]["mid"])
        model.prepend(items)
        self.view.delegate.clear_cache()
        QTimer.singleShot(0, lambda: sb.setValue(before_val + sb.maximum() - before_max))

    def _on_action(self, name: str, mid: str) -> None:
        e = self.engine
        if name == "accept":
            self.ctrl.run(e.accept_file(mid))
        elif name == "decline":
            self.ctrl.run(e.decline_file(mid))
        elif name == "retry":
            self.ctrl.run(e.retry_message(mid))
        elif name == "cancel":
            self.ctrl.run(e.cancel_file(mid))
        elif name.startswith("react:"):
            self.ctrl.run(e.react(mid, name[6:]))
        elif name == "open":
            tr = e.transfer(mid)
            if tr and tr.get("path") and os.path.exists(tr["path"]):
                p = Path(tr["path"])
                if p.suffix.lower() in DANGEROUS_EXT:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))
                    self.ctrl.info.emit("Fichier exécutable : le dossier a été ouvert, rien n'a été lancé.")
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _open_link(self, url: str) -> None:
        if not url.lower().startswith(("http://", "https://")):
            return
        dlg = ConfirmDialog(self, "Ouvrir ce lien ?", f"{url}\n\nVous quittez LDC P2P pour ouvrir ce lien dans votre navigateur.", "Ouvrir")
        if dlg.exec():
            QDesktopServices.openUrl(QUrl(url))

    def _context_menu(self, mid: str, pos) -> None:
        m = self.engine.db.get_message(mid)
        if not m:
            return
        menu = QMenu(self)
        sel = self.view.selected_text()
        if sel:
            menu.addAction(icons.icon("copy", theme.icon_hex(1), 16), "Copier la sélection", lambda: copy_text(sel))
        menu.addAction(icons.icon("copy", theme.icon_hex(1), 16), "Copier le message", lambda: copy_text(m["body"] or ""))
        if self.composer.btn_send.isEnabled():
            rm = menu.addMenu(icons.icon("smile", theme.icon_hex(1), 16), "Réagir")
            for em in ALLOWED_REACTIONS:
                rm.addAction(em, lambda em=em: self.ctrl.run(self.engine.react(mid, em)))
        if self.composer.btn_send.isEnabled():
            menu.addAction(icons.icon("reply", theme.icon_hex(1), 16), "Répondre",
                           lambda: self.composer.start_reply(mid, "Vous" if m["direction"] == "out" else self._name_of(m["sender"]), m["body"] or ""))
        if m["direction"] == "out" and m["kind"] == "text":
            menu.addAction(icons.icon("edit", theme.icon_hex(1), 16), "Modifier", lambda: self.composer.start_edit(mid, m["body"] or ""))
        if m["status"] == "failed":
            menu.addAction(icons.icon("refresh", theme.icon_hex(1), 16), "Réessayer l'envoi", lambda: self.ctrl.run(self.engine.retry_message(mid)))
        if m["direction"] == "out":
            menu.addAction(icons.icon("info", theme.icon_hex(1), 16), "Infos de remise", lambda: self._show_info(mid))
        menu.addSeparator()
        menu.addAction(icons.icon("trash", "#F87171", 16), "Supprimer pour moi", lambda: self.ctrl.run(self.engine.delete_message(mid)))
        if m["direction"] == "out" and m["kind"] != "system":
            menu.addAction(icons.icon("trash", "#F87171", 16), "Supprimer pour tous", lambda: self._confirm_delete_all(mid))
        menu.exec(pos)

    def _show_info(self, mid: str) -> None:
        m = self.engine.db.get_message(mid)
        if m:
            rows = [(self.engine.display_name(p), st) for p, st in self.engine.db.receipts(mid).items()]
            MessageInfoDialog(self, m["body"] or "", rows).exec()

    def _confirm_delete_all(self, mid: str) -> None:
        if ConfirmDialog(self, "Supprimer pour tous ?", "Le message sera supprimé chez vos correspondants s'ils sont en ligne ou à leur prochaine connexion. "
                         "Rien ne garantit qu'il n'a pas déjà été lu ou copié.", "Supprimer", True).exec():
            self.ctrl.run(self.engine.delete_message(mid, for_everyone=True))

    # ------------------------------------------------------------------ panneau de détails
    def toggle_details(self) -> None:
        self.details_open = not self.details_open
        self.details.setVisible(self.details_open and self.conv is not None)
        if self.details_open:
            self.rebuild_details()

    def _clear_details(self) -> None:
        while self.details_lay.count():
            it = self.details_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
            elif it.layout():
                self._drain(it.layout())

    def _drain(self, lay) -> None:
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._drain(it.layout())

    def _row_switch(self, text: str, checked: bool, cb) -> QHBoxLayout:
        r = QHBoxLayout()
        r.addWidget(label(text), 1)
        sw = Switch(checked)
        sw.toggled.connect(cb)
        r.addWidget(sw)
        return r

    def rebuild_details(self) -> None:
        self._clear_details()
        s = self._summary() if self.conv else None
        if not s:
            self.details.hide()
            return
        self.details.setVisible(True)
        e, L = self.engine, self.details_lay
        av = Avatar(s["conv"], s["title"], 84, status=(s["presence"] if s["kind"] == "dm" and s["state"] == "accepted" else None), group=s["kind"] == "group")
        L.addWidget(av, 0, Qt.AlignHCenter)
        nm = label(s["title"], "h2")
        nm.setAlignment(Qt.AlignCenter)
        nm.setWordWrap(True)
        L.addWidget(nm)
        if s["kind"] == "dm":
            c = e.db.get_contact(s["conv"])
            idl = label(s["conv"], "mono")
            idl.setStyleSheet("font-size: 8pt; color: %s;" % ("rgba(20,20,40,0.6)" if theme.is_light() else "rgba(235,235,245,0.55)"))
            idl.setAlignment(Qt.AlignCenter)
            idl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            L.addWidget(idl)
            if s["state"] == "pending_in":
                a, d = button("Accepter la demande", "primary"), button("Refuser", "danger")
                a.clicked.connect(lambda: self.ctrl.run(e.accept_request(s["conv"])))
                d.clicked.connect(lambda: self.ctrl.run(e.decline_request(s["conv"])))
                L.addSpacing(8)
                L.addWidget(a)
                L.addWidget(d)
                L.addStretch(1)
                return
            if s["state"] == "pending_out":
                x = button("Annuler la demande", "danger")
                x.clicked.connect(lambda: self.ctrl.run(e.cancel_request(s["conv"])))
                L.addSpacing(8)
                L.addWidget(x)
                L.addStretch(1)
                return
            ver = bool(c and c["verified"])
            L.addSpacing(6)
            vb = button("Contact vérifié" if ver else "Vérifier l'identité", "", "shield", "#34D399" if ver else "#FBBF24")
            vb.clicked.connect(lambda: self._safety(s["conv"]))
            L.addWidget(vb)
            rn = button("Renommer", "", "edit")
            rn.clicked.connect(lambda: self._rename_contact(s["conv"], s["title"]))
            L.addWidget(rn)
        else:
            self._group_details(s)
        L.addSpacing(6)
        L.addWidget(divider())
        L.addWidget(label("CONFIDENTIALITÉ", "section"))
        combo = QComboBox()
        for txt, val in TTL_CHOICES:
            combo.addItem(txt, val)
        combo.setCurrentIndex(max(0, next((i for i, (_, v) in enumerate(TTL_CHOICES) if v == s["ttl"]), 0)))
        combo.currentIndexChanged.connect(lambda i: self.ctrl.run(e.set_conv_ttl(s["conv"], combo.itemData(i))))
        L.addWidget(label("Messages éphémères", "muted"))
        L.addWidget(combo)
        L.addLayout(self._row_switch("Muet", s["muted"], lambda v: (e.db.set_conv_setting(s["conv"], muted=int(v)), self.refresh_list())))
        L.addLayout(self._row_switch("Épinglé en haut", bool(s["pinned"]), lambda v: (e.db.set_conv_setting(s["conv"], pinned=int(v)), self.refresh_list())))
        L.addStretch(1)
        clr = button("Vider la conversation", "", "trash", "#F87171")
        clr.clicked.connect(lambda: self._clear_conv(s))
        L.addWidget(clr)
        if s["kind"] == "dm":
            blk = button("Bloquer ce contact", "danger", "ban", "#FCA5A5")
            blk.clicked.connect(lambda: self._block(s))
            L.addWidget(blk)

    def _group_details(self, s: dict) -> None:
        e, L = self.engine, self.details_lay
        g = e.db.get_group(s["conv"][4:])
        if not g:
            return
        owner = g["owner"] == e.me
        L.addWidget(label(f"MEMBRES ({len(g['members'])})", "section"))
        for m in g["members"]:
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(Avatar(m, e.display_name(m), 30))
            row.addWidget(label(e.display_name(m) + (" · propriétaire" if m == g["owner"] else "")), 1)
            if owner and m != e.me:
                rm = QPushButton()
                rm.setProperty("kind", "icon")
                rm.setIcon(icons.icon("x", "#F87171", 16))
                rm.setToolTip("Retirer du groupe")
                rm.clicked.connect(lambda _=False, mm=m: self.ctrl.run(e.group_remove_member(s["conv"], mm)))
                row.addWidget(rm)
            L.addLayout(row)
        if owner:
            add = button("Ajouter des membres", "", "user-plus")
            add.clicked.connect(lambda: self._add_members(s["conv"], g["members"]))
            ren = button("Renommer le groupe", "", "edit")
            ren.clicked.connect(lambda: self._rename_group(s["conv"], s["title"]))
            L.addWidget(add)
            L.addWidget(ren)
        leave = button("Dissoudre le groupe" if owner else "Quitter le groupe", "danger", "log-out", "#FCA5A5")
        leave.clicked.connect(lambda: self._leave(s, owner))
        L.addWidget(leave)

    def _safety(self, cid: str) -> None:
        e = self.engine
        c = e.db.get_contact(cid)
        dlg = SafetyDialog(self, e.display_name(cid), e.contact_safety_number(cid), bool(c["verified"]))
        dlg.exec()
        if dlg.verified_value != bool(c["verified"]):
            e.set_verified(cid, dlg.verified_value)
        self._on_conv_changed(cid)

    def _rename_contact(self, cid: str, current: str) -> None:
        d = TextPrompt(self, "Renommer le contact", "Ce nom n'est visible que par vous.", current)
        if d.exec():
            self.engine.set_alias(cid, d.value)

    def _rename_group(self, conv: str, current: str) -> None:
        d = TextPrompt(self, "Renommer le groupe", "Tous les membres verront le nouveau nom.", current)
        if d.exec() and d.value:
            self.ctrl.run(self.engine.group_rename(conv, d.value))

    def _add_members(self, conv: str, members: list[str]) -> None:
        cands = [c for c in self.engine.contacts({"accepted"}) if c["id"] not in members]
        if not cands:
            self.ctrl.error.emit("Tous vos contacts sont déjà dans ce groupe.")
            return
        d = PickMembersDialog(self, cands)
        if d.exec():
            self.ctrl.run(self.engine.group_add_members(conv, d.result_ids))

    def _leave(self, s: dict, owner: bool) -> None:
        txt = "Le groupe sera supprimé pour tous ses membres." if owner else "Vous ne recevrez plus les messages de ce groupe."
        if ConfirmDialog(self, "Dissoudre le groupe ?" if owner else "Quitter le groupe ?", txt, "Confirmer", True).exec():
            self.ctrl.run(self.engine.leave_group(s["conv"]))
            self.conv = None

    def _clear_conv(self, s: dict) -> None:
        if ConfirmDialog(self, "Vider la conversation ?", "Les messages de cet appareil seront supprimés. Vos correspondants conservent les leurs.", "Vider", True).exec():
            self.engine.db.delete_conversation(s["conv"])
            self._show_conv()
            self.refresh_list()

    def _block(self, s: dict) -> None:
        if ConfirmDialog(self, f"Bloquer {s['title']} ?", "Cette personne ne pourra plus vous écrire, ni voir votre présence. Elle n'en sera pas informée.", "Bloquer", True).exec():
            self.ctrl.run(self.engine.block(s["conv"]))
            self.conv = None
