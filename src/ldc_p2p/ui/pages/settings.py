"""Page « Paramètres » organisée par catégories."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ... import __version__
from ...crypto.identity import InvalidPassword, Keystore
from ...profile import Profile, export_backup, import_backup
from ..controller import UiController
from ..dialogs import (ConfirmDialog, DiagnosticsDialog, PasswordPrompt, RecoveryDialog, TextPrompt, button, copy_text)
from ..widgets.glass import GlassPanel, Switch, label


class SettingsPage(QWidget):
    logout_requested = Signal(bool)   # True = supprimer le profil
    status_message = Signal(str, str)

    def __init__(self, ctrl: UiController, profile: Profile, parent=None):
        super().__init__(parent)
        self.ctrl, self.engine, self.profile = ctrl, ctrl.engine, profile
        self.ks_path = profile.directory / "keystore.json"
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = GlassPanel(radius=24)
        root.addWidget(panel)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(28, 24, 14, 16)
        pl.addWidget(label("Paramètres", "h2"))
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self.col = QVBoxLayout(holder)
        self.col.setContentsMargins(0, 8, 14, 20)
        self.col.setSpacing(14)
        sa.setWidget(holder)
        pl.addWidget(sa, 1)
        self._account()
        self._network()
        self._privacy()
        self._notifications()
        self._files()
        self._updates()
        self._about()
        self.col.addStretch(1)

    # -- helpers ------------------------------------------------------------------------
    def _card(self, title: str) -> QVBoxLayout:
        self.col.addWidget(label(title.upper(), "section"))
        c = GlassPanel(radius=18)
        l = QVBoxLayout(c)
        l.setContentsMargins(20, 14, 20, 14)
        l.setSpacing(12)
        self.col.addWidget(c)
        return l

    def _row(self, lay: QVBoxLayout, title: str, desc: str, widget: QWidget | None = None) -> QHBoxLayout:
        r = QHBoxLayout()
        r.setSpacing(16)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        txt.addWidget(label(title))
        if desc:
            txt.addWidget(label(desc, "faint", wrap=True))
        r.addLayout(txt, 1)
        if widget is not None:
            r.addWidget(widget, 0, Qt.AlignVCenter)
        lay.addLayout(r)
        return r

    def _switch(self, lay, title: str, desc: str, attr: str, after=None) -> Switch:
        sw = Switch(bool(getattr(self.engine.settings, attr)))

        def changed(v: bool) -> None:
            setattr(self.engine.settings, attr, v)
            self.engine.settings.save(self.engine.db)
            if after:
                after(v)
        sw.toggled.connect(changed)
        self._row(lay, title, desc, sw)
        return sw

    def _verify_pw(self, pw: str) -> None:
        Keystore.unlock(self.ks_path, pw)

    # -- sections -----------------------------------------------------------------------
    def _account(self) -> None:
        e = self.engine
        L = self._card("Compte et identité")
        name = label(e.identity.name, "h3")
        self.name_lbl = name
        self._row(L, "Pseudo", "Visible par vos contacts.", name)
        rn = button("Modifier", "")
        rn.clicked.connect(self._rename)
        L.itemAt(L.count() - 1).layout().addWidget(rn)
        idl = label(e.me, "mono")
        idl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cp = button("Copier", "", "copy")
        cp.clicked.connect(lambda: (copy_text(e.me), self.status_message.emit("Identifiant copié", "ok")))
        self._row(L, "Identifiant LDC", "Dérivé de vos clés : impossible à usurper.", cp)
        L.addWidget(idl)
        card = button("Copier mon code d'invitation", "", "link")
        card.clicked.connect(lambda: (copy_text(e.my_card_code()), self.status_message.emit("Code d'invitation copié", "ok")))
        self._row(L, "Code d'invitation", "À donner à un ami pour qu'il vous ajoute. Ne contient ni IP ni mot de passe.", card)
        rec = button("Afficher", "", "key")
        rec.clicked.connect(self._show_recovery)
        self._row(L, "Code de récupération", "Permet de retrouver votre identité sur un autre ordinateur.", rec)
        pw = button("Changer", "")
        pw.clicked.connect(self._change_password)
        self._row(L, "Mot de passe", "Protège votre clé privée sur cet ordinateur.", pw)
        ex, im = button("Exporter", "", "download"), button("Importer", "")
        ex.clicked.connect(self._export)
        im.clicked.connect(self._import)
        r = self._row(L, "Sauvegarde chiffrée", "Contacts, groupes et réglages (pas l'historique des messages).", ex)
        r.addWidget(im)
        out = button("Verrouiller / changer de profil", "", "lock")
        out.clicked.connect(lambda: self.logout_requested.emit(False))
        dl = button("Supprimer ce profil", "danger", "trash", "#FCA5A5")
        dl.clicked.connect(self._delete_profile)
        r2 = self._row(L, "Session", "", out)
        r2.addWidget(dl)

    def _network(self) -> None:
        s = self.engine.settings
        L = self._card("Réseau")
        host = QLineEdit(s.server_host)
        host.setPlaceholderText("relais.exemple.fr")
        host.setMinimumWidth(240)
        port = QSpinBox()
        port.setRange(1, 65535)
        port.setValue(s.server_port)
        tls = Switch(s.server_tls)
        apply = button("Appliquer", "primary")

        def do_apply() -> None:
            s.server_host, s.server_port, s.server_tls = host.text().strip(), port.value(), tls.isChecked()
            self.engine.apply_server_settings()
            self.status_message.emit("Serveur mis à jour — reconnexion…", "ok")
        apply.clicked.connect(do_apply)
        self._row(L, "Serveur de rendez-vous", "Sert à retrouver vos contacts et de relais chiffré si la connexion directe échoue. Il ne peut pas lire vos messages.", host)
        r = self._row(L, "Port", "", port)
        self._row(L, "Chiffrer la connexion au serveur (TLS)", "Recommandé : masque vos métadonnées aux observateurs du réseau.", tls)
        L.addWidget(apply, 0, Qt.AlignRight)
        self._switch(L, "Connexions directes", "Pair à pair sur réseau local, IPv6 ou port redirigé. Appliqué au prochain démarrage.", "direct_enabled")
        diag = button("Ouvrir le diagnostic", "", "wifi")
        diag.clicked.connect(lambda: DiagnosticsDialog(self, self.engine).exec())
        self._row(L, "Diagnostic réseau", "Voir pourquoi un contact est ou n'est pas joignable.", diag)

    def _privacy(self) -> None:
        L = self._card("Confidentialité")
        self._switch(L, "Accusés de lecture", "Informer vos contacts que vous avez lu leurs messages.", "read_receipts")
        self._switch(L, "Indicateur « écrit… »", "Montrer quand vous êtes en train d'écrire.", "typing_indicators")
        blocked = [c for c in self.engine.contacts({"blocked"})]
        self._row(L, "Contacts bloqués", f"{len(blocked)} contact(s). Gérez-les depuis l'onglet Contacts.")

    def _notifications(self) -> None:
        L = self._card("Notifications")
        self._switch(L, "Notifications Windows", "Afficher une notification à la réception d'un message.", "notifications")
        self._switch(L, "Afficher un aperçu du message", "Désactivé par défaut : une notification peut être vue par quelqu'un qui regarde votre écran.", "notification_previews")
        self._switch(L, "Réduire dans la zone de notification à la fermeture", "Continue de recevoir vos messages en arrière-plan.", "close_to_tray")

    def _files(self) -> None:
        L = self._card("Fichiers")
        e = self.engine
        path = label(str(e.download_dir), "faint")
        ch = button("Changer", "", "folder")

        def pick() -> None:
            d = QFileDialog.getExistingDirectory(self, "Dossier de téléchargement", str(e.download_dir))
            if d:
                e.download_dir = Path(d)
                e.settings.download_dir = d
                e.settings.save(e.db)
                path.setText(d)
        ch.clicked.connect(pick)
        self._row(L, "Dossier de réception", "", ch)
        L.addWidget(path)
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix(" Mo")
        spin.setSpecialValueText("Toujours demander")
        spin.setValue(e.settings.auto_accept_mb)

        def chg(v: int) -> None:
            e.settings.auto_accept_mb = v
            e.settings.save(e.db)
        spin.valueChanged.connect(chg)
        self._row(L, "Accepter automatiquement jusqu'à", "Les fichiers exécutables demandent toujours une confirmation.", spin)

    def _updates(self) -> None:
        from ... import release_key
        L = self._card("Mises à jour")
        url = QLineEdit(self.engine.settings.update_url)
        url.setPlaceholderText("https://… (adresse des mises à jour, fournie par l'éditeur)")
        url.setMinimumWidth(300)

        def save() -> None:
            self.engine.settings.update_url = url.text().strip()
            self.engine.settings.save(self.engine.db)
        url.editingFinished.connect(save)
        btn = button("Rechercher", "", "refresh")
        configured = bool(release_key.RELEASE_PUBLIC_KEY_HEX)
        btn.setEnabled(configured)
        btn.clicked.connect(lambda: (save(), self._check_update()))
        self._row(L, "Source des mises à jour", "Les mises à jour sont signées : l'application refuse tout fichier dont la signature ou l'empreinte ne correspond pas." if configured
                  else "Désactivé dans cette version : aucune clé de publication n'est intégrée (voir docs/DISTRIBUTION.md).", url)
        self._row(L, "Version installée", __version__, btn)

    def _check_update(self) -> None:
        import asyncio
        from ... import release_key, updater
        s = self.engine.settings
        if not s.update_url:
            self.status_message.emit("Renseignez d'abord l'adresse des mises à jour.", "error")
            return

        async def run() -> None:
            loop = asyncio.get_running_loop()
            try:
                up = await loop.run_in_executor(None, updater.check_for_update, s.update_url, __version__, bytes.fromhex(release_key.RELEASE_PUBLIC_KEY_HEX))
            except Exception as e:
                self.status_message.emit(f"Vérification impossible : {e}", "error")
                return
            if up is None:
                self.status_message.emit("Vous utilisez la dernière version.", "ok")
                return
            if not ConfirmDialog(self, f"Version {up.version} disponible", (up.notes or "Une nouvelle version est disponible.") + "\n\nLe fichier sera vérifié (signature et empreinte) avant toute installation.", "Télécharger").exec():
                return
            try:
                out = await loop.run_in_executor(None, updater.download_update, up, self.profile.directory.parent.parent / "updates")
            except Exception as e:
                self.status_message.emit(f"Téléchargement refusé : {e}", "error")
                return
            self.status_message.emit(f"Téléchargé et vérifié : {out.name}", "ok")
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out.parent)))
        asyncio.ensure_future(run())

    def _about(self) -> None:
        L = self._card("À propos")
        self._row(L, f"LDC P2P {__version__}", "Messagerie pair à pair hybride, chiffrement de bout en bout.")
        L.addWidget(label(
            "Sécurité : chiffrement NaCl (X25519 + XSalsa20-Poly1305) et signatures Ed25519, clé privée protégée par scrypt. "
            "Limites connues : pas de confidentialité persistante (forward secrecy), protocole non audité, une seule session active par identité. "
            "Ne confiez pas de secrets critiques à une version préliminaire.", "faint", wrap=True))

    # -- actions ----------------------------------------------------------------------------
    def _rename(self) -> None:
        d = TextPrompt(self, "Changer de pseudo", "Vos contacts verront le nouveau pseudo à leur prochaine invitation.", self.engine.identity.name)
        if not d.exec() or not d.value:
            return
        p = PasswordPrompt(self, "Confirmer", "Saisissez votre mot de passe pour enregistrer le nouveau pseudo.", verify=self._verify_pw)
        if p.exec():
            from ...crypto.identity import Identity
            ident = Identity(self.engine.identity.seed, d.value)
            Keystore.create(self.ks_path, ident, p.value)
            from ...profile import write_hint
            write_hint(self.profile.directory, ident.name)
            self.engine.identity.name = ident.name
            self.name_lbl.setText(ident.name)
            self.status_message.emit("Pseudo modifié", "ok")

    def _show_recovery(self) -> None:
        p = PasswordPrompt(self, "Afficher le code de récupération", "Par sécurité, saisissez votre mot de passe.", verify=self._verify_pw)
        if p.exec():
            RecoveryDialog(self, self.engine.identity.recovery_code()).exec()

    def _change_password(self) -> None:
        p = PasswordPrompt(self, "Changer le mot de passe", "", confirm_new=True, verify=self._verify_pw)
        if p.exec():
            Keystore.change_password(self.ks_path, p.value, p.new_value)
            self.status_message.emit("Mot de passe modifié", "ok")

    def _export(self) -> None:
        p = PasswordPrompt(self, "Exporter une sauvegarde", "Confirmez votre mot de passe, puis choisissez une phrase secrète pour chiffrer le fichier.",
                           confirm_new=True, verify=self._verify_pw, new_label="Phrase secrète de la sauvegarde (8 caractères minimum)")
        if not p.exec():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer la sauvegarde", "ldc-sauvegarde.ldcbackup", "Sauvegarde LDC (*.ldcbackup)")
        if path:
            export_backup(self.engine.db, self.engine.identity, Path(path), p.new_value)
            self.status_message.emit("Sauvegarde enregistrée", "ok")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choisir une sauvegarde", "", "Sauvegarde LDC (*.ldcbackup)")
        if not path:
            return
        p = PasswordPrompt(self, "Importer une sauvegarde", "Saisissez la phrase secrète de ce fichier.", placeholder="Phrase secrète")
        if p.exec():
            try:
                res = import_backup(self.engine.db, self.engine.identity, Path(path), p.value)
            except InvalidPassword as ex:
                self.status_message.emit(str(ex), "error")
                return
            self.engine.events.emit("contact_update", id=None)
            self.status_message.emit(f"{res['contacts']} contact(s) et {res['groups']} groupe(s) restaurés", "ok")

    def _delete_profile(self) -> None:
        if not ConfirmDialog(self, "Supprimer ce profil ?", "Votre identité, vos contacts et tous vos messages seront effacés de cet ordinateur. "
                             "Sans votre code de récupération, c'est irréversible.", "Continuer", True).exec():
            return
        p = PasswordPrompt(self, "Dernière confirmation", "Saisissez votre mot de passe pour supprimer définitivement ce profil.", verify=self._verify_pw)
        if p.exec():
            self.logout_requested.emit(True)
