"""Installation à la demande des composants d'appel (aiortc, sounddevice, OpenCV) dans l'environnement courant."""
from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys

from .. import updater


async def install_call_components() -> tuple[bool, str]:
    root = updater.install_root()
    req = (root / "requirements-calls.txt") if root else None
    pkgs = ["-r", str(req)] if req and req.exists() else ["aiortc>=1.9", "sounddevice>=0.4.7", "opencv-python-headless>=4.9", "numpy>=1.26"]
    loop = asyncio.get_running_loop()

    def run() -> tuple[bool, str]:
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", *pkgs],
                               capture_output=True, text=True, timeout=1200, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return r.returncode == 0, (r.stderr or r.stdout)[-300:]
        except Exception as e:
            return False, str(e)
    ok, out = await loop.run_in_executor(None, run)
    importlib.invalidate_caches()
    from ..engine.calls import calls_available
    if ok and calls_available():
        return True, "Composants d'appel installés."
    if ok:
        return True, "Composants installés : redémarrez l'application pour les activer."
    return False, f"Installation impossible : {out.strip()}"
