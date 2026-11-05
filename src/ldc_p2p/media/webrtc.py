"""Backend WebRTC (aiortc) : audio Opus, vidéo VP8, DTLS-SRTP, ICE avec ou sans serveurs STUN/TURN.

Sécurité : l'empreinte DTLS est dans le SDP, transporté dans une enveloppe signée et chiffrée de bout en bout :
les médias sont donc liés cryptographiquement à l'identité du contact (pas d'attaque de l'homme du milieu).
"""
from __future__ import annotations

import asyncio
import logging
from fractions import Fraction

from .base import CallBackend, CallsUnavailable, IceConfig, MediaCallbacks
from .devices import FRAME, RATE, VIDEO_SIZE

log = logging.getLogger("ldc.webrtc")
MAX_SDP = 64 * 1024


def valid_sdp(sdp: object) -> bool:
    return isinstance(sdp, str) and sdp.startswith("v=0") and len(sdp) <= MAX_SDP


def filter_sdp(sdp: str, relay_only: bool) -> str:
    """Mode « masquer mon IP » : ne garde que les candidats de type relais (adresse du serveur TURN)."""
    if not relay_only:
        return sdp
    out, kept = [], 0
    for ln in sdp.splitlines():
        if ln.startswith("a=candidate:") and " typ relay" not in ln:
            continue
        kept += ln.startswith("a=candidate:")
        out.append(ln)
    if not kept:
        raise CallsUnavailable("« Masquer mon adresse IP » exige un serveur TURN : aucun candidat relais disponible.")
    return "\r\n".join(out) + "\r\n"


def _imports():
    try:
        import av  # noqa: F401
        import numpy  # noqa: F401
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription  # noqa: F401
        return True
    except Exception as e:
        raise CallsUnavailable("Composants d'appel manquants (aiortc). Installez-les depuis Paramètres › Appels.") from e


