"""Premier lancement et déverrouillage : parcours guidé et honnête sur ce qui est protégé."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import paths, profile
from ..crypto.identity import InvalidPassword, InvalidRecoveryCode
from . import icons
from .dialogs import button, copy_text
from .widgets.glass import AuroraBackground, GlassPanel, Switch, label

WELCOME, CREATE, RECOVERY, SERVER, UNLOCK, RESTORE = range(6)


class OnboardingWindow(AuroraBackground):
    profile_ready = Signal(object, dict)   # Profile, config serveur

    def __init__(self):
        super().__init__()
        self.setWindowTitle("LDC P2P")
        self.setMinimumSize(880, 700)
        self._new: profile.Profile | None = None
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        self.card = GlassPanel(radius=28, strong=True)
        self.card.setFixedWidth(540)
        self.card.setMaximumHeight(640)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(40, 36, 40, 32)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        outer.addWidget(self.card)
        for builder in (self._welcome, self._create, self._recovery, self._server, self._unlock, self._restore):
            self.stack.addWidget(builder())
        self.reset()

    def reset(self) -> None:
        self._refresh_profiles()
        self.stack.setCurrentIndex(UNLOCK if paths.list_profiles() else WELCOME)

    # -- briques -------------------------------------------------------------------------
    def _title(self, lay: QVBoxLayout, title: str, sub: str = "", back_to: int | None = None) -> None:
        if back_to is not None:
            b = button("Retour", "ghost", "chevron-left")
            b.clicked.connect(lambda: self.stack.setCurrentIndex(back_to))
            lay.addWidget(b, 0, Qt.AlignLeft)
        lay.addWidget(label(title, "title"))
        if sub:
            s = label(sub, "muted", wrap=True)
            lay.addWidget(s)
        lay.addSpacing(8)

    def _pw(self, placeholder: str) -> QLineEdit:
        e = QLineEdit()
        e.setEchoMode(QLineEdit.Password)
        e.setPlaceholderText(placeholder)
        return e

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(12)
        return w, l

    # -- pages -----------------------------------------------------------------------------
    def _welcome(self) -> QWidget:
        w, l = self._page()
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(84))
        l.addWidget(logo, 0, Qt.AlignLeft)
        l.addSpacing(6)
        l.addWidget(label("Bienvenue sur LDC P2P", "title"))
        l.addWidget(label("Une messagerie privée, chiffrée de bout en bout. Votre identité est une clé cryptographique qui reste sur votre ordinateur : "
                          "pas d'e-mail, pas de compte à créer chez un tiers.", "muted", wrap=True))
        l.addSpacing(10)
        create = button("Créer mon identité", "primary", "key", "#FFFFFF")
        restore = button("Restaurer avec mon code de récupération", "", "refresh")
        create.clicked.connect(lambda: self.stack.setCurrentIndex(CREATE))
        restore.clicked.connect(lambda: self.stack.setCurrentIndex(RESTORE))
        for b in (create, restore):
            b.setMinimumHeight(44)
            l.addWidget(b)
        l.addStretch(1)
        return w

    def _create(self) -> QWidget:
        w, l = self._page()
        self._title(l, "Créer mon identité", "Choisissez un pseudo (visible par vos contacts) et un mot de passe qui protège votre clé sur cet ordinateur.", WELCOME)
        self.c_name = QLineEdit()
        self.c_name.setPlaceholderText("Pseudo")
        self.c_name.setMaxLength(40)
        self.c_pw1, self.c_pw2 = self._pw("Mot de passe (8 caractères minimum)"), self._pw("Confirmer le mot de passe")
        self.c_err = label("", "error", wrap=True)
        self.c_strength = label("", "faint")
        self.c_pw1.textChanged.connect(self._strength)
        go = button("Créer mon identité", "primary")
        go.setMinimumHeight(44)
        go.clicked.connect(self._do_create)
        self.c_pw2.returnPressed.connect(self._do_create)
        for x in (self.c_name, self.c_pw1, self.c_strength, self.c_pw2, self.c_err, go):
            l.addWidget(x)
        l.addStretch(1)
        return w

    def _strength(self, text: str) -> None:
        n = len(text)
        classes = sum(any(f(c) for c in text) for f in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum()))
        score = "faible" if n < 8 else ("correct" if n < 12 or classes < 2 else "solide")
        self.c_strength.setText(f"Robustesse : {score}" if text else "")

    def _do_create(self) -> None:
        name, p1, p2 = self.c_name.text().strip(), self.c_pw1.text(), self.c_pw2.text()
        if not name:
            self.c_err.setText("Choisissez un pseudo.")
        elif len(p1) < 8:
            self.c_err.setText("Le mot de passe doit contenir au moins 8 caractères.")
        elif p1 != p2:
            self.c_err.setText("Les deux mots de passe sont différents.")
        else:
            self.c_err.setText("")
            self.setCursor(Qt.WaitCursor)
            QGuiApplication.processEvents()
            try:
                self._new = profile.create_profile(name, p1)
            except ValueError as e:
                self.c_err.setText(str(e))
                return
            finally:
                self.unsetCursor()
            self.rec_text.setText(self._new.identity.recovery_code())
            self.c_pw1.clear()
            self.c_pw2.clear()
            self.stack.setCurrentIndex(RECOVERY)

    def _recovery(self) -> QWidget:
        w, l = self._page()
        self._title(l, "Votre code de récupération", "C'est la seule façon de retrouver votre identité si vous perdez cet ordinateur. "
                    "Personne, pas même LDC, ne peut le recréer. Notez-le sur papier.")
        panel = GlassPanel(radius=16)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(20, 18, 20, 18)
        self.rec_text = label("", "mono", wrap=True)
        self.rec_text.setStyleSheet("font-size: 13pt; letter-spacing: 1px;")
        self.rec_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl.addWidget(self.rec_text)
        l.addWidget(panel)
        cp = button("Copier le code", "", "copy")
        cp.clicked.connect(lambda: copy_text(self.rec_text.text()))
        l.addWidget(cp, 0, Qt.AlignLeft)
        self.rec_ack = QCheckBox("J'ai noté ce code dans un endroit sûr")
        l.addWidget(self.rec_ack)
        nxt = button("Continuer", "primary")
        nxt.setMinimumHeight(44)
        nxt.setEnabled(False)
        self.rec_ack.toggled.connect(nxt.setEnabled)
        nxt.clicked.connect(lambda: self.stack.setCurrentIndex(SERVER))
        l.addWidget(nxt)
        l.addStretch(1)
        return w

    def _server(self) -> QWidget:
        w, l = self._page()
        self._title(l, "Serveur de rendez-vous", "Pour joindre des contacts sur Internet, LDC P2P utilise un petit serveur qui met les gens en relation "
                    "et relaie des données chiffrées quand la connexion directe est impossible. Il ne peut pas lire vos messages. "
                    "Vous pouvez l'héberger vous-même (voir la documentation) ou le configurer plus tard.")
        self.s_host = QLineEdit()
        self.s_host.setPlaceholderText("Adresse du serveur, par ex. relais.exemple.fr (facultatif)")
        row = QHBoxLayout()
        self.s_port = QSpinBox()
        self.s_port.setRange(1, 65535)
        self.s_port.setValue(9443)
        self.s_tls = Switch(False)  # le serveur local par défaut n'a pas de TLS
        row.addWidget(label("Port"))
        row.addWidget(self.s_port)
        row.addStretch(1)
        row.addWidget(label("TLS"))
        row.addWidget(self.s_tls)
        l.addWidget(self.s_host)
        l.addLayout(row)
        l.addWidget(label("Sans serveur, seules les connexions directes sur le même réseau local fonctionnent.", "faint", wrap=True))
        self.s_token = QLineEdit()
        self.s_token.setEchoMode(QLineEdit.Password)
        self.s_token.setPlaceholderText("Jeton d'accès (seulement si le serveur est privé)")
        l.addWidget(self.s_token)
        test = button("Tester la connexion", "", "wifi")
        self.s_result = label("", "muted", wrap=True)
        test.clicked.connect(self._test_server)
        l.addWidget(test, 0, Qt.AlignLeft)
        l.addWidget(self.s_result)
        go = button("Terminer", "primary")
        go.setMinimumHeight(44)
        go.clicked.connect(self._finish_new)
        l.addWidget(go)
        l.addStretch(1)
        return w

    def _test_server(self) -> None:
        import asyncio
        from ..network.errors import probe_server
        self.s_result.setText("Test en cours…")
        self.s_result.setProperty("role", "muted")

        async def run() -> None:
            r = await probe_server(self.s_host.text().strip(), self.s_port.value(), self.s_tls.isChecked(), self.s_token.text())
            self.s_result.setText(("✓ " if r.ok else "✗ ") + r.message)
            self.s_result.setProperty("role", "ok" if r.ok else "error")
            self.s_result.style().unpolish(self.s_result)
            self.s_result.style().polish(self.s_result)
        asyncio.ensure_future(run())

    def _finish_new(self) -> None:
        cfg = {"host": self.s_host.text().strip(), "port": self.s_port.value(), "tls": self.s_tls.isChecked(), "token": self.s_token.text()}
        p, self._new = self._new, None
        if p:
            self.profile_ready.emit(p, cfg)

    def _unlock(self) -> QWidget:
        w, l = self._page()
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(64))
        l.addWidget(logo, 0, Qt.AlignLeft)
        l.addWidget(label("Content de vous revoir", "title"))
        self.u_combo = QComboBox()
        self.u_pw = self._pw("Mot de passe")
        self.u_err = label("", "error", wrap=True)
        go = button("Déverrouiller", "primary", "lock", "#FFFFFF")
        go.setMinimumHeight(44)
        go.clicked.connect(self._do_unlock)
        self.u_pw.returnPressed.connect(self._do_unlock)
        for x in (self.u_combo, self.u_pw, self.u_err, go):
            l.addWidget(x)
        row = QHBoxLayout()
        a, b = button("Nouvelle identité", "ghost"), button("Restaurer", "ghost")
        a.clicked.connect(lambda: self.stack.setCurrentIndex(CREATE))
        b.clicked.connect(lambda: self.stack.setCurrentIndex(RESTORE))
        row.addWidget(a)
        row.addWidget(b)
        l.addLayout(row)
        l.addStretch(1)
        return w

    def _refresh_profiles(self) -> None:
        self.u_combo.clear()
        for pid in paths.list_profiles():
            self.u_combo.addItem(f"{profile.profile_label(pid)}   ·   …{pid[-11:]}", pid)

    def _do_unlock(self) -> None:
        pid = self.u_combo.currentData()
        if not pid:
            return
        self.setCursor(Qt.WaitCursor)
        QGuiApplication.processEvents()
        try:
            p = profile.unlock_profile(pid, self.u_pw.text())
        except InvalidPassword as e:
            self.u_err.setText(str(e))
            return
        except Exception as e:  # base corrompue, version trop récente, etc.
            self.u_err.setText(f"Impossible d'ouvrir ce profil : {e}")
            return
        finally:
            self.unsetCursor()
        self.u_pw.clear()
        self.u_err.setText("")
        self.profile_ready.emit(p, {})

    def _restore(self) -> QWidget:
        w, l = self._page()
        self._title(l, "Restaurer mon identité", "Saisissez le code de récupération noté à la création. Vous retrouverez le même identifiant : vos contacts vous reconnaîtront.", None)
        back = button("Retour", "ghost", "chevron-left")
        back.clicked.connect(lambda: self.reset())
        l.insertWidget(0, back, 0, Qt.AlignLeft)
        self.r_code = QPlainTextEdit()
        self.r_code.setPlaceholderText("XXXXX-XXXXX-XXXXX-…")
        self.r_code.setFixedHeight(84)
        self.r_name = QLineEdit()
        self.r_name.setPlaceholderText("Pseudo")
        self.r_pw1, self.r_pw2 = self._pw("Nouveau mot de passe pour cet ordinateur"), self._pw("Confirmer le mot de passe")
        self.r_err = label("", "error", wrap=True)
        go = button("Restaurer", "primary")
        go.setMinimumHeight(44)
        go.clicked.connect(self._do_restore)
        for x in (self.r_code, self.r_name, self.r_pw1, self.r_pw2, self.r_err, go):
            l.addWidget(x)
        l.addStretch(1)
        return w

    def _do_restore(self) -> None:
        name, p1, p2 = self.r_name.text().strip() or "Anonyme", self.r_pw1.text(), self.r_pw2.text()
        if len(p1) < 8 or p1 != p2:
            self.r_err.setText("Mot de passe trop court ou différent de sa confirmation.")
            return
        self.setCursor(Qt.WaitCursor)
        QGuiApplication.processEvents()
        try:
            p = profile.restore_from_recovery_code(self.r_code.toPlainText(), name, p1)
        except (InvalidRecoveryCode, ValueError) as e:
            self.r_err.setText(str(e))
            return
        finally:
            self.unsetCursor()
        self._new = p
        self.rec_text.setText(p.identity.recovery_code())
        self.r_code.clear()
        self.r_pw1.clear()
        self.r_pw2.clear()
        self.stack.setCurrentIndex(SERVER)
