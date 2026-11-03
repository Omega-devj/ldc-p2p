"""Fenêtre principale : navigation latérale + pages + notifications système."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMenu, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from ..engine.core import ChatEngine
from ..profile import Profile
from . import icons, theme
from .controller import UiController
from .dialogs import AddContactDialog, DiagnosticsDialog, NewGroupDialog
from .pages.chats import ChatsPage
from .pages.contacts import ContactsPage
from .pages.settings import SettingsPage
from .widgets.glass import AuroraBackground, Avatar, GlassPanel, PresenceDot, Toast

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
        self.setIcon(icons.icon(self.icon_name, "#E9E3FF" if self.isChecked() else "#9A9AAE", 24, 1.9))

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


class MainWindow(QWidget):
    logout = Signal(bool)
    closed = Signal()

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

    # -- navigation ---------------------------------------------------------------------------------
    def goto(self, page: int) -> None:
        self.stack.setCurrentIndex(page)
        for i, b in enumerate(self.nav):
            b.setChecked(i == page)

    def show_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def add_contact(self) -> None:
        e = self.engine
        d = AddContactDialog(self, e.my_card_code(), e.identity.name, e.me)
        if d.exec() and d.code_value:
            task = self.ctrl.run(e.add_contact_from_code(d.code_value, d.greeting_value), ok="Demande envoyée")

            def done(t):
                res = t.result() if not t.cancelled() and t.exception() is None else None
                if res:
                    self.goto(PAGE_CHATS)
                    self.chats.open_conv(res["id"])
            task.add_done_callback(done)

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
            return False
        return not (self.isActiveWindow() and self.stack.currentIndex() == PAGE_CHATS and self.chats.conv == conv)

    def _on_message_notify(self, conv: str, mid: str, direction: str) -> None:
        self.update_badges()
        if direction != "in" or not self._should_notify(conv):
            return
        e = self.engine
        m = e.db.get_message(mid)
        if not m:
            return
        self._last_notified_conv = conv
        if e.settings.notification_previews:
            title = e.display_name(m["sender"])
            body = (m["body"] or "")[:140] if m["kind"] == "text" else "Fichier : " + str((m.get("extra") or {}).get("name", ""))
        else:
            title, body = "LDC P2P", "Nouveau message chiffré"
        self.tray.showMessage(title, body, QSystemTrayIcon.NoIcon, 6000)

    def _on_request_notify(self, cid: str) -> None:
        self.update_badges()
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
        if self.use_backdrop:
            apply_windows_backdrop(self)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
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
