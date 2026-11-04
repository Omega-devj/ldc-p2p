"""Page « Paramètres » organisée par catégories."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ... import __version__
from ...crypto.identity import InvalidPassword, Keystore
from ...profile import Profile, export_backup, import_backup
from ..controller import UiController
from ..dialogs import (ConfirmDialog, DiagnosticsDialog, PasswordPrompt, RecoveryDialog, TextPrompt, button, copy_text)
from .. import theme
from ..widgets.glass import GlassPanel, Switch, label


class SettingsPage(QWidget):
    logout_requested = Signal(bool)   # True = supprimer le profil
    theme_changed = Signal(str, str)
    security_changed = Signal()
    status_message = Signal(str, str)

    def __init__(self, ctrl: UiController, profile: Profile, parent=None):
        super().__init__(parent)
        self.ctrl, self.engine, self.profile = ctrl, ctrl.engine, profile
        self.ks_path = profile.directory / "keystore.json"
        self.updates = None
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
        self._appearance()
        self._network()
        self._security()
        self._privacy()
        self._notifications()
        self._files()
        self._windows()
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
        r = self._row(L, "Sauvegarde chiffrée", "Contacts, groupes et réglages.", ex)
        r.addWidget(im)
        hx = button("Exporter l'historique", "", "download")
        hx.clicked.connect(self._export_history)
        self._row(L, "Historique des messages", "Fichier chiffré par une phrase secrète (texte et noms de fichiers).", hx)
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
        token = QLineEdit(s.server_token)
        token.setEchoMode(QLineEdit.Password)
        token.setPlaceholderText("seulement si le serveur est privé")
        token.setMinimumWidth(240)
        apply = button("Appliquer", "primary")
        test = button("Tester la connexion", "", "wifi")
        dirty = label("", "faint")
        result = label("", "muted", wrap=True)

        def current() -> tuple:
            return host.text().strip(), port.value(), tls.isChecked(), token.text()

        def saved() -> tuple:
            return s.server_host, s.server_port, s.server_tls, s.server_token

        def refresh_dirty(*_) -> None:
            d = current() != saved()
            dirty.setText("● Modifications non enregistrées — cliquez sur Appliquer" if d else "")
            dirty.setStyleSheet("color: #FBBF24; font-weight: 600;")
            apply.setEnabled(d)

        def show(ok: bool | None, text: str) -> None:
            result.setText(text)
            result.setProperty("role", "muted" if ok is None else ("ok" if ok else "error"))
            result.style().unpolish(result)
            result.style().polish(result)

        def do_apply() -> None:
            s.server_host, s.server_port, s.server_tls, s.server_token = current()
            self.engine.apply_server_settings()
            refresh_dirty()
            show(None, "Reconnexion en cours…")
            self.status_message.emit("Réglages du serveur enregistrés — reconnexion…", "ok")

        def do_test() -> None:
            from ...network.errors import probe_server
            show(None, "Test en cours…")
            h, p_, t, tok = current()

            async def run() -> None:
                r = await probe_server(h, p_, t, tok)
                show(r.ok, ("✓ " if r.ok else "✗ ") + r.message)
            asyncio.ensure_future(run())

        for w in (host, token):
            w.textChanged.connect(refresh_dirty)
        port.valueChanged.connect(refresh_dirty)
        tls.toggled.connect(refresh_dirty)
        apply.clicked.connect(do_apply)
        test.clicked.connect(do_test)
        self._row(L, "Serveur de rendez-vous", "Sert à retrouver vos contacts et de relais chiffré si la connexion directe échoue. Il ne peut pas lire vos messages.", host)
        self._row(L, "Port", "", port)
        self._row(L, "Chiffrer la connexion au serveur (TLS)", "Recommandé sur Internet. Désactivez-le pour un serveur local lancé sans certificat.", tls)
        self._row(L, "Jeton d'accès", "Demandé par les serveurs privés.", token)
        r = QHBoxLayout()
        r.addWidget(dirty, 1)
        r.addWidget(test)
        r.addWidget(apply)
        L.addLayout(r)
        L.addWidget(result)
        refresh_dirty()
        sw = Switch(bool(s.direct_enabled))
        sw.toggled.connect(lambda v: self.ctrl.run(self.engine.set_direct_enabled(v)))
        self._row(L, "Connexions directes", "Pair à pair sur réseau local, IPv6 ou port redirigé. Appliqué immédiatement.", sw)
        diag = button("Ouvrir le diagnostic", "", "wifi")
        diag.clicked.connect(lambda: DiagnosticsDialog(self, self.engine).exec())
        self._row(L, "Diagnostic réseau", "Voir pourquoi un contact est ou n'est pas joignable.", diag)

    def _appearance(self) -> None:
        from PySide6.QtWidgets import QComboBox
        from .. import prefs, sounds
        L = self._card("Apparence et sons")
        mode = QComboBox()
        for txt, val in (("Sombre (Liquid Glass)", "sombre"), ("Clair", "clair")):
            mode.addItem(txt, val)
        cur_mode, cur_acc = theme.current()
        mode.setCurrentIndex(max(0, mode.findData(cur_mode)))
        acc = QComboBox()
        for name in theme.ACCENTS:
            acc.addItem(name.capitalize(), name)
        acc.setCurrentIndex(max(0, acc.findData(cur_acc)))

        def changed(*_) -> None:
            prefs.save(mode.currentData(), acc.currentData())
            self.theme_changed.emit(mode.currentData(), acc.currentData())
        mode.activated.connect(changed)
        acc.activated.connect(changed)
        self._row(L, "Thème", "S'applique immédiatement.", mode)
        self._row(L, "Couleur d'accent", "", acc)
        if sys.platform == "win32":
            self._switch(L, "Flou Windows 11 (expérimental)", "Effet verre réel derrière la fenêtre. Redémarrage conseillé.", "backdrop")
        snd = QComboBox()
        for k, v in sounds.LABELS.items():
            snd.addItem(v, k)
        snd.setCurrentIndex(max(0, snd.findData(self.engine.settings.sound_name)))

        def pick(i: int) -> None:
            self.engine.settings.sound_name = snd.itemData(i)
            self.engine.settings.save(self.engine.db)
            sounds.play(snd.itemData(i))
        snd.activated.connect(pick)
        self._switch(L, "Son de notification", "Joué à la réception d'un message (sauf « Ne pas déranger » et conversations muettes).", "sound")
        self._row(L, "Sonnerie", "", snd)

    def _security(self) -> None:
        from PySide6.QtWidgets import QComboBox
        L = self._card("Sécurité de l'application")
        lock = QComboBox()
        for txt, v in (("Jamais", 0), ("1 minute", 1), ("5 minutes", 5), ("15 minutes", 15), ("30 minutes", 30), ("1 heure", 60)):
            lock.addItem(txt, v)
        lock.setCurrentIndex(max(0, lock.findData(self.engine.settings.lock_minutes)))

        def chg(i: int) -> None:
            self.engine.settings.lock_minutes = lock.itemData(i)
            self.engine.settings.save(self.engine.db)
            self.security_changed.emit()
        lock.currentIndexChanged.connect(chg)
        self._row(L, "Verrouillage automatique", "Après inactivité, l'écran est masqué jusqu'à la saisie du mot de passe (Ctrl+L pour verrouiller tout de suite). Les messages continuent d'arriver.", lock)
        away = QComboBox()
        for txt, v in (("Jamais", 0), ("5 minutes", 5), ("10 minutes", 10), ("30 minutes", 30)):
            away.addItem(txt, v)
        away.setCurrentIndex(max(0, away.findData(self.engine.settings.auto_away_minutes)))

        def chg2(i: int) -> None:
            self.engine.settings.auto_away_minutes = away.itemData(i)
            self.engine.settings.save(self.engine.db)
            self.security_changed.emit()
        away.currentIndexChanged.connect(chg2)
        self._row(L, "Passer en « Absent » après", "Revient en « En ligne » dès que vous reprenez la main.", away)
        if sys.platform == "win32":
            self._switch(L, "Protéger contre les captures d'écran", "La fenêtre apparaît noire dans les captures et partages d'écran (Windows 10 2004+).", "capture_protection",
                         after=lambda v: self.security_changed.emit())

    def _windows(self) -> None:
        from .. import winint
        if not winint.IS_WIN:
            return
        L = self._card("Windows")
        sw = Switch(winint.autostart_enabled())
        sw.toggled.connect(lambda v: self.status_message.emit("Démarrage automatique " + ("activé" if v else "désactivé"), "ok") if winint.set_autostart(v) else self.status_message.emit("Impossible de modifier le démarrage automatique", "error"))
        self._row(L, "Lancer LDC P2P au démarrage de Windows", "", sw)
        reg = button("Associer les liens ldc://" if not winint.protocol_registered() else "Liens ldc:// associés ✓", "")
        reg.clicked.connect(lambda: self.status_message.emit("Liens ldc:// associés", "ok") if winint.register_protocol() else self.status_message.emit("Association impossible", "error"))
        self._row(L, "Liens d'invitation", "Un lien ldc://add/… ouvre directement l'ajout de contact.", reg)

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
        from PySide6.QtWidgets import QComboBox
        from ... import updater
        L = self._card("Mises à jour")
        e = self.engine
        combo = QComboBox()
        for txt, val in (("Me prévenir", 1), ("Installer automatiquement", 2), ("Jamais", 0)):
            combo.addItem(txt, val)
        combo.setCurrentIndex(max(0, combo.findData(e.settings.auto_update)))

        def chg(i: int) -> None:
            e.settings.auto_update = combo.itemData(i)
            e.settings.save(e.db)
        combo.currentIndexChanged.connect(chg)
        self._row(L, "Quand une nouvelle version est publiée", "Vérifié au démarrage puis toutes les 6 heures. Les mises à jour sont signées : "
                  "tout fichier dont la signature ou l'empreinte ne correspond pas est refusé.", combo)
        btn = button("Rechercher maintenant", "primary", "refresh", "#FFFFFF")
        btn.clicked.connect(lambda: self.updates and asyncio.ensure_future(self.updates.check(manual=True)))
        self._row(L, f"Version installée : {updater.current_version()}", f"Source : {e.settings.update_url}", btn)

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

    def _export_history(self) -> None:
        from ...profile import export_history
        p = PasswordPrompt(self, "Exporter l'historique", "Confirmez votre mot de passe, puis choisissez une phrase secrète pour chiffrer le fichier.",
                           confirm_new=True, verify=self._verify_pw, new_label="Phrase secrète de l'export (8 caractères minimum)")
        if not p.exec():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer l'historique", "ldc-historique.ldchistory", "Historique LDC (*.ldchistory)")
        if path:
            names = {c["id"]: (c["alias"] or c["name"]) for c in self.engine.contacts()}
            n = export_history(self.engine.db, self.engine.identity, Path(path), p.new_value, names)
            self.status_message.emit(f"{n} message(s) exportés (chiffrés)", "ok")

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
