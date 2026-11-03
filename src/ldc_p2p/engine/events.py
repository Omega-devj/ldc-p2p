"""Bus d'événements minimal : le moteur ne dépend ni de Qt ni de l'interface."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger("ldc.events")


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[..., None]]] = defaultdict(list)
        self._wild: list[Callable[..., None]] = []

    def on(self, name: str, cb: Callable[..., None]) -> None:
        self._subs[name].append(cb)

    def on_any(self, cb: Callable[..., None]) -> None:
        """cb(name, **data) pour tous les événements."""
        self._wild.append(cb)

    def emit(self, name: str, **data: Any) -> None:
        for cb in list(self._subs.get(name, ())):
            try:
                cb(**data)
            except Exception:
                log.exception("Erreur dans un abonné à l'événement %s", name)
        for cb in list(self._wild):
            try:
                cb(name, **data)
            except Exception:
                log.exception("Erreur dans un abonné global à l'événement %s", name)
