"""Partage de fichiers entre contacts (conversations privées).

Protocole : l'offre voyage comme un message normal (fiable, acquittée, dédupliquée). Le destinataire
doit ACCEPTER (consentement) ; l'expéditeur envoie alors des blocs chiffrés dans une fenêtre glissante,
le destinataire acquitte sa position, et reprend tout seul là où il s'est arrêté après une coupure.
Intégrité : SHA-256 complet vérifié avant de rendre le fichier visible. Nom assaini, jamais de chemin
fourni par le pair.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from pathlib import Path
from typing import Any

from ..crypto.e2e import new_mid, now_ms, seal
from ..crypto.identity import b64, unb64
from ..network.relay_client import ChannelUnavailable
from .core import ChatEngine, EngineError

log = logging.getLogger("ldc.files")

CHUNK = 96 * 1024
MAX_FILE = 100 * 1024 * 1024
WINDOW = 8
ACK_EVERY = 4
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\u202a-\u202e\u2066-\u2069]')
DANGEROUS_EXT = {".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".js", ".jar", ".lnk", ".reg", ".dll", ".hta"}


def safe_filename(name: str) -> str:
    name = str(name).replace("\\", "/").split("/")[-1]
    name = _BAD.sub("_", name).strip(" .")
    if not name:
        name = "fichier"
    stem, dot, ext = name.partition(".")
    if stem.lower() in _RESERVED:
        name = "_" + name
    if len(name) > 120:
        base, _, ext2 = name.rpartition(".")
        name = (base[:100] + "." + ext2[:15]) if base else name[:120]
    return name


def unique_path(directory: Path, name: str) -> Path:
    p = directory / name
    i = 1
    while p.exists():
        p = directory / f"{Path(name).stem} ({i}){Path(name).suffix}"
        i += 1
    return p


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class FilesMixin(ChatEngine):
    def _init_extensions(self) -> None:
        super()._init_extensions()
        self._streams: dict[str, asyncio.Task] = {}
        self._acked: dict[str, int] = {}
        self._ack_events: dict[str, asyncio.Event] = {}

    def _resume_extensions(self) -> None:
        super()._resume_extensions()
        for t in self.db.active_transfers():
            if t["direction"] == "out":
                self.db.update_transfer(t["fid"], state="paused")  # reprise pilotée par le destinataire

    # -- expéditeur --------------------------------------------------------------------
    async def send_file(self, conv: str, path: str | Path) -> str:
        p = Path(path)
        if conv.startswith("grp:"):
            raise EngineError("Le partage de fichiers est disponible dans les conversations privées.")
        try:
            size = p.stat().st_size
        except OSError as e:
            raise EngineError("Fichier illisible") from e
        if not p.is_file() or size == 0:
            raise EngineError("Choisissez un fichier non vide.")
        if size > MAX_FILE:
            raise EngineError(f"Fichier trop volumineux (maximum {MAX_FILE // (1024 * 1024)} Mo).")
        peers = self._conv_recipients(conv)
        sha = await asyncio.get_running_loop().run_in_executor(None, _sha256_file, p)
        fid = new_mid()
        n = math.ceil(size / CHUNK)
        extra = {"fid": fid, "name": safe_filename(p.name), "size": size, "sha256": sha, "n": n, "chunk": CHUNK}
        self.db.put_transfer(fid=fid, mid=fid, conv=conv, peer=peers[0], direction="out", size=size, sha256=sha,
                             chunk_size=CHUNK, nchunks=n, next_idx=0, path=str(p), state="offered", created_at=now_ms())
        return self._send_message(conv, "file", extra["name"], extra, mid=fid)

    def transfer(self, mid: str) -> dict | None:
        return self.db.get_transfer(mid)

    async def _dispatch_ext(self, sender: str, contact: Any, env: dict, inner: dict) -> None:
        t = inner["t"]
        if t not in ("fget", "fno", "fack", "fchunk", "fdone"):
            return await super()._dispatch_ext(sender, contact, env, inner)
        fid = inner.get("f")
        tr = self.db.get_transfer(fid) if isinstance(fid, str) else None
        if tr is None or tr["peer"] != sender:
            return
        if t == "fget" and tr["direction"] == "out":
            start = inner.get("i")
            if isinstance(start, int) and 0 <= start <= tr["nchunks"] and tr["state"] in ("offered", "paused", "sending"):
                self._start_stream(tr, start)
        elif t == "fno" and tr["direction"] == "out":
            self._stop_stream(fid)
            self.db.update_transfer(fid, state="declined")
            self.events.emit("file_state", mid=tr["mid"], state="declined")
        elif t == "fack" and tr["direction"] == "out":
            u = inner.get("u")
            if isinstance(u, int) and 0 <= u <= tr["nchunks"]:
                self._acked[fid] = u
                self._ack_events.setdefault(fid, asyncio.Event()).set()
                self.events.emit("file_progress", mid=tr["mid"], done=u, total=tr["nchunks"])
        elif t == "fdone" and tr["direction"] == "out":
            self._stop_stream(fid)
            ok = bool(inner.get("ok"))
            self.db.update_transfer(fid, state="done" if ok else "failed")
            self.events.emit("file_state", mid=tr["mid"], state="done" if ok else "failed")
        elif t == "fchunk" and tr["direction"] == "in":
            await self._on_chunk(tr, inner)

    def _stop_stream(self, fid: str) -> None:
        task = self._streams.pop(fid, None)
        if task and not task.done():
            task.cancel()

    def _start_stream(self, tr: dict, start: int) -> None:
        self._stop_stream(tr["fid"])
        self.db.update_transfer(tr["fid"], state="sending")
        self.events.emit("file_state", mid=tr["mid"], state="sending")
        self._acked[tr["fid"]] = start
        self._streams[tr["fid"]] = self.spawn(self._stream(tr["fid"], start), f"stream-{tr['fid'][:6]}")

    async def _stream(self, fid: str, start: int) -> None:
        tr = self.db.get_transfer(fid)
        pub = self.pub_of(tr["peer"]) if tr else None
        if not tr or pub is None:
            return
        n, ev = tr["nchunks"], self._ack_events.setdefault(fid, asyncio.Event())
        sent = acked = start
        stalls = 0
        try:
            with open(tr["path"], "rb") as f:
                while acked < n:
                    while sent < n and sent - acked < WINDOW:
                        f.seek(sent * CHUNK)
                        data = f.read(CHUNK)
                        env = seal(self.identity, pub, {"t": "fchunk", "f": fid, "i": sent, "d": b64(data)})
                        status = await self._transmit(tr["peer"], env)
                        if status.startswith("rejected"):
                            await asyncio.sleep(1.0)  # limite de débit : on réessaie le même bloc
                            continue
                        sent += 1
                    try:
                        await asyncio.wait_for(ev.wait(), 20)
                        ev.clear()
                        stalls = 0
                    except asyncio.TimeoutError:
                        stalls += 1
                        if stalls >= 3:
                            raise ChannelUnavailable("le destinataire ne répond plus")
                    u = self._acked.get(fid, acked)
                    if u < sent and u == acked:
                        sent = u  # trou signalé ou perte : on renvoie depuis la position acquittée
                    acked = max(acked, u)
            # tous les blocs sont acquittés ; l'état final arrive avec « fdone »
        except (ChannelUnavailable, ConnectionError, OSError) as e:
            log.info("Transfert en pause (%s)", e)
            self.db.update_transfer(fid, state="paused")
            self.events.emit("file_state", mid=tr["mid"], state="paused")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Erreur de transfert")
            self.db.update_transfer(fid, state="failed")
            self.events.emit("file_state", mid=tr["mid"], state="failed")

    # -- destinataire --------------------------------------------------------------------
    def _on_file_offer(self, sender: str, conv: str, mid: str, extra: dict) -> None:
        try:
            fid, size, sha, n, chunk = extra["fid"], extra["size"], extra["sha256"], extra["n"], extra["chunk"]
            ok = (isinstance(fid, str) and re.fullmatch(r"[0-9a-f]{32}", fid) and isinstance(size, int) and 0 < size <= MAX_FILE
                  and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) and chunk == CHUNK and n == math.ceil(size / CHUNK))
        except (KeyError, TypeError):
            ok = False
        if not ok or self.db.get_transfer(fid):
            self.db.delete_message(mid)
            return
        self.db.put_transfer(fid=fid, mid=mid, conv=conv, peer=sender, direction="in", size=size, sha256=sha,
                             chunk_size=CHUNK, nchunks=n, next_idx=0, path=None, state="offered", created_at=now_ms())
        extra["name"] = safe_filename(extra.get("name", "fichier"))
        self.db.update_message(mid, extra=extra)
        limit = self.settings.auto_accept_mb * 1024 * 1024
        if limit and size <= limit and Path(extra["name"]).suffix.lower() not in DANGEROUS_EXT:
            self.spawn(self.accept_file(mid), "auto-accept")

    async def accept_file(self, mid: str) -> None:
        tr = self.db.get_transfer(mid)
        if not tr or tr["direction"] != "in" or tr["state"] not in ("offered", "paused", "failed"):
            raise EngineError("Transfert introuvable")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        part = self.download_dir / f".ldc-{tr['fid'][:12]}.part"
        if tr["state"] == "failed" and part.exists():
            part.unlink()
            self.db.update_transfer(tr["fid"], next_idx=0)
            tr["next_idx"] = 0
        self.db.update_transfer(tr["fid"], state="receiving", path=str(part))
        self._enqueue(tr["peer"], {"t": "fget", "f": tr["fid"], "i": tr["next_idx"]}, kind="ctl")
        self.events.emit("file_state", mid=mid, state="receiving")

    async def decline_file(self, mid: str) -> None:
        tr = self.db.get_transfer(mid)
        if not tr or tr["direction"] != "in":
            raise EngineError("Transfert introuvable")
        self.db.update_transfer(tr["fid"], state="declined")
        self._enqueue(tr["peer"], {"t": "fno", "f": tr["fid"]}, kind="ctl")
        self.events.emit("file_state", mid=mid, state="declined")

    async def _on_chunk(self, tr: dict, inner: dict) -> None:
        if tr["state"] != "receiving":
            return
        i, d = inner.get("i"), inner.get("d")
        if not isinstance(i, int) or not isinstance(d, str):
            return
        fid, peer, n = tr["fid"], tr["peer"], tr["nchunks"]
        nxt = tr["next_idx"]
        if i != nxt:
            if i > nxt or i < nxt:  # trou ou doublon : indique notre position
                await self._send_ctl(peer, {"t": "fack", "f": fid, "u": nxt})
            return
        try:
            data = unb64(d)
        except ValueError:
            return
        expected = CHUNK if i < n - 1 else tr["size"] - (n - 1) * CHUNK
        if len(data) != expected:
            return
        try:
            part = Path(tr["path"])
            with open(part, "r+b" if part.exists() else "wb") as f:
                f.seek(i * CHUNK)
                f.truncate()
                f.write(data)
        except OSError as e:
            log.warning("Écriture impossible: %s", e)
            self.db.update_transfer(fid, state="failed")
            await self._send_ctl(peer, {"t": "fdone", "f": fid, "ok": False})
            self.events.emit("file_state", mid=tr["mid"], state="failed")
            return
        nxt += 1
        self.db.update_transfer(fid, next_idx=nxt)
        self.events.emit("file_progress", mid=tr["mid"], done=nxt, total=n)
        if nxt == n:
            await self._finish_download(tr)
        elif nxt % ACK_EVERY == 0:
            await self._send_ctl(peer, {"t": "fack", "f": fid, "u": nxt})

    async def _finish_download(self, tr: dict) -> None:
        part = Path(tr["path"])
        digest = await asyncio.get_running_loop().run_in_executor(None, _sha256_file, part)
        m = self.db.get_message(tr["mid"])
        if digest != tr["sha256"] or part.stat().st_size != tr["size"]:
            part.unlink(missing_ok=True)
            self.db.update_transfer(tr["fid"], state="failed", next_idx=0)
            await self._send_ctl(tr["peer"], {"t": "fdone", "f": tr["fid"], "ok": False})
            self.events.emit("file_state", mid=tr["mid"], state="failed")
            return
        name = safe_filename((m["extra"] or {}).get("name", "fichier") if m else "fichier")
        final = unique_path(self.download_dir, name)
        part.replace(final)
        self.db.update_transfer(tr["fid"], state="done", path=str(final))
        await self._send_ctl(tr["peer"], {"t": "fack", "f": tr["fid"], "u": tr["nchunks"]})
        await self._send_ctl(tr["peer"], {"t": "fdone", "f": tr["fid"], "ok": True})
        self.events.emit("file_state", mid=tr["mid"], state="done")

    def _contact_online_ext(self, cid: str) -> None:
        super()._contact_online_ext(cid)
        for t in self.db.active_transfers():
            if t["direction"] == "in" and t["peer"] == cid and t["state"] == "receiving":
                try:
                    self._enqueue(cid, {"t": "fget", "f": t["fid"], "i": t["next_idx"]}, kind="ctl")
                except EngineError:
                    pass
