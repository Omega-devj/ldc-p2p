"""Fenêtre principale : navigation latérale + pages + notifications système."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLineEdit, QMenu, QPushButton, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from ..engine.core import ChatEngine
from ..profile import Profile
from . import icons, theme
from .controller import UiController
import asyncio
from ..crypto.identity import InvalidPassword, Keystore
from . import sounds, winint
from .activity import ActivityMonitor
from .update_manager import UpdateManager
from .call_window import CallWindow, IncomingCallPopup
from .call_setup import install_call_components
from .dialogs import AddContactDialog, ConfirmDialog, DiagnosticsDialog, NewGroupDialog, button
from .pages.chats import ChatsPage
from .pages.contacts import ContactsPage
from .pages.settings import SettingsPage
from .widgets.glass import AuroraBackground, Avatar, GlassPanel, PresenceDot, Toast, label

PAGE_CHATS, PAGE_CONTACTS, PAGE_SETTINGS = range(3)


class NavButton(QPushButton):
    def __init__(self, icon_name: str, tip: str):
        super().__init__()
        self.setProperty("kind", "nav")
        self.setCheckable(True)
        self.setToolTip(tip)
        self.setFixedSize(52, 52)
        self.setIconSize(QSize(24, 24))
        self.icon_name = icon_name
        self.badge = 0
        self.setCursor(Qt.PointingHandCursor)
        self._sync()
        self.toggled.connect(lambda _: self._sync())

    def _sync(self) -> None:
        self.setIcon(icons.icon(self.icon_name, theme.accent_hex(3 if not theme.is_light() else 2) if self.isChecked() else theme.icon_hex(0), 24, 1.9))

    def set_badge(self, n: int) -> None:
        self.badge = n
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self.badge:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            txt = str(min(self.badge, 99))
            w = 20 if len(txt) == 1 else 26
            r = QRectF(self.width() - w - 2, 2, w, 18)
            p.setPen(QColor("#0B0A14"))
            p.setBrush(QColor("#F87171"))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor("white"))
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, txt)


def apply_windows_backdrop(widget: QWidget) -> bool:
    """Flou système Windows 11 (Mica/Acrylique). Expérimental : sans effet hors Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(3)  # 2 = Mica, 3 = Acrylique, 4 = Mica Alt
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
        dark = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        return True
    except Exception:
        return False


