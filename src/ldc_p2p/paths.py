"""Emplacements des données. Aucune saisie utilisateur n'est jamais utilisée dans un chemin."""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

_ID_RE = re.compile(r"^LDC-[A-Z2-7]{6}(-[A-Z2-7]{6}){3}$")


def data_dir() -> Path:
    override = os.environ.get("LDC_P2P_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "LDCP2P"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ldc-p2p"
    base.mkdir(parents=True, exist_ok=True)
    return base


def profiles_root() -> Path:
    p = data_dir() / "profiles"
    p.mkdir(parents=True, exist_ok=True)
    return p


def profile_dir(ldc_id: str) -> Path:
    """Dossier d'un profil, indexé par l'identifiant LDC (validé), jamais par le pseudo."""
    if not _ID_RE.match(ldc_id):
        raise ValueError("Identifiant LDC invalide")
    p = profiles_root() / ldc_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_profiles() -> list[str]:
    return sorted(d.name for d in profiles_root().iterdir() if d.is_dir() and _ID_RE.match(d.name) and (d / "keystore.json").exists())


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
