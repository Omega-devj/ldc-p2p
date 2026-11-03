"""Page « Contacts » : demandes, contacts acceptés, bloqués."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import icons, theme
from ..controller import UiController
from ..dialogs import button
from ..widgets.glass import Avatar, GlassPanel, label


class ContactsPage(QWidget):
    open_chat = Signal(str)
    add_contact = Signal()

    def __init__(self, ctrl: UiController, parent=None):
        super().__init__(parent)
        self.ctrl, self.engine = ctrl, ctrl.engine
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = GlassPanel(radius=24)
        root.addWidget(panel)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(28, 24, 20, 20)
        pl.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Contacts", "h2"), 1)
        add = button("Ajouter un contact", "primary", "user-plus", "#FFFFFF")
        add.clicked.connect(self.add_contact)
        head.addWidget(add)
        pl.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrer par nom ou identifiant")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", "#8E8EA0", 18), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        pl.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.holder.setStyleSheet("background: transparent;")
        self.rows = QVBoxLayout(self.holder)
        self.rows.setContentsMargins(0, 0, 10, 0)
        self.rows.setSpacing(8)
        self.rows.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.holder)
        pl.addWidget(self.scroll, 1)
        ctrl.contacts_changed.connect(self.refresh)
        ctrl.presence_changed.connect(self.refresh)
        self.refresh()

    def _clear(self) -> None:
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _section(self, title: str, count: int) -> None:
        self.rows.addSpacing(8)
        self.rows.addWidget(label(f"{title}  ·  {count}", "section"))

    def _row(self, c: dict, actions: list[QPushButton], subtitle: str, status: str | None = None) -> QWidget:
        w = GlassPanel(radius=16)
        h = QHBoxLayout(w)
        h.setContentsMargins(14, 10, 12, 10)
        h.setSpacing(12)
        h.addWidget(Avatar(c["id"], c["alias"] or c["name"], 42, status=status))
        col = QVBoxLayout()
        col.setSpacing(0)
        nm = label(c["alias"] or c["name"], "h3")
        col.addWidget(nm)
        col.addWidget(label(subtitle, "faint"))
        h.addLayout(col, 1)
        if c["verified"]:
            sh = label("")
            sh.setPixmap(icons.pixmap("shield", "#34D399", 18))
            sh.setToolTip("Identité vérifiée")
            h.addWidget(sh)
        for a in actions:
            h.addWidget(a)
        return w

    def refresh(self) -> None:
        e = self.engine
        q = self.search.text().strip().casefold()
        contacts = [c for c in e.contacts() if c["state"] != "group" and (not q or q in (c["alias"] or c["name"]).casefold() or q in c["id"].casefold())]
        self._clear()
        groups = {s: [c for c in contacts if c["state"] == s] for s in ("pending_in", "pending_out", "accepted", "blocked")}
        if groups["pending_in"]:
            self._section("DEMANDES REÇUES", len(groups["pending_in"]))
            for c in groups["pending_in"]:
                ok, no = button("Accepter", "primary"), button("Refuser", "danger")
                ok.clicked.connect(lambda _=False, i=c["id"]: self.ctrl.run(e.accept_request(i)))
                no.clicked.connect(lambda _=False, i=c["id"]: self.ctrl.run(e.decline_request(i)))
                self.rows.addWidget(self._row(c, [ok, no], "souhaite vous ajouter · " + c["id"]))
        if groups["accepted"]:
            self._section("CONTACTS", len(groups["accepted"]))
            for c in groups["accepted"]:
                pres = e.presence_of(c["id"])
                msg = button("Message", "", "chat")
                msg.clicked.connect(lambda _=False, i=c["id"]: self.open_chat.emit(i))
                self.rows.addWidget(self._row(c, [msg], theme.PRESENCE_LABELS.get(pres, "") + " · " + c["id"], pres))
        if groups["pending_out"]:
            self._section("EN ATTENTE DE RÉPONSE", len(groups["pending_out"]))
            for c in groups["pending_out"]:
                cancel = button("Annuler", "ghost")
                cancel.clicked.connect(lambda _=False, i=c["id"]: self.ctrl.run(e.cancel_request(i)))
                self.rows.addWidget(self._row(c, [cancel], "demande envoyée · " + c["id"]))
        if groups["blocked"]:
            self._section("BLOQUÉS", len(groups["blocked"]))
            for c in groups["blocked"]:
                ub, rm = button("Débloquer", ""), button("Supprimer", "danger")
                ub.clicked.connect(lambda _=False, i=c["id"]: self.ctrl.run(e.unblock(i)))
                rm.clicked.connect(lambda _=False, i=c["id"]: self.ctrl.run(e.delete_contact(i)))
                self.rows.addWidget(self._row(c, [ub, rm], c["id"]))
        if not any(groups.values()):
            empty = label("Aucun contact pour l'instant.\nÉchangez votre code d'invitation avec un ami pour commencer.", "muted")
            empty.setAlignment(Qt.AlignCenter)
            self.rows.addSpacing(60)
            self.rows.addWidget(empty)