class WebRtcBackend(CallBackend):
    def __init__(self, ice: IceConfig, cb: MediaCallbacks, mic=None, speaker=None, camera=None):
        _imports()
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection
        self.ice, self.cb = ice, cb
        self.mic, self.speaker, self.camera = mic, speaker, camera
        self.muted = False
        self.cam_on = True
        self._tasks: list[asyncio.Task] = []
        self._closed = False
        servers = []
        if not ice.local_only:                       # « sans serveur » = aucune entrée ICE
            servers += [RTCIceServer(urls=u) for u in ice.stun if u]
            if ice.turn_url:
                servers.append(RTCIceServer(urls=ice.turn_url, username=ice.turn_user or None, credential=ice.turn_pass or None))
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        self.pc.on("connectionstatechange", self._on_conn)
        self.pc.on("track", self._on_track)

    # -- pistes locales -----------------------------------------------------------------------------
    def _make_audio_track(self):
        import av
        import numpy as np
        from aiortc import MediaStreamTrack
        backend = self

        class MicTrack(MediaStreamTrack):
            kind = "audio"

            def __init__(self):
                super().__init__()
                self._pts = 0

            async def recv(self):
                data = await backend.mic.read()
                if backend.muted:
                    data = np.zeros(FRAME, dtype=np.int16)
                frame = av.AudioFrame.from_ndarray(data.reshape(1, -1), format="s16", layout="mono")
                frame.sample_rate = RATE
                frame.pts = self._pts
                frame.time_base = Fraction(1, RATE)
                self._pts += FRAME
                return frame
        return MicTrack()

    def _make_video_track(self):
        import av
        import numpy as np
        from aiortc import VideoStreamTrack
        backend = self

        class CamTrack(VideoStreamTrack):
            def __init__(self):
                super().__init__()
                self._n = 0

            async def recv(self):
                pts, tb = await self.next_timestamp()
                arr = await backend.camera.read() if backend.cam_on else None
                if arr is None:
                    arr = np.zeros((VIDEO_SIZE[1], VIDEO_SIZE[0], 3), dtype=np.uint8)
                self._n += 1
                if self._n % 3 == 0 and backend.cam_on:
                    backend.cb.on_local_video(arr)
                frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
                frame.pts, frame.time_base = pts, tb
                return frame
        return CamTrack()

    async def _start_io(self, video: bool) -> None:
        if self.mic is not None and hasattr(self.mic, "start"):
            await self.mic.start()
        if self.speaker is not None and hasattr(self.speaker, "start"):
            await self.speaker.start()
        if video and self.camera is not None and hasattr(self.camera, "start"):
            await self.camera.start()

    # -- signalisation --------------------------------------------------------------------------------
    async def create_offer(self, video: bool) -> str:
        self.video = video
        await self._start_io(video)
        self.pc.addTrack(self._make_audio_track())
        if video:
            self.pc.addTrack(self._make_video_track())
        await self.pc.setLocalDescription(await self.pc.createOffer())
        return filter_sdp(self.pc.localDescription.sdp, self.ice.hide_ip)

    async def accept_offer(self, sdp: str, video: bool) -> str:
        from aiortc import RTCSessionDescription
        if not valid_sdp(sdp):
            raise CallsUnavailable("Offre d'appel invalide")
        self.video = video
        await self._start_io(video)
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        self.pc.addTrack(self._make_audio_track())
        if video:
            self.pc.addTrack(self._make_video_track())
        await self.pc.setLocalDescription(await self.pc.createAnswer())
        return filter_sdp(self.pc.localDescription.sdp, self.ice.hide_ip)

    async def set_answer(self, sdp: str) -> None:
        from aiortc import RTCSessionDescription
        if not valid_sdp(sdp):
            raise CallsUnavailable("Réponse d'appel invalide")
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))

    # -- pistes distantes ---------------------------------------------------------------------------------
    def _on_conn(self) -> None:
        st = self.pc.connectionState
        if st == "connected":
            self._tasks.append(asyncio.ensure_future(self._stats_loop()))
        self.cb.on_state({"new": "connecting", "connecting": "connecting", "connected": "connected",
                          "failed": "failed", "closed": "closed", "disconnected": "connecting"}.get(st, st))

    def _on_track(self, track) -> None:
        self._tasks.append(asyncio.ensure_future(self._consume_audio(track) if track.kind == "audio" else self._consume_video(track)))

    async def _consume_audio(self, track) -> None:
        import av
        import numpy as np
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        try:
            while not self._closed:
                frame = await track.recv()
                for f in resampler.resample(frame):
                    samples = f.to_ndarray().reshape(-1).astype(np.int16)
                    if self.speaker is not None:
                        self.speaker.write(samples)
        except Exception as e:  # fin de piste
            log.debug("fin audio distant: %r", e)

    async def _consume_video(self, track) -> None:
        try:
            while not self._closed:
                frame = await track.recv()
                self.cb.on_remote_video(frame.to_ndarray(format="rgb24"))
        except Exception as e:
            log.debug("fin vidéo distante: %r", e)

    async def _stats_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(2)
            try:
                rep = await self.pc.getStats()
                rtt, lost, total = 0.0, 0, 0
                for r in rep.values():
                    if r.type == "remote-inbound-rtp":
                        rtt = max(rtt, getattr(r, "roundTripTime", 0) or 0)
                    elif r.type == "inbound-rtp":
                        lost += getattr(r, "packetsLost", 0) or 0
                        total += (getattr(r, "packetsReceived", 0) or 0) + (getattr(r, "packetsLost", 0) or 0)
                loss = lost / total if total else 0.0
                q = "poor" if (loss > 0.10 or rtt > 0.6) else ("medium" if (loss > 0.03 or rtt > 0.3) else "good")
                self.cb.on_quality(q, {"rtt_ms": int(rtt * 1000), "loss": round(loss, 3)})
            except Exception:
                return

    # -- commandes --------------------------------------------------------------------------------------------
    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def set_camera(self, on: bool) -> None:
        self.cam_on = on

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for t in self._tasks:
            t.cancel()
        try:
            await self.pc.close()
        except Exception:
            pass
        for dev in (self.mic, self.speaker, self.camera):
            if dev is not None and hasattr(dev, "stop"):
                try:
                    await dev.stop()
                except Exception:
                    pass
