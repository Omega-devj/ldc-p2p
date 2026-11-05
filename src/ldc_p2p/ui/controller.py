"""Pont moteur -> Qt. Avec qasync, la boucle asyncio tourne dans le thread Qt : les signaux sont émis sans risque."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

from PySide6.QtCore import QObject, Signal

from ..engine import Engine, EngineError

log = logging.getLogger("ldc.ui")


class UiController(QObject):
    conv_changed = Signal(str)
    message = Signal(str, str, str)       # conv, mid, direction
    message_status = Signal(str, str)     # mid, status
    message_updated = Signal(str, str)    # mid, conv
    message_deleted = Signal(str, str)    # mid, conv
    contacts_changed = Signal()
    contact_request = Signal(str)
    presence_changed = Signal()
    typing = Signal(str, str)             # conv, peer
    connection = Signal(str, dict)
    file_state = Signal(str, str)
    file_progress = Signal(str, int, int)
    call_state = Signal(object)
    call_quality = Signal(object, object)
    missed_call = Signal(str)
    error = Signal(str)
    info = Signal(str)

    def __init__(self, engine: Engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        ev = engine.events
        ev.on("conversation_update", lambda conv: self.conv_changed.emit(conv or ""))
        ev.on("message", lambda conv, mid, direction: self.message.emit(conv, mid, direction))
        ev.on("message_status", lambda mid, status: self.message_status.emit(mid, status))
        ev.on("message_updated", lambda mid, conv: self.message_updated.emit(mid, conv))
        ev.on("message_deleted", lambda mid, conv: self.message_deleted.emit(mid, conv))
        ev.on("contact_update", lambda id=None: self.contacts_changed.emit())
        ev.on("contact_request", lambda id: self.contact_request.emit(id))
        ev.on("presence", lambda id=None: self.presence_changed.emit())
        ev.on("typing", lambda conv, peer: self.typing.emit(conv, peer))
        ev.on("connection_state", lambda state, info: self.connection.emit(state, info or {}))
        ev.on("file_state", lambda mid, state: self.file_state.emit(mid, state))
        ev.on("file_progress", lambda mid, done, total: self.file_progress.emit(mid, done, total))
        ev.on("call_state", lambda call: self.call_state.emit(call))
        ev.on("call_quality", lambda call, stats: self.call_quality.emit(call, stats))
        ev.on("missed_call", lambda peer: self.missed_call.emit(peer))

    def run(self, coro: Awaitable, ok: str | None = None, on_success=None) -> asyncio.Task:
        """Lance une coroutine du moteur ; les EngineError deviennent des messages lisibles."""
        async def wrapper():
            try:
                res = await coro
                if ok:
                    self.info.emit(ok)
                if on_success:
                    on_success()
                return res
            except EngineError as e:
                self.error.emit(str(e))
            except Exception as e:  # ne jamais laisser une exception disparaître silencieusement
                log.exception("Erreur d'action")
                self.error.emit(f"Erreur inattendue : {e}")
        return asyncio.ensure_future(wrapper())
