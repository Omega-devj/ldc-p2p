"""Moteur de messagerie LDC P2P (indépendant de l'interface)."""
from .core import ChatEngine, EngineError
from .files import FilesMixin, safe_filename
from .groups import GroupsMixin
from .settings import STATUSES, EngineSettings


class Engine(GroupsMixin, FilesMixin):
    """Moteur complet : messages privés, groupes, fichiers, présence."""


__all__ = ["Engine", "ChatEngine", "EngineError", "EngineSettings", "STATUSES", "safe_filename"]
