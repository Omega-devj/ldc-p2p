"""Connexion persistante au serveur de rendez-vous/relais, avec reconnexion automatique."""
from __future__ import annotations

import asyncio
import logging
import random
import ssl
from typing import Awaitable, Callable

from ..crypto.identity import Identity, b64, unb64
from .framing import FrameError, read_frame, write_frame

log = logging.getLogger("ldc.relay")


class ChannelUnavailable(Exception):
    pass


class RelayClient:
    def __init__(self, identity: Identity, host: str, port: int, *, tls: bool = False,
                 ssl_ctx: ssl.SSLContext | None = None, visible: bool = True, token: str = "",
                 on_deliver: Callable[[dict], Awaitable[bool]] | None = None,
                 on_presence: Callable[[dict], None] | None = None,
                 on_state: Callable[[str, dict], None] | None = None,
                 ping_interval: float = 25.0, backoff_max: float = 30.0):
        self.identity, self.host, self.port = identity, host, port
        self.tls = tls
        self._ssl_ctx = ssl_ctx
        self.visible = visible
        self.token = token
        self.on_deliver, self.on_presence, self.on_state = on_deliver, on_presence, on_state
        self.ping_interval, self.backoff_max = ping_interval, backoff_max
        self.state = "disconnected"
        self.observed_ip: str | None = None
        self.last_error: str | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._watch_ids: list[str] = []
        self._wake = asyncio.Event()
        self._deliver_q: asyncio.Queue = asyncio.Queue()

    # -- cycle de vie -------------------------------------------------------------
    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="relay-client")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._writer:
            self._writer.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._fail_pending()
        self._set_state("disconnected")

    def reconnect_now(self) -> None:
        self._wake.set()

    def _set_state(self, state: str, **info) -> None:
        if state != self.state or info:
            self.state = state
            if self.on_state:
                self.on_state(state, info)

    def _fail_pending(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ChannelUnavailable("connexion perdue"))
        self._pending.clear()

    # -- boucle de reconnexion ------------------------------------------------------
    async def _run(self) -> None:
        delay = 1.0
        while not self._stopping:
            self._wake.clear()
            try:
                self._set_state("connecting")
                await self._session()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.info("Serveur injoignable (%s)", self.last_error)
            self._writer = None
            self._fail_pending()
            if self._stopping:
                break
            if self.state == "replaced":
                await self._wake.wait()
                continue
            self._set_state("disconnected", error=self.last_error)
            try:
                await asyncio.wait_for(self._wake.wait(), delay * random.uniform(0.8, 1.2))
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self.backoff_max)

    async def _session(self) -> None:
        ctx = None
        if self.tls:
            ctx = self._ssl_ctx or ssl.create_default_context()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port, ssl=ctx), 10)
        self._writer = writer
        try:
            hello = await asyncio.wait_for(read_frame(reader, 8192), 10)
            if hello["t"] != "hello" or hello.get("v") != 1:
                raise ConnectionError("serveur incompatible")
            challenge = unb64(hello["challenge"])
            pub = self.identity.public
            await write_frame(writer, {
                "t": "auth", "id": self.identity.id, "sk": b64(pub.sign_pk), "bk": b64(pub.box_pk),
                "sig": b64(self.identity.sign(b"ldc-auth-v1\0" + challenge + self.identity.id.encode())),
                "visible": self.visible, **({"tok": self.token} if self.token else {})})
            ready = await asyncio.wait_for(read_frame(reader, 8192), 10)
            if ready["t"] != "ready":
                reason = ready.get("reason", "")
                raise ConnectionError("accès refusé par le serveur (jeton d'accès ou liste d'invités)" if reason == "denied" else "authentification refusée par le serveur")
            self.observed_ip = ready.get("observed_ip")
            self.last_error = None
            self._set_state("connected", observed_ip=self.observed_ip, queued=ready.get("queued", 0))
            if self._watch_ids:
                await self._send({"t": "watch", "ids": self._watch_ids})
            ping = asyncio.create_task(self._ping_loop())
            self._deliver_q = asyncio.Queue()
            worker = asyncio.create_task(self._deliver_worker(self._deliver_q))
            try:
                await self._read_loop(reader)
            finally:
                ping.cancel()
                worker.cancel()
        finally:
            writer.close()

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            try:
                await self._send({"t": "ping"})
            except Exception:
                return

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                msg = await asyncio.wait_for(read_frame(reader), self.ping_interval * 3)
            except FrameError:
                continue
            t = msg["t"]
            if t == "deliver":
                self._deliver_q.put_nowait(msg)  # traité hors de la boucle de lecture (le traitement envoie des accusés)
            elif t == "relay_ack":
                fut = self._pending.pop(str(msg.get("mid")), None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif t == "presence" and self.on_presence and isinstance(msg.get("states"), dict):
                self.on_presence(msg["states"])
            elif t == "bye":
                if msg.get("reason") == "replaced":
                    self._set_state("replaced")
                return

    async def _deliver_worker(self, q: "asyncio.Queue[dict]") -> None:
        while True:
            await self._on_deliver(await q.get())

    async def _on_deliver(self, msg: dict) -> None:
        env = msg.get("env")
        ok = False
        if self.on_deliver and isinstance(env, dict):
            try:
                ok = await self.on_deliver(env)
            except Exception:
                log.exception("Traitement d'une enveloppe reçue en échec")
        if ok and isinstance(env, dict) and isinstance(env.get("id"), str):
            await self._send({"t": "mailack", "mid": env["id"]})

    async def _send(self, obj: dict) -> None:
        w = self._writer
        if w is None or w.is_closing() or self.state != "connected":
            raise ChannelUnavailable("pas de connexion au serveur")
        async with self._lock:
            await write_frame(w, obj)

    # -- API ---------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.state == "connected"

    async def send_env(self, to: str, env: dict, ephemeral: bool = False, timeout: float = 10.0) -> str:
        """Retourne 'delivered' | 'queued' | 'offline' | 'rejected:<raison>'. Lève ChannelUnavailable."""
        mid = env["id"]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self._send({"t": "relay", "to": to, "mid": mid, "env": env, "ephemeral": ephemeral})
            ack = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as e:
            raise ChannelUnavailable("le serveur n'a pas répondu") from e
        except (ConnectionError, OSError) as e:
            raise ChannelUnavailable(str(e)) from e
        finally:
            self._pending.pop(mid, None)
        status = str(ack.get("status"))
        return f"rejected:{ack.get('reason', '?')}" if status == "rejected" else status

    async def watch(self, ids: list[str]) -> None:
        self._watch_ids = list(ids)
        if self.connected:
            await self._send({"t": "watch", "ids": self._watch_ids})

    async def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if self.connected:
            await self._send({"t": "vis", "visible": visible})
