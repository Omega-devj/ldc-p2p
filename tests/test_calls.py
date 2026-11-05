"""Appels : machine à états et signalisation (backend factice) + vrai WebRTC en boucle locale (aiortc, médias synthétiques)."""
import asyncio

import numpy as np
import pytest

from conftest import befriend, wait_for
from ldc_p2p.engine import EngineError
from ldc_p2p.media.base import CallBackend, IceConfig, MediaCallbacks


class FakeBackend(CallBackend):
    instances: list = []

    def __init__(self, ice, cb):
        self.ice, self.cb, self.closed, self.muted, self.cam = ice, cb, False, False, True
        FakeBackend.instances.append(self)

    async def create_offer(self, video):
        self.video = video
        return "v=0\r\ns=fake-offer"

    async def accept_offer(self, sdp, video):
        asyncio.get_running_loop().call_later(0.05, self.cb.on_state, "connected")
        return "v=0\r\ns=fake-answer"

    async def set_answer(self, sdp):
        asyncio.get_running_loop().call_later(0.05, self.cb.on_state, "connected")

    def set_muted(self, m):
        self.muted = m

    def set_camera(self, on):
        self.cam = on

    async def close(self):
        self.closed = True


async def pair(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    await befriend(a, b)
    for n in (a, b):
        n.engine.media_factory = FakeBackend
    await wait_for(lambda: a.engine.server_online.get(b.identity.id) and b.engine.server_online.get(a.identity.id))
    return a, b


def logs(node, conv):
    return [m["body"] for m in node.engine.db.list_messages(conv) if m["kind"] == "system"]


async def test_call_accept_mute_hangup_and_logs(make_node, server):
    a, b = await pair(make_node, server)
    call = await a.engine.start_call(b.identity.id, video=True)
    await wait_for(lambda: b.engine.call and b.engine.call.state == "ringing")
    assert b.engine.call.video and b.engine.call.direction == "in"
    await b.engine.accept_call()
    await wait_for(lambda: a.engine.call.state == "active" and b.engine.call.state == "active")
    a.engine.set_call_muted(True)
    assert call.backend.muted is True
    await a.engine.hangup()
    await wait_for(lambda: b.engine.call is None and a.engine.call is None)
    assert any(t.startswith("Appel vidéo terminé") for t in logs(a, b.identity.id))
    assert any(t.startswith("Appel vidéo terminé") for t in logs(b, a.identity.id))
    assert all(i.closed for i in FakeBackend.instances[-2:])


async def test_reject_busy_dnd_offline_and_unaccepted(make_node, server):
    a, b = await pair(make_node, server)
    await a.engine.start_call(b.identity.id)
    await wait_for(lambda: b.engine.call and b.engine.call.state == "ringing")
    await b.engine.reject_call()
    await wait_for(lambda: a.engine.call is None)
    assert any("refusé" in t for t in logs(a, b.identity.id))
    # occupé : Bob en appel avec Alice, un 3ᵉ appel entrant est refusé automatiquement
    await a.engine.start_call(b.identity.id)
    await wait_for(lambda: b.engine.call and b.engine.call.state == "ringing")
    await b.engine.accept_call()
    await wait_for(lambda: b.engine.call.state == "active")
    with pytest.raises(EngineError):
        await a.engine.start_call(b.identity.id)  # déjà en appel
    await a.engine.hangup()
    await wait_for(lambda: b.engine.call is None)
    # ne pas déranger
    await b.engine.set_status("dnd")
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "dnd")
    await a.engine.start_call(b.identity.id)
    await wait_for(lambda: a.engine.call is None)
    assert any("manqué" in t for t in logs(b, a.identity.id))
    # hors ligne
    await b.stop()
    await wait_for(lambda: not a.engine.server_online.get(b.identity.id))
    with pytest.raises(EngineError):
        await a.engine.start_call(b.identity.id)


async def test_missed_call_timeout_and_cancel(make_node, server, monkeypatch):
    import ldc_p2p.engine.calls as calls
    monkeypatch.setattr(calls, "RING_SECONDS", 0.4)
    a, b = await pair(make_node, server)
    await a.engine.start_call(b.identity.id)
    await wait_for(lambda: a.engine.call is None and b.engine.call is None, 5)
    assert any("manqué" in t for t in logs(b, a.identity.id))
    monkeypatch.setattr(calls, "RING_SECONDS", 30)
    await a.engine.start_call(b.identity.id)
    await wait_for(lambda: b.engine.call is not None)
    await a.engine.hangup()  # l'appelant raccroche avant la réponse
    await wait_for(lambda: b.engine.call is None)
    assert sum("manqué" in t for t in logs(b, a.identity.id)) == 2


async def test_invite_rate_limit_and_forged_signaling(make_node, server):
    from ldc_p2p.crypto import e2e
    a, b = await pair(make_node, server)
    c = await make_node("carol", server.port)
    # Carol (non contact) ne peut pas faire sonner Bob
    env = e2e.seal(c.identity, b.identity.public, {"t": "call_invite", "c": "x1", "v": False, "sdp": "v=0\r\ns=x"})
    await b.engine._handle_envelope(env, "relay")
    assert b.engine.call is None
    # une réponse/fin d'un appel inexistant est ignorée
    for t in ("call_answer", "call_end", "call_reject"):
        await b.engine._handle_envelope(e2e.seal(a.identity, b.identity.public, {"t": t, "c": "zzz", "sdp": "v=0\r\n"}), "relay")
    assert b.engine.call is None
    # limite de débit : 3 invitations par minute et par contact
    for i in range(6):
        await b.engine._handle_envelope(e2e.seal(a.identity, b.identity.public, {"t": "call_invite", "c": f"c{i}", "v": False, "sdp": "v=0\r\ns=x"}), "relay")
        if b.engine.call:
            await b.engine.reject_call()
    assert len(b.engine._invites[a.identity.id]) == 3


