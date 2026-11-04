"""Mises à jour automatiques depuis les releases GitHub (manifeste signé + empreinte vérifiée)."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .. import release_key, updater

log = logging.getLogger("ldc.update")


class UpdateManager(QObject):
    available = Signal(object)     # updater.Update
    status = Signal(str, str)      # message, type (info/ok/error)
    ready_to_restart = Signal()

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.pending: updater.Update | None = None
        self.busy = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: asyncio.ensure_future(self.check()))
        QTimer.singleShot(8000, lambda: asyncio.ensure_future(self.check()))
        self._timer.start(6 * 3600 * 1000)

    @property
    def enabled(self) -> bool:
        s = self.engine.settings
        return bool(release_key.RELEASE_PUBLIC_KEY_HEX and s.update_url)

    async def check(self, manual: bool = False) -> None:
        s = self.engine.settings
        if not self.enabled or (s.auto_update == 0 and not manual) or self.busy:
            if manual:
                self.status.emit("Mises à jour indisponibles (aucune source ou clé de publication).", "error")
            return
        loop = asyncio.get_running_loop()
        try:
            up = await loop.run_in_executor(None, updater.check_for_update, s.update_url, updater.current_version(),
                                            bytes.fromhex(release_key.RELEASE_PUBLIC_KEY_HEX))
        except Exception as e:
            log.info("Vérification de mise à jour impossible: %s", e)
            if manual:
                self.status.emit(f"Vérification impossible : {e}", "error")
            return
        if up is None:
            if manual:
                self.status.emit("Vous utilisez la dernière version.", "ok")
            return
        self.pending = up
        self.available.emit(up)
        if s.auto_update == 2:
            await self.apply(up)

    async def apply(self, up: updater.Update | None = None) -> None:
        up = up or self.pending
        if not up or self.busy:
            return
        self.busy = True
        loop = asyncio.get_running_loop()
        root = updater.install_root()
        dest = Path(tempfile.gettempdir()) / "ldc-p2p-updates"
        try:
            self.status.emit("Téléchargement de la mise à jour…", "info")
            if root is not None and up.source_url:
                zpath = await loop.run_in_executor(None, lambda: updater.download_update(up, dest, source=True))
                await loop.run_in_executor(None, updater.apply_source_update, zpath, root)
                req = root / "requirements.txt"
                if req.exists():  # nouvelles dépendances éventuelles
                    self.status.emit("Mise à jour des composants…", "info")
                    await loop.run_in_executor(None, lambda: subprocess.run(
                        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", str(req)],
                        check=False, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
                self.status.emit(f"Version {up.version} installée : redémarrage…", "ok")
                self.ready_to_restart.emit()
            elif up.url:
                exe = await loop.run_in_executor(None, updater.download_update, up, dest)
                self.status.emit("Lancement de l'installateur vérifié…", "ok")
                if sys.platform == "win32":
                    os.startfile(str(exe))  # noqa: S606 (fichier vérifié par signature + SHA-256)
                self.ready_to_restart.emit()
            else:
                self.status.emit("Aucun fichier compatible avec cette installation.", "error")
        except Exception as e:
            log.exception("Mise à jour en échec")
            self.status.emit(f"Mise à jour impossible : {e}", "error")
        finally:
            self.busy = False

    @staticmethod
    def relaunch() -> None:
        root = updater.install_root()
        if root is not None:
            env = dict(os.environ, PYTHONPATH=str(root / "src"))
            subprocess.Popen([sys.executable, "-m", "ldc_p2p"], cwd=root, env=env,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
