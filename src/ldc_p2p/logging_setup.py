"""Journalisation : jamais de contenu de message, de clé ou de mot de passe."""
from __future__ import annotations
import logging
import logging.handlers
from . import paths


def setup(level: int = logging.INFO, to_file: bool = True) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if to_file:
        try:
            fh = logging.handlers.RotatingFileHandler(paths.logs_dir() / "ldc-p2p.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            pass