async def test_ice_config_modes(make_node, server):
    a = await make_node("alice", server.port)
    s = a.engine.settings
    s.call_mode, s.stun_servers, s.turn_url, s.hide_ip = "local", "stun:a:1, stun:b:2", "turn:t:3478", True
    ice = a.engine.ice_config()
    assert ice.local_only and ice.stun == ["stun:a:1", "stun:b:2"] and ice.turn_url == "turn:t:3478" and ice.hide_ip


# ------------------------------------------------------------------ vrai WebRTC (boucle locale)
aiortc = pytest.importorskip("aiortc")


class SineMic:
    async def read(self):
        await asyncio.sleep(0.02)
        t = np.arange(960)
        return (np.sin(2 * np.pi * 440 * t / 48000) * 8000).astype(np.int16)


class CountSpeaker:
    def __init__(self):
        self.samples = 0

    def write(self, s):
        self.samples += len(s)


class ColorCam:
    async def read(self):
        return np.full((480, 640, 3), (10, 200, 90), dtype=np.uint8)


def test_sdp_helpers():
    from ldc_p2p.media.base import CallsUnavailable
    from ldc_p2p.media.webrtc import filter_sdp, valid_sdp
    assert valid_sdp("v=0\r\n") and not valid_sdp("hello") and not valid_sdp(None) and not valid_sdp("v=0" + "x" * 70000)
    sdp = "v=0\r\na=candidate:1 1 udp 1 192.168.1.5 5000 typ host\r\na=candidate:2 1 udp 1 1.2.3.4 6000 typ relay\r\n"
    assert "192.168.1.5" not in filter_sdp(sdp, True) and "1.2.3.4" in filter_sdp(sdp, True)
    assert "192.168.1.5" in filter_sdp(sdp, False)
    with pytest.raises(CallsUnavailable):
        filter_sdp("v=0\r\na=candidate:1 1 udp 1 192.168.1.5 5000 typ host\r\n", True)


async def test_real_webrtc_audio_and_video_loopback_without_servers():
    """Deux vrais pairs WebRTC, AUCUN serveur (candidats locaux uniquement), médias synthétiques."""
    from ldc_p2p.media.webrtc import WebRtcBackend
    states, remote_frames = {"a": [], "b": []}, []
    spk_a, spk_b = CountSpeaker(), CountSpeaker()
    ice = IceConfig(local_only=True)
    a = WebRtcBackend(ice, MediaCallbacks(on_state=states["a"].append, on_remote_video=remote_frames.append), SineMic(), spk_a, ColorCam())
    b = WebRtcBackend(ice, MediaCallbacks(on_state=states["b"].append, on_remote_video=lambda f: None), SineMic(), spk_b, ColorCam())
    try:
        offer = await a.create_offer(video=True)
        assert offer.startswith("v=0") and "m=audio" in offer and "m=video" in offer
        answer = await b.accept_offer(offer, True)
        await a.set_answer(answer)
        for _ in range(150):
            if "connected" in states["a"] and "connected" in states["b"] and spk_a.samples > 9600 and spk_b.samples > 9600 and remote_frames:
                break
            await asyncio.sleep(0.1)
        assert "connected" in states["a"] and "connected" in states["b"]
        assert spk_a.samples > 9600 and spk_b.samples > 9600, "l'audio doit circuler dans les deux sens"
        assert remote_frames and remote_frames[0].shape == (480, 640, 3)
        assert abs(int(remote_frames[0][240, 320, 1]) - 200) < 40, "la vidéo reçue doit correspondre à celle envoyée"
        a.set_muted(True)
        a.set_camera(False)
    finally:
        await a.close()
        await b.close()


async def test_lan_discovery_finds_only_contacts_without_server():
    from ldc_p2p.network.lan import LanDiscovery, lan_tag
    found = []
    port = 47999
    a = LanDiscovery("LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA", 5001, lambda: ["LDC-BBBBBB-BBBBBB-BBBBBB-BBBBBB"], lambda *x: None, port, ["127.0.0.1"], interval=0.2)
    b = LanDiscovery("LDC-BBBBBB-BBBBBB-BBBBBB-BBBBBB", 5002, lambda: ["LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA"], lambda *x: found.append(x), port, ["127.0.0.1"], interval=0.2)
    stranger = LanDiscovery("LDC-CCCCCC-CCCCCC-CCCCCC-CCCCCC", 5003, lambda: ["LDC-ZZZZZZ-ZZZZZZ-ZZZZZZ-ZZZZZZ"], lambda *x: found.append(("STRANGER",) + x), port, ["127.0.0.1"], interval=0.2)
    try:
        await b.start()
        await a.start() if False else None
        # a et b partagent le port : on émet depuis a sans l'écouter (SO_REUSEADDR) ; b doit reconnaître a (contact) et ignorer stranger
        a.transport = b.transport
        a.my_id = "LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA"
        await wait_for(lambda: True)
        a._task = asyncio.create_task(a._announce_loop())
        stranger.transport = b.transport
        stranger._task = asyncio.create_task(stranger._announce_loop())
        await wait_for(lambda: any(f[0] == "LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA" for f in found if f[0] != "STRANGER"), 5)
        assert all(f[0] == "LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA" for f in found), "un inconnu ne doit jamais être reconnu"
        assert lan_tag("LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA", 1) != lan_tag("LDC-AAAAAA-AAAAAA-AAAAAA-AAAAAA", 2)  # étiquette rotative
    finally:
        a._task.cancel()
        stranger._task.cancel()
        await b.stop()
