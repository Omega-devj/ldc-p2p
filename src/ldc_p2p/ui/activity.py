"""Suivi d'inactivité (verrouillage automatique, « Absent » automatique) via un filtre d'événements global."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

_ACTIVITY = {QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel, QEvent.TouchBegin}


class ActivityMonitor(QObject):
    idle_lock = Signal()     # inactivité >= lock_minutes
    idle_away = Signal()     # inactivité >= auto_away_minutes
    back = Signal()          # activité après une période d'inactivité « absent »

    def __init__(self, lock_minutes: int = 0, away_minutes: int = 5, parent=None):
        super().__init__(parent)
        self.lock_minutes, self.away_minutes = lock_minutes, away_minutes
        self.last = time.monotonic()
        self._away_fired = False
        self._lock_fired = False
        app = QApplication.instance()
        app.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(5000)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() in _ACTIVITY:
            self.last = time.monotonic()
            if self._away_fired:
                self._away_fired = False
                self.back.emit()
            self._lock_fired = False
        return False

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last

    def _tick(self) -> None:
        idle = self.idle_seconds()
        if self.away_minutes and not self._away_fired and idle >= self.away_minutes * 60:
            self._away_fired = True
            self.idle_away.emit()
        if self.lock_minutes and not self._lock_fired and idle >= self.lock_minutes * 60:
            self._lock_fired = True
            self.idle_lock.emit()
