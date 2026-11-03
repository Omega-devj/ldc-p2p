"""Serveur LDC : rendez-vous + présence + relais de secours + boîte aux lettres hors ligne.

Ce que le serveur VOIT : identifiants LDC, adresse IP de connexion, qui écrit à qui, heure et
taille des enveloppes, liste de contacts surveillés (présence).
Ce que le serveur NE PEUT PAS faire : lire le contenu (chiffré de bout en bout), forger un message
(signatures vérifiées par les clients avec les clés épinglées), retrouver une clé privée.
Les enveloppes ne sont gardées que le temps nécessaire à leur remise (TTL) et supprimées à la confirmation.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass, field

from ..crypto.e2e import EnvelopeError, validate_envelope
from ..crypto.identity import ID_RE, PublicIdentity, derive_ldc_id, unb64, b64
from ..network.framing import FrameError, PROTOCOL_VERSION, read_frame, write_frame

log = logging.getLogger("ldc.server")


@dataclass
class Limits:
    max_connections: int = 2000
    max_per_ip: int = 20
    mailbox_max_msgs: int = 2000
    mailbox_max_bytes: int = 64 * 1024 * 1024
    mailbox_ttl_s: int = 14 * 86400
    max_watch: int = 500
    handshake_timeout: float = 10.0
    idle_timeout: float = 90.0
    send_timeout: float = 15.0
    rate_capacity: float = 300.0
    rate_refill: float = 60.0  # enveloppes / seconde en régime établi
    max_errors: int = 10


class Mailbox:
    def __init__(self, path: str, limits: Limits):
        self.limits = limits
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS mail(to_id TEXT NOT NULL, mid TEXT NOT NULL, env TEXT NOT NULL, "
                        "ts REAL NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(to_id, mid))")

    def put(self, to_id: str, mid: str, env_json: str) -> str:
        n, total = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM mail WHERE to_id=?", (to_id,)).fetchone()
        if self.db.execute("SELECT 1 FROM mail WHERE to_id=? AND mid=?", (to_id, mid)).fetchone():
            return "dup"
        size = len(env_json)
        if n >= self.limits.mailbox_max_msgs or total + size > self.limits.mailbox_max_bytes:
            return "full"
        self.db.execute("INSERT INTO mail VALUES(?,?,?,?,?)", (to_id, mid, env_json, time.time(), size))
        return "new"

    def fetch(self, to_id: str, limit: int = 200) -> list[tuple[str, str]]:
        return self.db.execute("SELECT mid, env FROM mail WHERE to_id=? ORDER BY ts, rowid LIMIT ?", (to_id, limit)).fetchall()

    def delete(self, to_id: str, mid: str) -> None:
        self.db.execute("DELETE FROM mail WHERE to_id=? AND mid=?", (to_id, mid))

    def count(self, to_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM mail WHERE to_id=?", (to_id,)).fetchone()[0]

    def prune(self) -> int:
        cur = self.db.execute("DELETE FROM mail WHERE ts<?", (time.time() - self.limits.mailbox_ttl_s,))
        return cur.rowcount

    def close(self) -> None:
        self.db.close()


@dataclass
class Session:
    id: str
    writer: asyncio.StreamWriter
    ip: str
    visible: bool = True
    watching: set[str] = field(default_factory=set)
    tokens: float = 0.0
    last_refill: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, obj: dict, timeout: float) -> bool:
        if self.closed:
            return False
        try:
            async with self.lock:
                await asyncio.wait_for(write_frame(self.writer, obj), timeout)
            return True
        except (asyncio.TimeoutError, ConnectionError, OSError, FrameError):
            self.close()
            return False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except Exception:
                pass


class RelayServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9443, db_path: str = ":memory:",
                 ssl_ctx: ssl.SSLContext | None = None, limits: Limits | None = None):
        self.host, self.port, self.ssl_ctx = host, port, ssl_ctx
        self.limits = limits or Limits()
        self.mailbox = Mailbox(db_path, self.limits)
        self.sessions: dict[str, Session] = {}
        self.watchers: dict[str, set[str]] = defaultdict(set)  # id observé -> ids des observateurs
        self.ip_counts: dict[str, int] = defaultdict(int)
        self._server: asyncio.base_events.Server | None = None
        self._prune_task: asyncio.Task | None = None

    # -- cycle de vie ----------------------------------------------------------
    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port, ssl=self.ssl_ctx)
        self.port = self._server.sockets[0].getsockname()[1]
        self._prune_task = asyncio.create_task(self._prune_loop(), name="mailbox-prune")
        log.info("Serveur LDC en écoute sur %s:%s (TLS=%s)", self.host, self.port, bool(self.ssl_ctx))

    async def stop(self) -> None:
        if self._prune_task:
            self._prune_task.cancel()
        if self._server:
            self._server.close()
        for s in list(self.sessions.values()):
            s.close()
        if self._server:
            try:
                await asyncio.wait_for(self._server.wait_closed(), 3)
            except asyncio.TimeoutError:
                pass
        self.mailbox.close()

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            n = self.mailbox.prune()
            if n:
                log.info("Boîte aux lettres : %d enveloppes expirées supprimées", n)

    # -- connexion -----------------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        ip = str(peer[0])
        if len(self.sessions) >= self.limits.max_connections or self.ip_counts[ip] >= self.limits.max_per_ip:
            writer.close()
            return
        self.ip_counts[ip] += 1
        sess: Session | None = None
        try:
            sess = await asyncio.wait_for(self._handshake(reader, writer, ip), self.limits.handshake_timeout)
            if sess is None:
                return
            await self._serve(reader, sess)
        except (asyncio.TimeoutError, ConnectionError, FrameError, OSError):
            pass
        except Exception:
            log.exception("Erreur inattendue de session")
        finally:
            self.ip_counts[ip] -= 1
            if sess is not None:
                await self._drop(sess)
            else:
                writer.close()

    async def _handshake(self, reader, writer, ip: str) -> Session | None:
        challenge = os.urandom(32)
        await write_frame(writer, {"t": "hello", "v": PROTOCOL_VERSION, "challenge": b64(challenge), "server": "ldc-relay/2"})
        msg = await read_frame(reader, max_size=8192)
        try:
            if msg["t"] != "auth":
                raise ValueError
            sk, bk = unb64(msg["sk"]), unb64(msg["bk"])
            ldc_id = msg["id"]
            if len(sk) != 32 or len(bk) != 32 or derive_ldc_id(sk, bk) != ldc_id:
                raise ValueError
            if not PublicIdentity(sk, bk).verify(unb64(msg["sig"]), b"ldc-auth-v1\0" + challenge + ldc_id.encode()):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            await write_frame(writer, {"t": "error", "reason": "auth"})
            writer.close()
            return None
        old = self.sessions.get(ldc_id)
        sess = Session(ldc_id, writer, ip, visible=bool(msg.get("visible", True)), tokens=self.limits.rate_capacity)
        self.sessions[ldc_id] = sess
        if old:
            await old.send({"t": "bye", "reason": "replaced"}, 2)
            old.close()
        queued = self.mailbox.count(ldc_id)
        await sess.send({"t": "ready", "observed_ip": ip, "queued": queued}, self.limits.send_timeout)
        asyncio.create_task(self._flush(sess), name="mailbox-flush")
        await self._notify_presence(ldc_id)
        return sess

    async def _drop(self, sess: Session) -> None:
        sess.close()
        if self.sessions.get(sess.id) is sess:
            del self.sessions[sess.id]
            for target in sess.watching:
                self.watchers[target].discard(sess.id)
            await self._notify_presence(sess.id)

    async def _flush(self, sess: Session) -> None:
        for mid, env_json in self.mailbox.fetch(sess.id):
            if not await sess.send({"t": "deliver", "env": _loads(env_json), "stored": True}, self.limits.send_timeout):
                return

    # -- présence ----------------------------------------------------------------------
    def _is_online(self, id_: str) -> bool:
        s = self.sessions.get(id_)
        return bool(s and s.visible and not s.closed)

    async def _notify_presence(self, id_: str) -> None:
        state = self._is_online(id_)
        for w in list(self.watchers.get(id_, ())):
            s = self.sessions.get(w)
            if s:
                await s.send({"t": "presence", "states": {id_: state}}, self.limits.send_timeout)

    # -- boucle principale ----------------------------------------------------------------
    def _allow(self, s: Session) -> bool:
        now = time.monotonic()
        s.tokens = min(self.limits.rate_capacity, s.tokens + (now - s.last_refill) * self.limits.rate_refill)
        s.last_refill = now
        if s.tokens >= 1:
            s.tokens -= 1
            return True
        return False

    async def _serve(self, reader, s: Session) -> None:
        errors = 0
        while not s.closed:
            try:
                msg = await asyncio.wait_for(read_frame(reader), self.limits.idle_timeout)
            except FrameError:
                errors += 1
                if errors >= self.limits.max_errors:
                    return
                continue
            t = msg["t"]
            if t == "ping":
                await s.send({"t": "pong"}, self.limits.send_timeout)
            elif t == "relay":
                await self._relay(s, msg)
            elif t == "mailack":
                if isinstance(msg.get("mid"), str):
                    self.mailbox.delete(s.id, msg["mid"])
            elif t == "watch":
                await self._watch(s, msg)
            elif t == "vis":
                v = bool(msg.get("visible", True))
                if v != s.visible:
                    s.visible = v
                    await self._notify_presence(s.id)
            else:
                errors += 1
                await s.send({"t": "error", "reason": "unknown"}, self.limits.send_timeout)
                if errors >= self.limits.max_errors:
                    return

    async def _relay(self, s: Session, msg: dict) -> None:
        ack = {"t": "relay_ack", "mid": msg.get("mid") if isinstance(msg.get("mid"), str) else None}
        env = msg.get("env")
        try:
            validate_envelope(env)
            to = msg.get("to")
            if env["from"] != s.id or env["to"] != to or not ID_RE.match(to):
                raise EnvelopeError("routage invalide")
        except (EnvelopeError, TypeError):
            return await self._ack(s, ack, "rejected", "invalid")
        ack["mid"] = env["id"]
        if not self._allow(s):
            return await self._ack(s, ack, "rejected", "rate")
        ephemeral = bool(msg.get("ephemeral"))
        target = self.sessions.get(to)
        online = bool(target and not target.closed)
        if ephemeral:
            if online and await target.send({"t": "deliver", "env": env}, self.limits.send_timeout):
                return await self._ack(s, ack, "delivered")
            return await self._ack(s, ack, "offline")
        env_json = _dumps(env)
        stored = self.mailbox.put(to, env["id"], env_json)
        if stored == "full":
            return await self._ack(s, ack, "rejected", "mailbox_full")
        if online and stored == "new" and await target.send({"t": "deliver", "env": env}, self.limits.send_timeout):
            return await self._ack(s, ack, "delivered")
        await self._ack(s, ack, "queued" if not online or stored == "dup" else "queued")

    async def _ack(self, s: Session, ack: dict, status: str, reason: str | None = None) -> None:
        ack["status"] = status
        if reason:
            ack["reason"] = reason
        await s.send(ack, self.limits.send_timeout)

    async def _watch(self, s: Session, msg: dict) -> None:
        ids = msg.get("ids")
        if not isinstance(ids, list):
            return
        ids = {i for i in ids if isinstance(i, str) and ID_RE.match(i)}
        if len(ids) > self.limits.max_watch:
            await s.send({"t": "error", "reason": "watch_limit"}, self.limits.send_timeout)
            return
        for old in s.watching - ids:
            self.watchers[old].discard(s.id)
        for new in ids:
            self.watchers[new].add(s.id)
        s.watching = ids
        await s.send({"t": "presence", "states": {i: self._is_online(i) for i in ids}}, self.limits.send_timeout)


def _dumps(obj: dict) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _loads(s: str) -> dict:
    import json
    return json.loads(s)
