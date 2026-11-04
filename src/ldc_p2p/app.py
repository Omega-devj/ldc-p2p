"""Point d'entrée : onboarding -> session (moteur + interface) -> verrouillage / fermeture propre."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__, logging_setup, paths, profile, updater
from .ui import prefs, winint
from .engine import Engine, EngineSettings
from .ui import icons, theme
from .ui.controller import UiController
from .ui.main_window import MainWindow
from .ui.onboarding import OnboardingWindow

log = logging.getLogger("ldc.app")


class Shell:
    def __init__(self, app: QApplication):
        self.app = app
        self.onboarding: OnboardingWindow | None = None
        self.window: MainWindow | None = None
        self.engine: Engine | None = None
        self.profile: profile.Profile | None = None
        self.lock: QLockFile | None = None
        self.ctrl: UiController | None = None
        self.pending_url: str | None = None
        self.ipc: QLocalServer | None = None

    # -- liens ldc:// : une instance déjà ouverte reçoit l'URL ------------------------------------------
    @staticmethod
    def ipc_name() -> str:
        import getpass
        return f"ldc-p2p-{getpass.getuser()}"

    def start_ipc(self) -> None:
        QLocalServer.removeServer(self.ipc_name())
        self.ipc = QLocalServer()
        self.ipc.listen(self.ipc_name())
        self.ipc.newConnection.connect(self._ipc_in)

    def _ipc_in(self) -> None:
        sock = self.ipc.nextPendingConnection()
        sock.waitForReadyRead(500)
        url = bytes(sock.readAll()).decode("utf-8", "ignore").strip()
        sock.disconnectFromServer()
        if url.lower().startswith("ldc://"):
            self.handle_url(url)

    def handle_url(self, url: str) -> None:
        if self.window:
            self.window.open_add_contact(url)
        else:
            self.pending_url = url

    def show_onboarding(self) -> None:
        if self.onboarding is None:
            self.onboarding = OnboardingWindow()
            self.onboarding.profile_ready.connect(self.start_session)
        self.onboarding.reset()
        self.onboarding.show()
        self.onboarding.raise_()

    def start_session(self, p: profile.Profile, cfg: dict) -> None:
        lock = QLockFile(str(p.directory / "profile.lock"))
        lock.setStaleLockTime(30_000)
        if not lock.tryLock(200):
            p.db.close()
            QMessageBox.warning(self.onboarding, "Profil déjà ouvert", "Ce profil est déjà ouvert dans une autre fenêtre de LDC P2P.")
            return
        self.lock, self.profile = lock, p
        settings = EngineSettings.load(p.db)
        if cfg.get("host"):
            settings.server_host, settings.server_port, settings.server_tls = cfg["host"], cfg["port"], cfg["tls"]
            settings.server_token = cfg.get("token", "")
        settings.save(p.db)
        self.engine = Engine(p.identity, p.db, settings)
        self.ctrl = UiController(self.engine)
        asyncio.ensure_future(self._boot(self.ctrl))

    async def _boot(self, ctrl: UiController) -> None:
        try:
            await self.engine.start()
        except Exception:
            log.exception("Démarrage du moteur impossible")
            QMessageBox.critical(None, "LDC P2P", "Le moteur de messagerie n'a pas pu démarrer. Consultez le journal.")
            self._release()
            return
        self._make_window(ctrl)
        self.window.show()
        if self.onboarding:
            self.onboarding.hide()
        if self.pending_url:
            QTimer.singleShot(600, lambda: self.window and self.window.open_add_contact(self.pending_url))
            self.pending_url = None

    def _make_window(self, ctrl: UiController) -> None:
        self.window = MainWindow(self.profile, ctrl)
        self.window.logout.connect(lambda delete: asyncio.ensure_future(self.end_session(delete)))
        self.window.closed.connect(lambda: asyncio.ensure_future(self.shutdown()))
        self.window.theme_changed.connect(self.change_theme)

    def change_theme(self, mode: str, accent: str) -> None:
        """Applique thème/accent immédiatement : feuille de style + reconstruction de la fenêtre (le moteur continue)."""
        from .ui import theme
        theme.configure(mode, accent)
        icons.pixmap.cache_clear()
        self.app.setStyleSheet(theme.build_qss())
        old = self.window
        if old is None:
            return
        geo, page = old.geometry(), old.stack.currentIndex()
        old.closed.disconnect()
        if old.tray:
            old.tray.hide()
        old.hide()
        old.deleteLater()
        self._make_window(self.ctrl)
        self.window.setGeometry(geo)
        self.window.show()
        self.window.goto(2 if page == 2 else page)

    def _release(self) -> None:
        if self.lock:
            self.lock.unlock()
            self.lock = None

    async def _stop_engine(self) -> None:
        if self.engine:
            await self.engine.stop()
        if self.profile:
            self.profile.db.close()
        self._release()

    async def end_session(self, delete: bool) -> None:
        pid = self.profile.identity.id if self.profile else None
        w, self.window = self.window, None
        if w:
            w.closed.disconnect()
            w.tray and w.tray.hide()
            w.close()
            w.deleteLater()
        await self._stop_engine()
        if delete and pid:
            profile.delete_profile(pid)
        self.engine = self.profile = None
        self.show_onboarding()

    async def shutdown(self) -> None:
        await self._stop_engine()
        self.app.quit()


def build_app(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("LDC P2P")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("LDC")
    app.setFont(theme.base_font(10))
    app.setStyleSheet(theme.build_qss())
    app.setWindowIcon(QIcon(icons.logo_pixmap(64)))
    app.setQuitOnLastWindowClosed(False)
    return app


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    ap = argparse.ArgumentParser(prog="ldc-p2p")
    ap.add_argument("--demo", action="store_true", help="ouvre un profil de démonstration hors ligne (aucune donnée réelle)")
    ap.add_argument("--debug", action="store_true")
    args, qt_args = ap.parse_known_args(argv[1:])
    logging_setup.setup(logging.DEBUG if args.debug else logging.INFO)
    mode, accent = prefs.load()
    from .ui import theme as _theme
    _theme.configure(mode, accent)
    app = build_app([argv[0], *qt_args])
    url = winint.url_from_argv(argv)
    if url:  # lien ldc:// : si l'application est déjà ouverte, on lui transmet le lien et on s'efface
        sock = QLocalSocket()
        sock.connectToServer(Shell.ipc_name())
        if sock.waitForConnected(300):
            sock.write(url.encode("utf-8"))
            sock.waitForBytesWritten(500)
            return 0
    import qasync
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    shell = Shell(app)
    shell.start_ipc()
    shell.pending_url = url
    if args.demo:
        from .demo import start_demo
        start_demo(shell)
    else:
        shell.show_onboarding()
    app.lastWindowClosed.connect(lambda: None)
    with loop:
        loop.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
