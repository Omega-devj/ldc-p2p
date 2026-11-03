import asyncio

import pytest

from ldc_p2p.crypto import e2e
from ldc_p2p.crypto.identity import Identity
from ldc_p2p.network.direct import DirectManager
from ldc_p2p.network.framing import FrameError, encode_frame, read_frame
from ldc_p2p.network.relay_client import ChannelUnavailable, RelayClient
from ldc_p2p.server.relay_server import Limits, RelayServer


async def wait_for(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition non atteinte")


# ---------------------------------------------------------------- framing
async def test_frame_fragmented_delivery():
    data = encode_frame({"t": "x", "payload": "é" * 5000})
    r = asyncio.StreamReader()
    task = asyncio.create_task(read_frame(r))
    for i in range(0, len(data), 7):  # 7 octets à la fois
        r.feed_data(data[i:i + 7])
        await asyncio.sleep(0)
    assert (await task)["payload"] == "é" * 5000


async def test_two_frames_in_one_chunk():
    r = asyncio.StreamReader()
    r.feed_data(encode_frame({"t": "a"}) + encode_frame({"t": "b"}))
    assert (await read_frame(r))["t"] == "a" and (await read_frame(r))["t"] == "b"


async def test_oversize_and_garbage_rejected():
    r = asyncio.StreamReader()
    r.feed_data((10 ** 9).to_bytes(4, "big"))
    with pytest.raises(FrameError):
        await read_frame(r)
    r2 = asyncio.StreamReader()
    r2.feed_data((5).to_bytes(4, "big") + b"nope!")
    with pytest.raises(FrameError):
        await read_frame(r2)
    r3 = asyncio.StreamReader()
    r3.feed_data(b"\x00\x00")
    r3.feed_eof()
    with pytest.raises(ConnectionError):
        await read_frame(r3)


# ---------------------------------------------------------------- serveur
@pytest.fixture
async def server():
    s = RelayServer("127.0.0.1", 0)
    await s.start()
    yield s
    await s.stop()


class Client:
    def __init__(self, server, name):
        self.ident = Identity.generate(name)
        self.inbox: list[dict] = []
        self.presence: dict = {}
        self.rc = RelayClient(self.ident, "127.0.0.1", server.port, on_deliver=self._deliver,
                              on_presence=self.presence.update, ping_interval=5)

    async def _deliver(self, env):
        self.inbox.append(env)
        return True

    async def up(self):
        self.rc.start()
        await wait_for(lambda: self.rc.connected)
        return self

    async def down(self):
        await self.rc.stop()


async def test_online_delivery_and_mailack(server):
    a, b = await Client(server, "A").up(), await Client(server, "B").up()
    env = e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": "salut"})
    assert await a.rc.send_env(b.ident.id, env) == "delivered"
    await wait_for(lambda: len(b.inbox) == 1)
    assert e2e.open_envelope(b.inbox[0], b.ident, a.ident.public)["text"] == "salut"
    await wait_for(lambda: server.mailbox.count(b.ident.id) == 0)  # supprimé après confirmation
    await a.down(), await b.down()


async def test_offline_queue_then_flush_on_connect(server):
    a = await Client(server, "A").up()
    b = Client(server, "B")  # hors ligne
    envs = [e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": str(i)}) for i in range(3)]
    for e in envs:
        assert await a.rc.send_env(b.ident.id, e) == "queued"
    assert server.mailbox.count(b.ident.id) == 3
    await b.up()
    await wait_for(lambda: len(b.inbox) == 3)
    assert [e2e.open_envelope(e, b.ident, a.ident.public)["text"] for e in b.inbox] == ["0", "1", "2"]
    await wait_for(lambda: server.mailbox.count(b.ident.id) == 0)
    await a.down(), await b.down()


async def test_unacked_envelope_is_redelivered(server):
    """Si le client plante avant de confirmer, le serveur redonne l'enveloppe à la reconnexion."""
    a = await Client(server, "A").up()
    b = Client(server, "B")

    async def refuse(env):
        return False  # traitement échoué : pas de mailack

    b.rc.on_deliver = refuse
    env = e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": "x"})
    await a.rc.send_env(b.ident.id, env)
    await b.up()
    await asyncio.sleep(0.3)
    assert server.mailbox.count(b.ident.id) == 1
    await b.down()
    b2 = Client(server, "B")
    b2.ident = b.ident
    b2.rc.identity = b.ident
    await b2.up()
    await wait_for(lambda: len(b2.inbox) == 1)
    await a.down(), await b2.down()


async def test_duplicate_mid_not_stored_twice(server):
    a = await Client(server, "A").up()
    b = Client(server, "B")
    env = e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": "x"})
    await a.rc.send_env(b.ident.id, env)
    await a.rc.send_env(b.ident.id, env)
    assert server.mailbox.count(b.ident.id) == 1
    await a.down()


async def test_server_rejects_spoofed_sender(server):
    a, b, eve = await Client(server, "A").up(), await Client(server, "B").up(), await Client(server, "E").up()
    env = e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": "faux"})  # écrit au nom d'A
    assert (await eve.rc.send_env(b.ident.id, env)).startswith("rejected")
    await asyncio.sleep(0.1)
    assert b.inbox == []
    await a.down(), await b.down(), await eve.down()


async def test_ephemeral_not_stored_when_offline(server):
    a = await Client(server, "A").up()
    b = Client(server, "B")
    env = e2e.seal(a.ident, b.ident.public, {"t": "typing"})
    assert await a.rc.send_env(b.ident.id, env, ephemeral=True) == "offline"
    assert server.mailbox.count(b.ident.id) == 0
    await a.down()


async def test_bad_auth_refused(server):
    r, w = await asyncio.open_connection("127.0.0.1", server.port)
    hello = await read_frame(r)
    ident = Identity.generate("X")
    from ldc_p2p.crypto.identity import b64
    from ldc_p2p.network.framing import write_frame
    await write_frame(w, {"t": "auth", "id": ident.id, "sk": b64(ident.public.sign_pk), "bk": b64(ident.public.box_pk),
                          "sig": b64(ident.sign(b"mauvais challenge"))})
    reply = await read_frame(r)
    assert reply["t"] == "error"
    w.close()


async def test_id_not_matching_keys_refused(server):
    from ldc_p2p.crypto.identity import b64
    from ldc_p2p.network.framing import write_frame
    r, w = await asyncio.open_connection("127.0.0.1", server.port)
    hello = await read_frame(r)
    victim, eve = Identity.generate("V"), Identity.generate("E")
    from ldc_p2p.crypto.identity import unb64
    ch = unb64(hello["challenge"])
    await write_frame(w, {"t": "auth", "id": victim.id, "sk": b64(eve.public.sign_pk), "bk": b64(eve.public.box_pk),
                          "sig": b64(eve.sign(b"ldc-auth-v1\0" + ch + victim.id.encode()))})
    assert (await read_frame(r))["t"] == "error"
    w.close()


async def test_session_replaced(server):
    a1 = await Client(server, "A").up()
    a2 = Client(server, "A")
    a2.ident = a1.ident
    a2.rc.identity = a1.ident
    await a2.up()
    await wait_for(lambda: a1.rc.state == "replaced")
    assert a2.rc.connected
    await a1.down(), await a2.down()


async def test_presence_watch_and_invisible(server):
    a, b = await Client(server, "A").up(), Client(server, "B")
    await a.rc.watch([b.ident.id])
    await wait_for(lambda: a.presence.get(b.ident.id) is False)
    await b.up()
    await wait_for(lambda: a.presence.get(b.ident.id) is True)
    await b.rc.set_visible(False)
    await wait_for(lambda: a.presence.get(b.ident.id) is False)
    await b.rc.set_visible(True)
    await wait_for(lambda: a.presence.get(b.ident.id) is True)
    await b.down()
    await wait_for(lambda: a.presence.get(b.ident.id) is False)
    await a.down()


async def test_rate_limit_and_mailbox_cap():
    s = RelayServer("127.0.0.1", 0, limits=Limits(rate_capacity=5, rate_refill=0.001, mailbox_max_msgs=3))
    await s.start()
    try:
        a = await Client(s, "A").up()
        b = Client(s, "B")
        results = []
        for i in range(8):
            results.append(await a.rc.send_env(b.ident.id, e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": str(i)})))
        assert results[:3] == ["queued"] * 3
        assert any(r == "rejected:mailbox_full" for r in results)
        assert any(r == "rejected:rate" for r in results)
        await a.down()
    finally:
        await s.stop()


async def test_client_reconnects_after_server_restart():
    s = RelayServer("127.0.0.1", 0)
    await s.start()
    port = s.port
    a = Client(s, "A")
    a.rc.backoff_max = 0.5
    await a.up()
    await s.stop()
    await wait_for(lambda: not a.rc.connected)
    s2 = RelayServer("127.0.0.1", port)
    await s2.start()
    try:
        await wait_for(lambda: a.rc.connected, timeout=8)
    finally:
        await a.down()
        await s2.stop()


async def test_send_without_connection_raises():
    ident = Identity.generate("A")
    rc = RelayClient(ident, "127.0.0.1", 1)
    env = e2e.seal(ident, Identity.generate("B").public, {"t": "x"})
    with pytest.raises(ChannelUnavailable):
        await rc.send_env("LDC-AAAAAA-BBBBBB-CCCCCC-DDDDDD", env)


# ---------------------------------------------------------------- liens directs
class Peer:
    def __init__(self, name):
        self.ident = Identity.generate(name)
        self.known: dict = {}
        self.got: list = []
        self.mgr = DirectManager(self.ident, lambda i: self.known.get(i), self._on_env, "127.0.0.1", 0)

    async def _on_env(self, peer_id, env):
        self.got.append((peer_id, env))
        return True


async def test_direct_link_mutual_auth_and_delivery():
    a, b = Peer("A"), Peer("B")
    a.known[b.ident.id], b.known[a.ident.id] = b.ident.public, a.ident.public
    await a.mgr.start(), await b.mgr.start()
    assert await a.mgr.connect(b.ident.public, [("127.0.0.1", 1), ("127.0.0.1", b.mgr.port)])
    await wait_for(lambda: b.mgr.link_for(a.ident.id) is not None)
    env = e2e.seal(a.ident, b.ident.public, {"t": "msg", "text": "direct"})
    assert await a.mgr.link_for(b.ident.id).send_env(env) == "delivered"
    assert b.got[0][0] == a.ident.id and b.got[0][1]["id"] == env["id"]
    env2 = e2e.seal(b.ident, a.ident.public, {"t": "msg", "text": "retour"})
    assert await b.mgr.link_for(a.ident.id).send_env(env2) == "delivered"
    await a.mgr.stop(), await b.mgr.stop()


async def test_direct_link_refuses_unknown_peer():
    a, b = Peer("A"), Peer("B")
    a.known[b.ident.id] = b.ident.public  # B ne connaît pas A
    await a.mgr.start(), await b.mgr.start()
    assert not await a.mgr.connect(b.ident.public, [("127.0.0.1", b.mgr.port)])
    assert b.mgr.link_for(a.ident.id) is None
    await a.mgr.stop(), await b.mgr.stop()


async def test_direct_link_detects_impostor_listener():
    """Eve écoute à la place de B : elle ne peut pas prouver l'identité de B."""
    a, eve, b = Peer("A"), Peer("E"), Peer("B")
    a.known[b.ident.id] = b.ident.public
    eve.known[a.ident.id] = a.ident.public
    await eve.mgr.start()
    assert not await a.mgr.connect(b.ident.public, [("127.0.0.1", eve.mgr.port)])
    assert a.mgr.link_for(b.ident.id) is None
    await eve.mgr.stop()


async def test_direct_link_detects_impostor_dialer():
    """Eve se présente avec l'ID d'A mais signe avec sa propre clé."""
    a, b, eve = Peer("A"), Peer("B"), Peer("E")
    b.known[a.ident.id] = a.ident.public
    await b.mgr.start()
    eve.ident = Identity.generate("E")
    # Eve annonce l'identifiant d'A : on triche en fournissant un faux "lookup" côté Eve
    fake_a = a.ident.public
    eve.mgr.identity = eve.ident
    eve_id_real = eve.ident.id
    import ldc_p2p.network.direct as d
    from ldc_p2p.crypto.identity import b64, unb64
    from ldc_p2p.network.framing import read_frame, write_frame
    r, w = await asyncio.open_connection("127.0.0.1", b.mgr.port)
    await write_frame(w, {"t": "dhello", "id": a.ident.id, "ch": b64(b"\x01" * 32)})
    lh = await read_frame(r)
    ch_l = unb64(lh["ch"])
    forged = eve.ident.sign(d._sig_data(b"D", ch_l, b"\x01" * 32, b.ident.id, a.ident.id))
    await write_frame(w, {"t": "dauth", "sig": b64(forged)})
    await asyncio.sleep(0.2)
    assert b.mgr.link_for(a.ident.id) is None
    w.close()
    await b.mgr.stop()