class LockOverlay(AuroraBackground):
    """Écran de verrouillage : masque tout le contenu jusqu'à la saisie du mot de passe du profil."""
    unlocked = Signal()

    def __init__(self, parent: QWidget, keystore_path):
        super().__init__(parent)
        self.ks = keystore_path
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        card = GlassPanel(radius=26, strong=True)
        card.setFixedWidth(400)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(34, 30, 34, 28)
        cl.setSpacing(12)
        logo = label("")
        logo.setPixmap(icons.logo_pixmap(64))
        logo.setAlignment(Qt.AlignCenter)
        cl.addWidget(logo)
        t = label("LDC P2P est verrouillé", "h2")
        t.setAlignment(Qt.AlignCenter)
        cl.addWidget(t)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Mot de passe")
        self.pw.returnPressed.connect(self._try)
        cl.addWidget(self.pw)
        self.err = label("", "error")
        self.err.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.err)
        go = button("Déverrouiller", "primary", "lock", "#FFFFFF")
        go.clicked.connect(self._try)
        cl.addWidget(go)
        lay.addWidget(card)
        self.hide()

    def lock(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.pw.clear()
        self.err.setText("")
        self.show()
        self.raise_()
        self.pw.setFocus()

    def _try(self) -> None:
        try:
            Keystore.unlock(self.ks, self.pw.text())
        except InvalidPassword:
            self.err.setText("Mot de passe incorrect")
            self.pw.selectAll()
            return
        self.hide()
        self.unlocked.emit()


class MainWindow(QWidget):
    logout = Signal(bool)
    closed = Signal()
    theme_changed = Signal(str, str)

    def __init__(self, profile: Profile, ctrl: UiController, tray_icon: QIcon | None = None):
        super().__init__()
        self.profile, self.ctrl, self.engine = profile, ctrl, ctrl.engine
        self.setWindowTitle("LDC P2P")
        self.setMinimumSize(1080, 700)
        self.resize(1320, 840)
        self.setWindowIcon(QIcon(icons.logo_pixmap(64)))
        self.use_backdrop = self.engine.settings.backdrop and sys.platform == "win32"
        if self.use_backdrop:
            self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.bg = AuroraBackground(opaque=not self.use_backdrop)
        outer.addWidget(self.bg)
        root = QHBoxLayout(self.bg)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addWidget(self._build_rail())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.chats = ChatsPage(ctrl)
        self.contacts = ContactsPage(ctrl)
        self.settings = SettingsPage(ctrl, profile)
        for p in (self.chats, self.contacts, self.settings):
            self.stack.addWidget(p)
        self.toast = Toast(self)
        self.lock_overlay = LockOverlay(self, profile.directory / "keystore.json")
        self.updates = UpdateManager(self.engine, self)
        self.settings.updates = self.updates
        self.update_bar = self._build_update_bar(root)
        self._auto_away_active = False
        self.activity = ActivityMonitor(self.engine.settings.lock_minutes, self.engine.settings.auto_away_minutes, self)
        self.activity.idle_lock.connect(self.lock_now)
        self.activity.idle_away.connect(self._auto_away)
        self.activity.back.connect(self._auto_back)
        self.call_win: CallWindow | None = None
        self.incoming: IncomingCallPopup | None = None
        self.ctrl.call_state.connect(self._on_call_state)
        self.ctrl.call_quality.connect(lambda call, st: self.call_win and self.call_win.set_quality(call.quality))
        self.ctrl.missed_call.connect(lambda peer: self.toast.show_message(f"Appel manqué de {self.engine.display_name(peer)}", "error", 6000))
        self.chats.call_requested.connect(self.request_call)
        self._shortcuts()
        self._wire()
        self._setup_tray(tray_icon)
        self.goto(PAGE_CHATS)
        self.update_badges()
        self._last_notified_conv: str | None = None
        self._set_conn(self.engine.relay.state if self.engine.relay else "none")

    # -- construction ----------------------------------------------------------------------------
    def _build_rail(self) -> QWidget:
        rail = GlassPanel(radius=26)
        rail.setFixedWidth(76)
        l = QVBoxLayout(rail)
        l.setContentsMargins(12, 16, 12, 14)
        l.setSpacing(8)
        logo = QPushButton()
        logo.setProperty("kind", "icon")
        logo.setIcon(QIcon(icons.logo_pixmap(40)))
        logo.setIconSize(QSize(40, 40))
        logo.setFixedSize(52, 52)
        logo.setToolTip("LDC P2P")
        l.addWidget(logo)
        l.addSpacing(10)
        self.nav = [NavButton("chat", "Messages"), NavButton("users", "Contacts"), NavButton("sliders", "Paramètres")]
        for i, b in enumerate(self.nav[:2]):
            b.clicked.connect(lambda _=False, i=i: self.goto(i))
            l.addWidget(b)
        l.addStretch(1)
        self.conn_btn = QPushButton()
        self.conn_btn.setProperty("kind", "nav")
        self.conn_btn.setFixedSize(52, 40)
        self.conn_btn.setIconSize(QSize(20, 20))
        self.conn_btn.setCursor(Qt.PointingHandCursor)
        self.conn_btn.clicked.connect(lambda: DiagnosticsDialog(self, self.engine).exec())
        l.addWidget(self.conn_btn)
        self.nav[2].clicked.connect(lambda: self.goto(PAGE_SETTINGS))
        l.addWidget(self.nav[2])
        self.me_btn = QPushButton()
        self.me_btn.setProperty("kind", "icon")
        self.me_btn.setFixedSize(52, 52)
        self.me_avatar = Avatar(self.engine.me, self.engine.identity.name, 40, status=self.engine.settings.status, parent=self.me_btn)
        self.me_avatar.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.me_avatar.move(6, 6)
        self.me_btn.clicked.connect(self._status_menu)
        self.me_btn.setToolTip("Mon statut")
        l.addWidget(self.me_btn)
        return rail

    def _wire(self) -> None:
        c = self.ctrl
        self.chats.btn_add.clicked.connect(self.add_contact)
        self.chats.btn_group.clicked.connect(self.new_group)
        self.contacts.add_contact.connect(self.add_contact)
        self.contacts.open_chat.connect(lambda cid: (self.goto(PAGE_CHATS), self.chats.open_conv(cid)))
        c.error.connect(lambda m: self.toast.show_message(m, "error", 5000))
        self.settings.security_changed.connect(self.apply_security_settings)
        self.settings.theme_changed.connect(lambda m, a: self.theme_changed.emit(m, a))
        c.info.connect(lambda m: self.toast.show_message(m, "ok"))
        self.settings.status_message.connect(lambda m, k: self.toast.show_message(m, k))
        self.settings.logout_requested.connect(self.logout)
        c.message.connect(self._on_message_notify)
        c.contact_request.connect(self._on_request_notify)
        c.conv_changed.connect(lambda *_: self.update_badges())
        c.contacts_changed.connect(self.update_badges)
        c.connection.connect(lambda state, info: self._set_conn(state))
        QApplication.instance().applicationStateChanged.connect(lambda *_: self.chats.on_window_activated())

    def _setup_tray(self, icon: QIcon | None) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon or QIcon(icons.logo_pixmap(32)), self)
        menu = QMenu()
        menu.addAction("Ouvrir LDC P2P", self.show_front)
        menu.addSeparator()
        menu.addAction("Quitter", lambda: (setattr(self, "_really_quit", True), self.close(), QApplication.quit()))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_front() if r == QSystemTrayIcon.Trigger else None)
        self.tray.messageClicked.connect(self._open_notified)
        self.tray.setToolTip("LDC P2P")
        self.tray.show()

    # -- appels ------------------------------------------------------------------------------------------
    def request_call(self, conv: str, video: bool) -> None:
        from ..engine.calls import calls_available
        if not calls_available():
            if ConfirmDialog(self, "Composants d'appel manquants",
                             "Les appels utilisent des composants supplémentaires (WebRTC, audio, caméra ; environ 150 Mo). Les installer maintenant ?",
                             "Installer").exec():
                self.toast.show_message("Installation des composants d'appel… (quelques minutes)", "info", 8000)

                async def run() -> None:
                    ok, msg = await install_call_components()
                    self.toast.show_message(msg, "ok" if ok else "error", 7000)
                asyncio.ensure_future(run())
            return
        self.ctrl.run(self.engine.start_call(conv, video))

    def _on_call_state(self, call) -> None:
        e = self.engine
        name = e.display_name(call.peer)
        if call.state == "ended":
            if self.incoming:
                self.incoming.stop_ring()
                self.incoming.close()
                self.incoming = None
            e.remote_video_sink = e.local_video_sink = None
            win = self.call_win
            if win:
                win.set_state("ended", call.reason)
                QTimer.singleShot(1400, win.close)
                self.call_win = None
            if call.reason and call.reason not in ("Appel refusé",):
                self.toast.show_message(call.reason, "error", 5000)
            return
        if call.direction == "in" and call.state == "ringing":
            self.incoming = IncomingCallPopup(name, call.peer, call.video)
            self.incoming.accepted.connect(lambda v: self._accept_call(v))
            self.incoming.declined.connect(lambda: self.ctrl.run(e.reject_call()))
            geo = self.screen().availableGeometry()
            self.incoming.move(geo.right() - 390, geo.top() + 40)
            self.incoming.show()
            if self.tray and e.settings.notifications:
                self.tray.showMessage("LDC P2P", f"Appel {'vidéo ' if call.video else ''}entrant" + (f" de {name}" if e.settings.notification_previews else ""),
                                      QSystemTrayIcon.NoIcon, 8000)
            return
        if self.incoming:
            self.incoming.stop_ring()
            self.incoming.close()
            self.incoming = None
        if self.call_win is None:
            w = CallWindow(name, call.peer, call.video)
            w.hangup_clicked.connect(lambda: self.ctrl.run(e.hangup()))
            w.mute_toggled.connect(e.set_call_muted)
            w.camera_toggled.connect(e.set_call_camera)
            e.remote_video_sink, e.local_video_sink = w.view.set_remote, w.view.set_local
            self.call_win = w
            w.show()
            w.raise_()
        self.call_win.set_state(call.state, "")

    def _accept_call(self, video: bool) -> None:
        self.ctrl.run(self.engine.accept_call(video))

    # -- mise à jour -----------------------------------------------------------------------------------
    def _build_update_bar(self, root) -> QWidget:
        bar = GlassPanel(self.bg, radius=16, strong=True)
        bar.setFixedHeight(48)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 6, 10, 6)
        self.update_lbl = label("")
        self.update_btn = button("Mettre à jour et redémarrer", "primary", "download", "#FFFFFF")
        later = button("Plus tard", "ghost")
        h.addWidget(self.update_lbl, 1)
        h.addWidget(self.update_btn)
        h.addWidget(later)
        bar.hide()
        later.clicked.connect(bar.hide)
        self.update_btn.clicked.connect(lambda: (self.update_btn.setEnabled(False), asyncio.ensure_future(self.updates.apply())))
        self.updates.available.connect(self._show_update)
        self.updates.status.connect(lambda m, k: self.toast.show_message(m, k, 4500))
        self.updates.ready_to_restart.connect(self._restart_after_update)
        return bar

    def _show_update(self, up) -> None:
        self.update_lbl.setText(f"Nouvelle version {up.version} disponible")
        self.update_btn.setEnabled(True)
        self._place_update_bar()
        self.update_bar.show()
        self.update_bar.raise_()

    def _place_update_bar(self) -> None:
        w = min(self.bg.width() - 120, 720)
        self.update_bar.setGeometry((self.bg.width() - w) // 2, 10, w, 48)

    def _restart_after_update(self) -> None:
        QTimer.singleShot(1500, lambda: (UpdateManager.relaunch(), setattr(self, "_really_quit", True), self.close()))

    # -- verrouillage, absence, raccourcis ----------------------------------------------------------------
    def lock_now(self) -> None:
        self.lock_overlay.lock()

    def _auto_away(self) -> None:
        if self.engine.settings.status == "online":
            self._auto_away_active = True
            self.ctrl.run(self.engine.set_status("away"))
            self.me_avatar.dot.set_status("away")

    def _auto_back(self) -> None:
        if self._auto_away_active and self.engine.settings.status == "away":
            self.ctrl.run(self.engine.set_status("online"))
            self.me_avatar.dot.set_status("online")
        self._auto_away_active = False

    def apply_security_settings(self) -> None:
        s = self.engine.settings
        self.activity.lock_minutes, self.activity.away_minutes = s.lock_minutes, s.auto_away_minutes
        self._apply_capture_protection()

    def _apply_capture_protection(self) -> None:
        winint.set_capture_protection(int(self.winId()), self.engine.settings.capture_protection)

    def _shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:
            QShortcut(QKeySequence(keys), self, activated=fn)
        sc("Ctrl+K", lambda: (self.goto(PAGE_CHATS), self.chats.search.setFocus(), self.chats.search.selectAll()))
        sc("Ctrl+N", self.add_contact)
        sc("Ctrl+L", self.lock_now)
        sc("Ctrl+1", lambda: self.goto(PAGE_CHATS))
        sc("Ctrl+2", lambda: self.goto(PAGE_CONTACTS))
        sc("Ctrl+3", lambda: self.goto(PAGE_SETTINGS))
        sc("Alt+Down", lambda: self._step_conv(1))
        sc("Alt+Up", lambda: self._step_conv(-1))
        sc("Ctrl+Tab", lambda: self._step_conv(1))
        sc("Ctrl+Shift+Tab", lambda: self._step_conv(-1))
        sc("Esc", self._escape)

    def _step_conv(self, d: int) -> None:
        items = [i for i in self.chats.list.model_.items if not i.get("result")]
        if not items:
            return
        idx = next((k for k, i in enumerate(items) if i["conv"] == self.chats.conv), -1)
        self.goto(PAGE_CHATS)
        self.chats.open_conv(items[(idx + d) % len(items)]["conv"])

    def _escape(self) -> None:
        c = self.chats
        if self.stack.currentIndex() != PAGE_CHATS:
            return
        if c.composer.mode:
            c.composer._cancel_banner()
        elif c.search.text():
            c.search.clear()
        elif c.details_open:
            c.toggle_details()
        else:
            c.view.clear_selection()

    # -- navigation ---------------------------------------------------------------------------------
    def goto(self, page: int) -> None:
        self.stack.setCurrentIndex(page)
        for i, b in enumerate(self.nav):
            b.setChecked(i == page)

    def show_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def add_contact(self, prefill: str = "") -> None:
        e = self.engine
        d = AddContactDialog(self, e.my_card_code(), e.identity.name, e.me, prefill if isinstance(prefill, str) else "")
        if not (d.exec() and d.code_value):
            return
        from ..engine import EngineError
        try:
            pub, twins = e.preview_card(d.code_value)
        except EngineError as ex:
            self.toast.show_message(str(ex), "error", 5000)
            return
        if twins and not ConfirmDialog(self, "Un contact porte déjà ce nom",
                                       f"« {pub.name} » ressemble à votre contact « {twins[0]['alias'] or twins[0]['name']} », mais l'identifiant est différent "
                                       "(ce n'est donc PAS la même personne). Vérifiez auprès de lui avant de continuer.", "Ajouter quand même").exec():
            return
        connected = bool(e.relay and e.relay.connected)
        task = self.ctrl.run(e.add_contact_from_code(d.code_value, d.greeting_value))

        async def report(cid: str) -> None:
            for _ in range(60):  # jusqu'à 6 s : on attend la réponse RÉELLE du serveur
                if e.request_results.get(cid):
                    break
                await asyncio.sleep(0.1)
            res = e.request_results.get(cid)
            if res == "delivered":
                self.toast.show_message("✓ Demande reçue par l'application de votre contact", "ok", 6000)
            elif res in ("queued", "offline"):
                self.toast.show_message("✓ Demande reçue par le serveur : elle sera remise dès que votre contact se connecte", "ok", 7000)
            else:
                self.toast.show_message("✗ Le serveur n'a PAS confirmé la demande. Ouvrez le diagnostic (icône Wi-Fi).", "error", 9000)

        def done(t):
            res = t.result() if not t.cancelled() and t.exception() is None else None
            if res:
                asyncio.ensure_future(report(res["id"]))
                if not connected:
                    self.toast.show_message("Demande enregistrée, mais vous n'êtes PAS connecté au serveur : elle partira dès la connexion (vérifiez Paramètres › Réseau).", "error", 8000)
                self.goto(PAGE_CHATS)
                self.chats.open_conv(res["id"])
        task.add_done_callback(done)

    def open_add_contact(self, code: str) -> None:
        self.show_front()
        self.add_contact(code)

    def new_group(self) -> None:
        cands = self.engine.contacts({"accepted"})
        if not cands:
            self.toast.show_message("Ajoutez d'abord un contact accepté pour créer un groupe.", "info", 4500)
            return
        d = NewGroupDialog(self, cands)
        if d.exec():
            task = self.ctrl.run(self.engine.create_group(d.result_name, d.result_ids))

            def done(t):
                if not t.cancelled() and t.exception() is None and t.result():
                    self.goto(PAGE_CHATS)
                    self.chats.open_conv(t.result())
            task.add_done_callback(done)

    def _status_menu(self) -> None:
        menu = QMenu(self)
        for key in ("online", "away", "dnd", "invisible"):
            col = theme.PRESENCE_COLORS[key]
            act = QAction(theme.PRESENCE_LABELS[key], menu)
            act.setCheckable(True)
            act.setChecked(self.engine.settings.status == key)
            act.triggered.connect(lambda _=False, k=key: self._set_status(k))
            menu.addAction(act)
        menu.exec(self.me_btn.mapToGlobal(self.me_btn.rect().topRight()))

    def _set_status(self, key: str) -> None:
        self.ctrl.run(self.engine.set_status(key))
        self.me_avatar.dot.set_status(key)

    # -- état de connexion --------------------------------------------------------------------------------
    def _set_conn(self, state: str) -> None:
        e = self.engine
        if not e.settings.server_host:
            name, color, tip = "wifi", "#8E8EA0", "Aucun serveur configuré (réseau local uniquement)"
        elif state == "connected":
            name, color, tip = "wifi", "#34D399", "Connecté au serveur"
        elif state in ("connecting", "disconnected"):
            name, color, tip = "wifi", "#FBBF24", "Connexion au serveur…"
        elif state == "replaced":
            name, color, tip = "alert", "#F87171", "Session ouverte sur un autre appareil"
        else:
            name, color, tip = "wifi", "#8E8EA0", "Hors ligne"
        self.conn_btn.setIcon(icons.icon(name, color, 20))
        self.conn_btn.setToolTip(tip + " — cliquer pour le diagnostic")

    # -- notifications --------------------------------------------------------------------------------------
    def update_badges(self) -> None:
        unread = sum(self.engine.db.unread_counts().values())
        reqs = len(self.engine.db.list_contacts({"pending_in"}))
        self.nav[0].set_badge(unread)
        self.nav[1].set_badge(reqs)
        self.setWindowTitle(f"({unread}) LDC P2P" if unread else "LDC P2P")

    def _should_notify(self, conv: str) -> bool:
        s = self.engine.settings
        if not s.notifications or s.status == "dnd" or not self.tray:
            return False
        if self.engine.db.conv_settings(conv)["muted"]:
            m = self.engine.db.last_messages().get(conv)  # conversation muette : seules les mentions notifient
            return bool(m and isinstance(m.get("extra"), dict) and self.engine.me in (m["extra"].get("men") or []))
        return not (self.isActiveWindow() and self.stack.currentIndex() == PAGE_CHATS and self.chats.conv == conv)

    def _on_message_notify(self, conv: str, mid: str, direction: str) -> None:
        self.update_badges()
        if direction == "in" and not (self.isActiveWindow() and self.stack.currentIndex() == PAGE_CHATS and self.chats.conv == conv) \
                and not self.engine.db.conv_settings(conv)["muted"] and self.engine.settings.status != "dnd":
            m0 = self.engine.db.get_message(mid)
            if m0 and m0["kind"] != "system":  # bandeau interne : fonctionne même si les notifications Windows sont désactivées
                self.toast.show_message(f"Nouveau message de {self.engine.display_name(m0['sender'])}", "info", 4000)
        if direction != "in" or not self._should_notify(conv):
            return
        e = self.engine
        m = e.db.get_message(mid)
        if not m:
            return
        self._last_notified_conv = conv
        if e.settings.sound:
            sounds.play(e.settings.sound_name)
        if e.settings.notification_previews and not self.lock_overlay.isVisible():
            title = e.display_name(m["sender"])
            body = (m["body"] or "")[:140] if m["kind"] == "text" else "Fichier : " + str((m.get("extra") or {}).get("name", ""))
        else:
            title, body = "LDC P2P", "Nouveau message chiffré"
        self.tray.showMessage(title, body, QSystemTrayIcon.NoIcon, 6000)

    def _on_request_notify(self, cid: str) -> None:
        self.update_badges()
        c = self.engine.db.get_contact(cid)
        who = c["name"] if c else "Quelqu'un"
        self.toast.show_message(f"Nouvelle demande de contact de {who} — ouvrez l'onglet Contacts", "info", 8000)  # toujours visible dans l'application
        if self.engine.settings.sound and self.engine.settings.status != "dnd":
            sounds.play(self.engine.settings.sound_name)
        if self.engine.settings.notifications and self.engine.settings.status != "dnd" and self.tray:
            self._last_notified_conv = cid
            self.tray.showMessage("LDC P2P", "Nouvelle demande de contact", QSystemTrayIcon.NoIcon, 6000)

    def _open_notified(self) -> None:
        self.show_front()
        if self._last_notified_conv:
            self.goto(PAGE_CHATS)
            self.chats.open_conv(self._last_notified_conv)

    # -- fenêtre --------------------------------------------------------------------------------------------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._apply_capture_protection()
        if self.use_backdrop:
            apply_windows_backdrop(self)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.lock_overlay.isVisible():
            self.lock_overlay.setGeometry(self.rect())
        if self.update_bar.isVisible():
            self._place_update_bar()
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 28)

    def closeEvent(self, e) -> None:
        if self.engine.settings.close_to_tray and self.tray and not getattr(self, "_really_quit", False):
            e.ignore()
            self.hide()
            self.tray.showMessage("LDC P2P", "L'application continue en arrière-plan.", QSystemTrayIcon.NoIcon, 3000)
            return
        if self.tray:
            self.tray.hide()
        super().closeEvent(e)
        self.closed.emit()
