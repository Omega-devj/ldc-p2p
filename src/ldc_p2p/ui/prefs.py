"""Préférences d'interface globales (thème, accent) : fichier ui.json, lu avant même le déverrouillage."""
from __future__ import annotations

import json

from .. import paths


def load() -> tuple[str, str]:
    try:
        d = json.loads((paths.data_dir() / "ui.json").read_text("utf-8"))
        return str(d.get("theme", "sombre")), str(d.get("accent", "violet"))
    except (OSError, ValueError):
        return "sombre", "violet"


def save(mode: str, accent: str) -> None:
    try:
        (paths.data_dir() / "ui.json").write_text(json.dumps({"theme": mode, "accent": accent}), "utf-8")
    except OSError:
        pass
