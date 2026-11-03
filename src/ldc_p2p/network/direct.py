"""Liens directs pair à pair (LAN, IPv6, port redirigé), authentifiés mutuellement.

Seuls les contacts acceptés et non bloqués peuvent établir un lien. L'authentification
signe les deux défis et les deux identifiants (pas de réflexion possible). Les enveloppes
qui circulent restent chiffrées de bout en bout exactement comme via le serveur.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Awaitable, Callable

from ..crypto.identity import Identity, PublicIdentity, b64, unb64
from .framing import FrameError, read_frame, write_frame

log = logging.getLogger("ldc.direct")

LookupFn = Callable[[str], PublicIdentity | None]
EnvFn = Callable[[str, dict], Awaitable[bool]]


def _sig_data(role: bytes, ch_first: bytes, ch_second: bytes, id_first: str, id_second: str) -> bytes:
    return b"ldc-link-v1|" + role + b"|" + ch_first + ch_second + id_first.encode() + b"|" + id_second.encode()


def local_addresses() -> list[str]:
    """Adresses IPv4 locales probables (sans envoyer aucun paquet)."""
    out: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # adresse de documentation : n'émet rien
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out


class DirectLink:
    def __init__(self, peer_id: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, on_env: EnvFn, on_close,
                 dialer_id: str = ""):
        self.peer_id, self.reader, self.writer = peer_id, reader, writer
        self.dialer_id = dialer_id
        self._on_env, self._on_close = on_env, on_close
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.closed = False
        self._q: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._read_loop(), name=f"link-{peer_id[-6:]}")
        self._worker = asyncio.create_task(self._env_worker(), name=f"link-w-{peer_id[-6:]}")

    async def send_env(self, env: dict, timeout: float = 8.0) -> str:
        if self.closed:
            raise ConnectionError("lien fermé")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[env["id"]] = fut
        try:
            async with self._lock:
                await write_frame(self.writer, {"t": "env", "env": env})
            await asyncio.wait_for(fut, timeout)
            return "delivered"
        except asyncio.TimeoutError as e:
            raise ConnectionError("le pair n'a pas confirmé") from e
        finally:
            self._pending.pop(env["id"], None)

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await asyncio.wait_for(read_frame(self.reader), 60)
                t = msg["t"]
                if t == "env" and isinstance(msg.get("env"), dict):
                    self._q.put_nowait(msg["env"])  # le traitement envoie des accusés : ne jamais bloquer la lecture
                elif t == "link_ack":
                    fut = self._pending.get(str(msg.get("mid")))
                    if fut and not fut.done():
                        fut.set_result(True)
                elif t == "ping":
                    async with self._lock:
                        await write_frame(self.writer, {"t": "pong"})
        except (asyncio.TimeoutError, ConnectionError, FrameError, OSError, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("Erreur de lien direct")
        finally:
            self.close()

    async def _env_worker(self) -> None:
        try:
            while True:
                env = await self._q.get()
                try:
                    ok = await self._on_env(self.peer_id, env)
                    if ok:
                        async with self._lock:
                            await write_frame(self.writer, {"t": "link_ack", "mid": env.get("id")})
                except (ConnectionError, OSError):
                    return
                except Exception:
                    log.exception("Erreur de traitement d'une enveloppe directe")
        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._worker.cancel()
            for f in self._pending.values():
                if not f.done():
                    f.set_exception(ConnectionError("lien fermé"))
            try:
                self.writer.close()
            except Exception:
                pass
            self._on_close(self)


class DirectManager:
    def __init__(self, identity: Identity, lookup: LookupFn, on_env: EnvFn, host: str = "0.0.0.0", port: int = 0,
                 max_inbound: int = 30):
        self.identity, self.lookup, self.on_env = identity, lookup, on_env
        self.host, self.port, self.max_inbound = host, port, max_inbound
        self.links: dict[str, DirectLink] = {}
        self._server: asyncio.base_events.Server | None = None
        self._inbound = 0

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._accept, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        for l in list(self.links.values()):
            l.close()

    def link_for(self, peer_id: str) -> DirectLink | None:
        l = self.links.get(peer_id)
        return l if l and not l.closed else None

    def _register(self, link: DirectLink) -> DirectLink:
        """Deux liens croisés : les deux côtés gardent celui ouvert par l'identifiant le plus petit."""
        old = self.links.get(link.peer_id)
        if old and not old.closed and old is not link and link.dialer_id > old.dialer_id:
            link.close()
            return old
        self.links[link.peer_id] = link
        if old and old is not link:
            old.close()
        return link

    def _unregister(self, link: DirectLink) -> None:
        if self.links.get(link.peer_id) is link:
            del self.links[link.peer_id]

    # -- côté écoute -----------------------------------------------------------------
    async def _accept(self, reader, writer) -> None:
        if self._inbound >= self.max_inbound:
            writer.close()
            return
        self._inbound += 1
        try:
            hello = await asyncio.wait_for(read_frame(reader, 4096), 5)
            peer = self.lookup(hello.get("id", "")) if hello["t"] == "dhello" else None
            if peer is None:
                raise ConnectionError("pair inconnu ou bloqué")
            ch_d = unb64(hello["ch"])
            ch_l = os.urandom(32)
            me = self.identity.id
            await write_frame(writer, {"t": "lhello", "id": me, "ch": b64(ch_l),
                                       "sig": b64(self.identity.sign(_sig_data(b"L", ch_d, ch_l, peer.id, me)))})
            auth = await asyncio.wait_for(read_frame(reader, 4096), 5)
            if auth["t"] != "dauth" or not peer.verify(unb64(auth["sig"]), _sig_data(b"D", ch_l, ch_d, me, peer.id)):
                raise ConnectionError("authentification du pair refusée")
            if self.lookup(peer.id) is None:  # bloqué pendant la poignée de main
                raise ConnectionError("pair bloqué")
            self._register(DirectLink(peer.id, reader, writer, self.on_env, self._unregister, dialer_id=peer.id))
        except (asyncio.TimeoutError, ConnectionError, FrameError, KeyError, ValueError, TypeError, OSError):
            writer.close()
        finally:
            self._inbound -= 1

    # -- côté appelant ------------------------------------------------------------------
    async def connect(self, peer: PublicIdentity, candidates: list[tuple[str, int]], timeout: float = 4.0) -> bool:
        if self.link_for(peer.id):
            return True
        tasks = [asyncio.create_task(self._dial(peer, h, p, timeout)) for h, p in candidates[:8]]
        if not tasks:
            return False
        try:
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if any(t.result() for t in done if not t.cancelled() and t.exception() is None):
                    return True
            return False
        finally:
            for t in tasks:
                t.cancel()

    async def _dial(self, peer: PublicIdentity, host: str, port: int, timeout: float) -> bool:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        except (asyncio.TimeoutError, OSError):
            return False
        try:
            ch_d, me = os.urandom(32), self.identity.id
            await write_frame(writer, {"t": "dhello", "id": me, "ch": b64(ch_d)})
            lh = await asyncio.wait_for(read_frame(reader, 4096), timeout)
            ch_l = unb64(lh["ch"])
            if lh["t"] != "lhello" or lh["id"] != peer.id or not peer.verify(
                    unb64(lh["sig"]), _sig_data(b"L", ch_d, ch_l, me, peer.id)):
                raise ConnectionError("le pair ne prouve pas son identité")
            await write_frame(writer, {"t": "dauth", "sig": b64(self.identity.sign(_sig_data(b"D", ch_l, ch_d, peer.id, me)))})
            link = self._register(DirectLink(peer.id, reader, writer, self.on_env, self._unregister, dialer_id=me))
            return not link.closed
        except (asyncio.TimeoutError, ConnectionError, FrameError, KeyError, ValueError, TypeError, OSError):
            writer.close()
            return False
