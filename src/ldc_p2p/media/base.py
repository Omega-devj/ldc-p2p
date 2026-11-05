"""Interface média des appels : le moteur ne connaît ni WebRTC, ni micro, ni caméra (testable, remplaçable)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


class CallsUnavailable(Exception):
    """Composants d'appel (aiortc, sounddevice, opencv) non installés."""


@dataclass
class IceConfig:
    """Comment établir le chemin média.
    local_only : AUCUN serveur (ni STUN ni TURN) → connexion directe sur réseau local / IPv6 / port redirigé.
    stun       : serveurs STUN (découverte de l'adresse publique) ; turn : relais média de secours.
    hide_ip    : ne publie que des candidats « relais » (TURN obligatoire) pour ne pas révéler son adresse."""
    local_only: bool = False
    stun: list[str] = field(default_factory=list)
    turn_url: str = ""
    turn_user: str = ""
    turn_pass: str = ""
    hide_ip: bool = False
    audio_in: str = ""
    audio_out: str = ""
    camera: str = ""


@dataclass
class MediaCallbacks:
    on_state: Callable[[str], None] = lambda s: None            # "connecting" | "connected" | "failed" | "closed"
    on_remote_video: Callable[[object], None] = lambda frame: None   # tableau numpy RGB (H, W, 3)
    on_local_video: Callable[[object], None] = lambda frame: None
    on_quality: Callable[[str, dict], None] = lambda q, stats: None  # "good" | "medium" | "poor"


class CallBackend(ABC):
    video: bool = False

    @abstractmethod
    async def create_offer(self, video: bool) -> str: ...

    @abstractmethod
    async def accept_offer(self, sdp: str, video: bool) -> str: ...

    @abstractmethod
    async def set_answer(self, sdp: str) -> None: ...

    @abstractmethod
    def set_muted(self, muted: bool) -> None: ...

    @abstractmethod
    def set_camera(self, on: bool) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
