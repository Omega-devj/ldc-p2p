"""Appels vocaux et vidéo (1 à 1).

Signalisation : enveloppes chiffrées et signées de bout en bout (lien direct si possible, sinon relais), donc
l'empreinte DTLS du SDP est authentifiée par l'identité du contact. Médias : WebRTC (DTLS-SRTP), directs entre pairs.
Modes : « auto » (STUN, TURN de secours) ou « local » (aucun serveur : réseau local / IPv6 / port redirigé).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..crypto.e2e import new_mid, now_ms
from ..media.base import CallBackend, CallsUnavailable, IceConfig, MediaCallbacks
from .core import ChatEngine, EngineError

log = logging.getLogger("ldc.calls")
RING_SECONDS = 45
MAX_INVITES_PER_MIN = 3


@dataclass
class Call:
    id: str
    peer: str
    video: bool
    direction: str                      # "out" | "in"
    state: str = "ringing"              # ringing | connecting | active | ended
    started_at: float = 0.0
    muted: bool = False
    camera_on: bool = True
    quality: str = ""
    reason: str = ""
    backend: CallBackend | None = field(default=None, repr=False)
    offer: str = field(default="", repr=False)
    ring_task: asyncio.Task | None = field(default=None, repr=False)


def default_media_factory(ice: IceConfig, cb: MediaCallbacks) -> CallBackend:
    from ..media.devices import CvCamera, MicSource, SpeakerSink
    from ..media.webrtc import WebRtcBackend
    return WebRtcBackend(ice, cb, mic=MicSource(ice.audio_in), speaker=SpeakerSink(ice.audio_out), camera=CvCamera(ice.camera))


def calls_available() -> bool:
    try:
        import aiortc  # noqa: F401
        import av  # noqa: F401
        import cv2  # noqa: F401
        import sounddevice  # noqa: F401
        return True
    except Exception:
        return False


class CallsMixin(ChatEngine):
    def _init_extensions(self) -> None:
        super()._init_extensions()
        self.call: Call | None = None
        self.media_factory: Callable[[IceConfig, MediaCallbacks], CallBackend] = default_media_factory
        self.remote_video_sink: Callable[[Any], None] | None = None
        self.local_video_sink: Callable[[Any], None] | None = None
        self._invites: dict[str, list[float]] = {}

    # -- configuration réseau des appels ---------------------------------------------------------------
    def ice_config(self) -> IceConfig:
        s = self.settings
        return IceConfig(local_only=(s.call_mode == "local"), stun=[u.strip() for u in s.stun_servers.split(",") if u.strip()],
                         turn_url=s.turn_url.strip(), turn_user=s.turn_user, turn_pass=s.turn_pass, hide_ip=s.hide_ip,
                         audio_in=s.audio_in, audio_out=s.audio_out, camera=s.camera)

    def _callbacks(self, call: Call) -> MediaCallbacks:
        def state(st: str) -> None:
            if self.call is not call or call.state == "ended":
                return
            if st == "connected" and call.state != "active":
                call.state, call.started_at = "active", time.time()
                self.note(f"appel : connecté avec …{call.peer[-6:]}")
                self.events.emit("call_state", call=call)
            elif st == "failed":
                self.spawn(self._end_call(call, "connexion impossible (réseau ou pare-feu)", notify=True), "call-failed")
            elif st == "closed" and call.state == "active":
                self.spawn(self._end_call(call, "", notify=False), "call-closed")

        def quality(q: str, stats: dict) -> None:
            call.quality = q
            self.events.emit("call_quality", call=call, stats=stats)

        return MediaCallbacks(on_state=state, on_quality=quality,
                              on_remote_video=lambda f: self.remote_video_sink and self.remote_video_sink(f),
                              on_local_video=lambda f: self.local_video_sink and self.local_video_sink(f))

    async def _send_call(self, peer: str, inner: dict) -> str:
        pub = self.pub_of(peer)
        if pub is None:
            return "unknown"
        from ..crypto.e2e import seal
        try:
            return await self._transmit(peer, seal(self.identity, pub, inner), ephemeral=True)
        except Exception as e:  # aucun transport
            self.note(f"appel : signalisation impossible ({e})")
            return "unavailable"

    # -- appel sortant ----------------------------------------------------------------------------------
    async def start_call(self, peer: str, video: bool = False) -> Call:
        if not self.settings.calls_enabled:
            raise EngineError("Les appels sont désactivés dans les paramètres.")
        c = self.db.get_contact(peer)
        if not c or c["state"] != "accepted":
            raise EngineError("Vous ne pouvez appeler que vos contacts acceptés.")
        if self.call and self.call.state != "ended":
            raise EngineError("Un appel est déjà en cours.")
        if not (self.is_direct(peer) or self.server_online.get(peer)):
            raise EngineError("Ce contact n'est pas joignable pour le moment (hors ligne).")
        call = Call(new_mid(), peer, video, "out")
        self.call = call
        self.events.emit("call_state", call=call)
        try:
            call.backend = self.media_factory(self.ice_config(), self._callbacks(call))
            offer = await call.backend.create_offer(video)
        except CallsUnavailable as e:
            await self._end_call(call, str(e), notify=False, log_it=False)
            raise EngineError(str(e)) from e
        except Exception as e:
            await self._end_call(call, f"erreur média : {e}", notify=False, log_it=False)
            raise EngineError(f"Impossible de démarrer l'appel : {e}") from e
        status = await self._send_call(peer, {"t": "call_invite", "c": call.id, "v": video, "sdp": offer})
        if status != "delivered":
            reason = "Ce contact n'est pas joignable (hors ligne)." if status in ("offline", "queued") else f"Signalisation refusée ({status})."
            await self._end_call(call, reason, notify=False, log_it=False)
            raise EngineError(reason)
        call.ring_task = self.spawn(self._ring_timeout(call), "call-ring")
        self.note(f"appel : sonnerie vers …{peer[-6:]} ({'vidéo' if video else 'vocal'})")
        return call

    async def _ring_timeout(self, call: Call) -> None:
        await asyncio.sleep(RING_SECONDS)
        if self.call is call and call.state == "ringing":
            await self._end_call(call, "Sans réponse", notify=True, missed=True)

    # -- appel entrant ----------------------------------------------------------------------------------
    async def _on_call_invite(self, sender: str, inner: dict) -> None:
        from ..media.webrtc import valid_sdp
        cid, sdp, video = inner.get("c"), inner.get("sdp"), bool(inner.get("v"))
        if not isinstance(cid, str) or len(cid) > 64 or not valid_sdp(sdp):
            return
        now = time.time()
        recent = [t for t in self._invites.get(sender, []) if now - t < 60]
        if len(recent) >= MAX_INVITES_PER_MIN:
            return  # anti-harcèlement : on ignore silencieusement
        self._invites[sender] = recent + [now]
        reason = None
        if not self.settings.calls_enabled:
            reason = "unavailable"
        elif self.settings.status == "dnd":
            reason = "dnd"
        elif self.call and self.call.state != "ended":
            if self.call.peer == sender and self.call.direction == "out" and self.call.state == "ringing" and self.me > sender:
                await self._end_call(self.call, "", notify=False, log_it=False)  # appels croisés : le plus petit identifiant garde son appel
            else:
                reason = "busy"
        if reason:
            await self._send_call(sender, {"t": "call_reject", "c": cid, "r": reason})
            if reason in ("dnd", "busy"):
                self._call_log(sender, f"Appel {'vidéo ' if video else ''}manqué", missed=True)
            return
        call = Call(cid, sender, video, "in", offer=sdp)
        self.call = call
        call.ring_task = self.spawn(self._ring_timeout(call), "call-ring-in")
        self.note(f"appel : appel entrant de …{sender[-6:]} ({'vidéo' if video else 'vocal'})")
        self.events.emit("call_state", call=call)

    async def accept_call(self, video: bool | None = None) -> None:
        call = self.call
        if not call or call.direction != "in" or call.state != "ringing":
            raise EngineError("Aucun appel à décrocher.")
        use_video = call.video if video is None else (video and call.video)
        call.video = use_video
        if call.ring_task:
            call.ring_task.cancel()
        call.state = "connecting"
        self.events.emit("call_state", call=call)
        try:
            call.backend = self.media_factory(self.ice_config(), self._callbacks(call))
            answer = await call.backend.accept_offer(call.offer, use_video)
        except Exception as e:
            await self._send_call(call.peer, {"t": "call_reject", "c": call.id, "r": "unavailable"})
            await self._end_call(call, f"Impossible de décrocher : {e}", notify=False, log_it=False)
            raise EngineError(f"Impossible de décrocher : {e}") from e
        await self._send_call(call.peer, {"t": "call_answer", "c": call.id, "sdp": answer})

    async def reject_call(self, reason: str = "declined") -> None:
        call = self.call
        if call and call.direction == "in" and call.state == "ringing":
            await self._send_call(call.peer, {"t": "call_reject", "c": call.id, "r": reason})
            await self._end_call(call, "Appel refusé", notify=False, missed=False)

    async def hangup(self) -> None:
        call = self.call
        if call and call.state != "ended":
            await self._end_call(call, "", notify=True)

    # -- réception de la signalisation -------------------------------------------------------------------
    async def _dispatch_ext(self, sender: str, contact: Any, env: dict, inner: dict) -> None:
        t = inner["t"]
        if not t.startswith("call_"):
            return await super()._dispatch_ext(sender, contact, env, inner)
        call = self.call
        if t == "call_invite":
            return await self._on_call_invite(sender, inner)
        if not call or call.peer != sender or call.id != inner.get("c"):
            return  # signalisation d'un autre appel : ignorée
        if t == "call_answer" and call.direction == "out" and call.state == "ringing":
            from ..media.webrtc import valid_sdp
            if not valid_sdp(inner.get("sdp")):
                return
            if call.ring_task:
                call.ring_task.cancel()
            call.state = "connecting"
            self.events.emit("call_state", call=call)
            try:
                await call.backend.set_answer(inner["sdp"])
            except Exception as e:
                await self._end_call(call, f"Réponse invalide : {e}", notify=True)
        elif t == "call_reject" and call.direction == "out":
            why = {"busy": "Occupé", "dnd": "Ne pas déranger", "declined": "Appel refusé", "unavailable": "Indisponible"}.get(inner.get("r"), "Appel refusé")
            await self._end_call(call, why, notify=False)
        elif t == "call_end":
            await self._end_call(call, "", notify=False, missed=(call.direction == "in" and call.state == "ringing"))

    # -- fin d'appel ---------------------------------------------------------------------------------------
    async def _end_call(self, call: Call, reason: str, notify: bool, missed: bool = False, log_it: bool = True) -> None:
        if call.state == "ended":
            return
        prev = call.state
        call.state, call.reason = "ended", reason
        if call.ring_task and call.ring_task is not asyncio.current_task():  # ne pas s'annuler soi-même (délai de sonnerie)
            call.ring_task.cancel()
        if notify:
            await self._send_call(call.peer, {"t": "call_end", "c": call.id})
        if call.backend:
            try:
                await call.backend.close()
            except Exception:
                log.exception("fermeture média")
        if log_it:
            kind = "vidéo " if call.video else ""
            if missed or (prev == "ringing" and call.direction == "in" and not reason):
                self._call_log(call.peer, f"Appel {kind}manqué", missed=True, incoming=(call.direction == "in"))
            elif prev == "active":
                d = int(time.time() - call.started_at)
                self._call_log(call.peer, f"Appel {kind}terminé · {d // 60:02d}:{d % 60:02d}")
            elif reason:
                self._call_log(call.peer, f"Appel {kind}: {reason}")
        self.note(f"appel : terminé ({reason or 'normal'})")
        self.events.emit("call_state", call=call)
        if self.call is call:
            self.call = None

    def _call_log(self, peer: str, text: str, missed: bool = False, incoming: bool = True) -> None:
        self.db.add_message(mid=f"call-{new_mid()}", conv=peer, sender=peer, direction="in", body=text, ts=now_ms(),
                            status="received", kind="system")
        self.events.emit("conversation_update", conv=peer)
        if missed and incoming:
            self.events.emit("missed_call", peer=peer)

    # -- commandes pendant l'appel --------------------------------------------------------------------------
    def set_call_muted(self, muted: bool) -> None:
        if self.call and self.call.backend:
            self.call.muted = muted
            self.call.backend.set_muted(muted)
            self.events.emit("call_state", call=self.call)

    def set_call_camera(self, on: bool) -> None:
        if self.call and self.call.backend and self.call.video:
            self.call.camera_on = on
            self.call.backend.set_camera(on)
            self.events.emit("call_state", call=self.call)

    async def stop(self) -> None:
        if self.call and self.call.state != "ended":
            await self._end_call(self.call, "", notify=True)
        await super().stop()
