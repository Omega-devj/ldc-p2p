"""Dialogues de l'application (même langage visuel que la fenêtre principale)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..crypto.identity import ID_RE
from . import icons, theme
from .widgets.glass import AuroraBackground, GlassPanel, Switch, divider, label


def button(text: str, kind: str = "", icon: str | None = None, color: str = "#F3F3F8") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon:
        b.setIcon(icons.icon(icon, color, 18))
        b.setIconSize(QSize(18, 18))
    b.setCursor(Qt.PointingHandCursor)
    return b


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


class GlassDialog(QDialog):
    def __init__(self, parent, title: str, subtitle: str = "", width: int = 520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.bg = AuroraBackground()
        root.addWidget(self.bg)
        self.body = QVBoxLayout(self.bg)
        self.body.setContentsMargins(28, 26, 28, 24)
        self.body.setSpacing(14)
        self.body.addWidget(label(title, "h2"))
        if subtitle:
            self.body.addWidget(label(subtitle, "muted", wrap=True))
        self.setSizeGripEnabled(False)

    def buttons(self, *buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        row.setSpacing(10)
        for b in buttons:
            row.addWidget(b)
        self.body.addSpacing(6)
        self.body.addLayout(row)
        return row


class AddContactDialog(GlassDialog):
    def __init__(self, parent, my_code: str, my_name: str, my_id: str):
        super().__init__(parent, "Ajouter un contact",
                         "Échangez vos codes d'invitation par un canal de confiance. Un code ne contient ni adresse IP ni mot de passe.", 560)
        self.code_value: str | None = None
        self.greeting_value = ""
        self.body.addWidget(label("MON CODE D'INVITATION", "section"))
        mine = GlassPanel(radius=14)
        ml = QVBoxLayout(mine)
        ml.setContentsMargins(14, 12, 14, 12)
        who = label(f"{my_name}  ·  {my_id}", "faint")
        ml.addWidget(who)
        self.mine = QPlainTextEdit(my_code)
        self.mine.setReadOnly(True)
        self.mine.setMinimumHeight(70)
        self.mine.setMaximumHeight(70)
        self.mine.setProperty("role", "mono")
        ml.addWidget(self.mine)
        cp = button("Copier mon code", "", "copy")
        cp.clicked.connect(lambda: (copy_text(my_code), cp.setText("Copié ✓"), QTimer.singleShot(1600, lambda: cp.setText("Copier mon code"))))
        ml.addWidget(cp, 0, Qt.AlignLeft)
        self.body.addWidget(mine)
        self.body.addWidget(divider())
        self.body.addWidget(label("AJOUTER QUELQU'UN", "section"))
        self.code = QPlainTextEdit()
        self.code.setPlaceholderText("Collez ici le code d'invitation reçu (ldc1:…)")
        self.code.setFixedHeight(70)
        self.body.addWidget(self.code)
        self.greet = QLineEdit()
        self.greet.setPlaceholderText("Message d'accompagnement (facultatif)")
        self.greet.setMaxLength(120)
        self.body.addWidget(self.greet)
        self.err = label("", "error", wrap=True)
        self.body.addWidget(self.err)
        cancel, ok = button("Annuler", "ghost"), button("Envoyer la demande", "primary", "user-plus", "#FFFFFF")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        self.buttons(cancel, ok)

    def _ok(self) -> None:
        text = self.code.toPlainText().strip()
        if not text:
            self.err.setText("Collez d'abord le code d'invitation de votre contact.")
            return
        self.code_value, self.greeting_value = text, self.greet.text().strip()
        self.accept()


class NewGroupDialog(GlassDialog):
    def __init__(self, parent, contacts: list[dict]):
        super().__init__(parent, "Nouveau groupe privé",
                         "Chaque message est chiffré séparément pour chaque membre : le serveur ne voit ni le contenu ni la liste.", 500)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nom du groupe")
        self.name.setMaxLength(40)
        self.body.addWidget(self.name)
        self.body.addWidget(label("MEMBRES", "section"))
        self.list = QListWidget()
        self.list.setFixedHeight(220)
        for c in contacts:
            it = QListWidgetItem(c["alias"] or c["name"])
            it.setData(Qt.UserRole, c["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.list.addItem(it)
        self.list.setStyleSheet("QListWidget::item { padding: 9px 8px; border-radius: 8px; } QListWidget::item:hover { background: rgba(255,255,255,0.06); }")
        self.body.addWidget(self.list)
        self.err = label("", "error")
        self.body.addWidget(self.err)
        cancel, ok = button("Annuler", "ghost"), button("Créer le groupe", "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        self.buttons(cancel, ok)
        self.result_name, self.result_ids = "", []

    def _ok(self) -> None:
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]
        if not self.name.text().strip():
            self.err.setText("Donnez un nom au groupe.")
        elif not ids:
            self.err.setText("Choisissez au moins un membre.")
        else:
            self.result_name, self.result_ids = self.name.text().strip(), ids
            self.accept()


class PickMembersDialog(NewGroupDialog):
    def __init__(self, parent, contacts: list[dict]):
        super().__init__(parent, contacts)
        self.setWindowTitle("Ajouter des membres")
        self.name.hide()

    def _ok(self) -> None:
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]
        if not ids:
            self.err.setText("Choisissez au moins un contact.")
            return
        self.result_ids = ids
        self.accept()


class SafetyDialog(GlassDialog):
    def __init__(self, parent, name: str, number: str, verified: bool):
        super().__init__(parent, f"Vérifier {name}",
                         "Comparez ces 30 chiffres avec ceux affichés chez votre contact (en personne, ou par un appel). "
                         "S'ils sont identiques, personne ne se fait passer pour lui.", 520)
        self.verified_value = verified
        panel = GlassPanel(radius=18, strong=True)
        g = QGridLayout(panel)
        g.setContentsMargins(24, 20, 24, 20)
        g.setHorizontalSpacing(26)
        g.setVerticalSpacing(12)
        for i, grp in enumerate(number.split()):
            l = label(grp, "mono")
            l.setStyleSheet("font-size: 17pt; letter-spacing: 3px; font-weight: 600;")
            g.addWidget(l, i // 3, i % 3, Qt.AlignCenter)
        self.body.addWidget(panel)
        self.state = label("", "ok" if verified else "muted")
        self.body.addWidget(self.state)
        self._refresh()
        close = button("Fermer", "ghost")
        self.toggle = button("", "primary", "shield", "#FFFFFF")
        close.clicked.connect(self.accept)
        self.toggle.clicked.connect(self._toggle)
        self.buttons(close, self.toggle)
        self._refresh()

    def _refresh(self) -> None:
        if hasattr(self, "toggle"):
            self.toggle.setText("Retirer la vérification" if self.verified_value else "Les chiffres correspondent")
        self.state.setText("Contact vérifié ✓" if self.verified_value else "Non vérifié : vous communiquez avec la clé reçue lors de l'ajout.")
        self.state.setProperty("role", "ok" if self.verified_value else "muted")
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)

    def _toggle(self) -> None:
        self.verified_value = not self.verified_value
        self._refresh()


class RecoveryDialog(GlassDialog):
    def __init__(self, parent, code: str, first_time: bool = False):
        super().__init__(parent, "Code de récupération",
                         "C'est la SEULE façon de retrouver votre identité sur un autre ordinateur. "
                         "Personne, pas même LDC, ne peut le recréer. Notez-le hors ligne.", 560)
        panel = GlassPanel(radius=16, strong=True)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(20, 18, 20, 18)
        txt = label(code, "mono", wrap=True)
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        txt.setStyleSheet("font-size: 13pt; letter-spacing: 1px; line-height: 150%;")
        pl.addWidget(txt)
        self.body.addWidget(panel)
        cp = button("Copier", "", "copy")
        cp.clicked.connect(lambda: copy_text(code))
        self.body.addWidget(cp, 0, Qt.AlignLeft)
        self.ack = QCheckBox("J'ai noté ce code dans un endroit sûr")
        ok = button("Continuer" if first_time else "Fermer", "primary")
        if first_time:
            self.body.addWidget(self.ack)
            ok.setEnabled(False)
            self.ack.toggled.connect(ok.setEnabled)
        ok.clicked.connect(self.accept)
        self.buttons(ok)


class PasswordPrompt(GlassDialog):
    def __init__(self, parent, title: str, text: str, confirm_new: bool = False, verify=None, placeholder: str = "Mot de passe actuel", new_label: str = "Nouveau mot de passe (8 caractères minimum)"):
        super().__init__(parent, title, text, 440)
        self.verify, self.value, self.new_value = verify, "", ""
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText(placeholder)
        self.body.addWidget(self.pw)
        self.new1 = self.new2 = None
        if confirm_new:
            self.new1, self.new2 = QLineEdit(), QLineEdit()
            for w, ph in ((self.new1, new_label), (self.new2, "Confirmer le nouveau mot de passe")):
                w.setEchoMode(QLineEdit.Password)
                w.setPlaceholderText(ph)
                self.body.addWidget(w)
        self.err = label("", "error", wrap=True)
        self.body.addWidget(self.err)
        cancel, ok = button("Annuler", "ghost"), button("Confirmer", "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        self.pw.returnPressed.connect(self._ok)
        self.buttons(cancel, ok)

    def _ok(self) -> None:
        if self.new1 is not None:
            if len(self.new1.text()) < 8:
                self.err.setText("Le nouveau mot de passe doit contenir au moins 8 caractères.")
                return
            if self.new1.text() != self.new2.text():
                self.err.setText("Les deux nouveaux mots de passe sont différents.")
                return
            self.new_value = self.new1.text()
        if self.verify:
            try:
                self.verify(self.pw.text())
            except Exception as e:
                self.err.setText(str(e) or "Mot de passe incorrect")
                return
        self.value = self.pw.text()
        self.accept()


class TextPrompt(GlassDialog):
    def __init__(self, parent, title: str, text: str, initial: str = "", placeholder: str = ""):
        super().__init__(parent, title, text, 420)
        self.edit = QLineEdit(initial)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setMaxLength(40)
        self.body.addWidget(self.edit)
        cancel, ok = button("Annuler", "ghost"), button("Enregistrer", "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self.edit.returnPressed.connect(self.accept)
        self.buttons(cancel, ok)

    @property
    def value(self) -> str:
        return self.edit.text().strip()


class ConfirmDialog(GlassDialog):
    def __init__(self, parent, title: str, text: str, confirm: str = "Confirmer", danger: bool = False):
        super().__init__(parent, title, text, 440)
        cancel, ok = button("Annuler", "ghost"), button(confirm, "danger" if danger else "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        self.buttons(cancel, ok)


class DiagnosticsDialog(GlassDialog):
    """Diagnostic réseau lisible : ce qui fonctionne, ce qui ne fonctionne pas, et pourquoi."""

    def __init__(self, parent, engine):
        super().__init__(parent, "Diagnostic réseau", "État réel des connexions, sans jargon.", 600)
        self.engine = engine
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self.rows)
        self.body.addWidget(holder)
        retry = button("Reconnecter", "", "refresh")
        close = button("Fermer", "primary")
        retry.clicked.connect(self._retry)
        close.clicked.connect(self.accept)
        self.buttons(retry, close)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)

    def _retry(self) -> None:
        if self.engine.relay:
            self.engine.relay.reconnect_now()
        self.refresh()

    def _row(self, ok: bool | None, title: str, detail: str) -> QWidget:
        w = GlassPanel(radius=14)
        h = QHBoxLayout(w)
        h.setContentsMargins(14, 10, 14, 10)
        ico = QLabel()
        name, color = ("check", "#34D399") if ok else (("alert", "#F87171") if ok is False else ("info", "#A78BFA"))
        ico.setPixmap(icons.pixmap(name, color, 22))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(title, "h3"))
        col.addWidget(label(detail, "muted", wrap=True))
        h.addWidget(ico, 0, Qt.AlignTop)
        h.addLayout(col, 1)
        return w

    def refresh(self) -> None:
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        e = self.engine
        s = e.settings
        if not s.server_host:
            self.rows.addWidget(self._row(None, "Serveur de rendez-vous", "Aucun serveur configuré : seules les connexions directes sur le même réseau fonctionnent. Ajoutez un serveur dans Paramètres › Réseau."))
        elif e.relay and e.relay.connected:
            self.rows.addWidget(self._row(True, "Serveur de rendez-vous", f"Connecté à {s.server_host}:{s.server_port}" + (" (TLS)" if s.server_tls else " (sans TLS : métadonnées visibles sur le réseau)") + f". Votre adresse vue par le serveur : {e.relay.observed_ip}."))
        else:
            err = (e.relay.last_error if e.relay else None) or "en cours de connexion…"
            self.rows.addWidget(self._row(False, "Serveur de rendez-vous", f"Injoignable ({s.server_host}:{s.server_port}) : {err}. Vos messages sont conservés et partiront à la reconnexion."))
        if e.direct:
            self.rows.addWidget(self._row(True, "Connexions directes", f"À l'écoute sur le port {e.direct.port}. Fonctionne sur un même réseau local, en IPv6 ou avec une redirection de port."))
        else:
            self.rows.addWidget(self._row(None, "Connexions directes", "Désactivées : tout passe par le relais chiffré du serveur."))
        pending = e.db.conn.execute("SELECT COUNT(*) FROM outbox WHERE state='queued' AND kind='msg'").fetchone()[0]
        failed = e.db.conn.execute("SELECT COUNT(*) FROM outbox WHERE state='failed'").fetchone()[0]
        self.rows.addWidget(self._row(failed == 0, "Boîte d'envoi", f"{pending} message(s) en cours d'envoi, {failed} en échec."))
        for c in e.contacts({"accepted"}):
            mode = "direct" if e.is_direct(c["id"]) else ("via le relais" if e.server_online.get(c["id"]) else "hors ligne")
            self.rows.addWidget(self._row(mode != "hors ligne", c["alias"] or c["name"], f"Joignable : {mode}."))
