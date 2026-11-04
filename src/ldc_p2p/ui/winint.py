"""Intégration Windows : démarrage automatique, lien ldc://, protection contre les captures d'écran.
Toutes les fonctions sont sans effet (et sans erreur) hors Windows."""
from __future__ import annotations

import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE = "LDC P2P"


def launch_command() -> str:
    """Commande qui relance l'application (exécutable gelé ou interpréteur + module)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    src = Path(__file__).resolve().parents[1]
    return f'"{pyw if pyw.exists() else exe}" -c "import sys; sys.path.insert(0, r\'{src}\'); from ldc_p2p.app import main; main()"'


def autostart_enabled() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_VALUE, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def register_protocol() -> bool:
    """Associe ldc:// à l'application pour l'utilisateur courant (aucun droit administrateur)."""
    if not IS_WIN:
        return False
    try:
        import winreg
        base = r"Software\Classes\ldc"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:LDC P2P")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launch_command() + ' "%1"')
        return True
    except OSError:
        return False


def protocol_registered() -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\ldc\shell\open\command"):
            return True
    except OSError:
        return False


def set_capture_protection(hwnd: int, enabled: bool) -> bool:
    """WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+) : la fenêtre apparaît noire dans les captures et partages d'écran."""
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(hwnd), 0x11 if enabled else 0x0))
    except Exception:
        return False


def url_from_argv(argv: list[str]) -> str | None:
    for a in argv[1:]:
        if a.lower().startswith("ldc://"):
            return a
    return None
