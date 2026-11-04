"""Stockage local SQLite : migrations versionnées, transactions, contenus chiffrés au repos.

Chiffrés (ChaCha20-Poly1305, clé dérivée de l'identité) : corps des messages et métadonnées de pièce jointe.
NON chiffrés : identifiants, horodatages, statuts, noms de contacts (limite documentée ; SQLCipher prévu).
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..crypto.cipher import FieldCipher

RECEIPT_RANK = {"pending": 0, "sent": 1, "delivered": 2, "read": 3}
_RANK_STATUS = {v: k for k, v in RECEIPT_RANK.items()}

MIGRATIONS: list = [
    """
    CREATE TABLE contacts(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, alias TEXT,
        sign_pk BLOB NOT NULL, box_pk BLOB NOT NULL,
        state TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
        added_at INTEGER NOT NULL, hint TEXT, last_seen INTEGER);
    CREATE TABLE groups(
        gid TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1, members TEXT NOT NULL,
        created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mid TEXT NOT NULL UNIQUE,
        conv TEXT NOT NULL, sender TEXT NOT NULL, direction TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'text', body BLOB, extra BLOB,
        ts INTEGER NOT NULL, status TEXT NOT NULL, reply_to TEXT,
        expires_at INTEGER, ttl INTEGER NOT NULL DEFAULT 0, read_at INTEGER);
    CREATE INDEX idx_msg_conv ON messages(conv, ts, id);
    CREATE INDEX idx_msg_exp ON messages(expires_at) WHERE expires_at IS NOT NULL;
    CREATE TABLE receipts(
        mid TEXT NOT NULL, peer TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        PRIMARY KEY(mid, peer));
    CREATE TABLE outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT, env_mid TEXT NOT NULL, msg_mid TEXT,
        to_id TEXT NOT NULL, envelope TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'msg',
        attempts INTEGER NOT NULL DEFAULT 0, next_try INTEGER NOT NULL,
        created_at INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'queued', UNIQUE(env_mid, to_id));
    CREATE INDEX idx_outbox_due ON outbox(state, next_try);
    CREATE TABLE seen(mid TEXT PRIMARY KEY, ts INTEGER NOT NULL);
    CREATE TABLE conv_settings(
        conv TEXT PRIMARY KEY, ttl INTEGER NOT NULL DEFAULT 0,
        muted INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    """
    CREATE TABLE transfers(
        fid TEXT PRIMARY KEY, mid TEXT NOT NULL, conv TEXT NOT NULL, peer TEXT NOT NULL,
        direction TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
        chunk_size INTEGER NOT NULL, nchunks INTEGER NOT NULL, next_idx INTEGER NOT NULL DEFAULT 0,
        path TEXT, state TEXT NOT NULL, created_at INTEGER NOT NULL);
    """,
    "_mig3",  # réactions + chiffrement des noms de contacts/groupes (fonction Python ci-dessous)
]


def now_ms() -> int:
    return int(time.time() * 1000)


class Database:
    def __init__(self, path: Path | str, cipher: FieldCipher):
        self.path = str(path)
        self.cipher = cipher
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    # -- infrastructure --------------------------------------------------------
    def _migrate(self) -> None:
        (version,) = self.conn.execute("PRAGMA user_version").fetchone()
        if version > len(MIGRATIONS):
            raise RuntimeError("Base créée par une version plus récente de LDC P2P")
        for i in range(version, len(MIGRATIONS)):
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                step = MIGRATIONS[i]
                if step == "_mig3":
                    self._mig3()
                else:
                    for stmt in filter(str.strip, step.split(";")):
                        self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version={i + 1}")
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def _mig3(self) -> None:
        c = self.conn
        c.execute("CREATE TABLE reactions(mid TEXT NOT NULL, sender TEXT NOT NULL, emoji TEXT NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY(mid, sender))")
        for r in c.execute("SELECT id, name, alias, hint FROM contacts").fetchall():
            c.execute("UPDATE contacts SET name=?, alias=?, hint=? WHERE id=?",
                      (self._es(r["name"], b"c-name|" + r["id"].encode()), self._es(r["alias"], b"c-alias|" + r["id"].encode()),
                       self._es(r["hint"], b"c-hint|" + r["id"].encode()), r["id"]))
        for r in c.execute("SELECT gid, name, members FROM groups").fetchall():
            c.execute("UPDATE groups SET name=?, members=? WHERE gid=?",
                      (self._es(r["name"], b"g-name|" + r["gid"].encode()), self._es(r["members"], b"g-members|" + r["gid"].encode()), r["gid"]))

    def _es(self, value, aad: bytes):
        """Chiffre une chaîne (ou renvoie None)."""
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        return self.cipher.encrypt(str(value).encode("utf-8"), aad)

    def _ds(self, blob, aad: bytes):
        if blob is None:
            return None
        if isinstance(blob, str):  # donnée héritée non migrée
            return blob
        return self.cipher.decrypt(bytes(blob), aad).decode("utf-8")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _enc(self, data: Any, mid: str) -> bytes | None:
        if data is None:
            return None
        raw = data.encode("utf-8") if isinstance(data, str) else json.dumps(data).encode("utf-8")
        return self.cipher.encrypt(raw, mid.encode())

    def _dec_text(self, blob: bytes | None, mid: str) -> str | None:
        return None if blob is None else self.cipher.decrypt(blob, mid.encode()).decode("utf-8")

    # -- paramètres --------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT key,value FROM settings")}

    # -- contacts (nom, alias et indication de serveur chiffrés au repos) -----------------------
    def _contact(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        i = d["id"].encode()
        d["name"] = self._ds(r["name"], b"c-name|" + i) or ""
        d["alias"] = self._ds(r["alias"], b"c-alias|" + i)
        d["hint"] = self._ds(r["hint"], b"c-hint|" + i)
        d["sign_pk"], d["box_pk"] = bytes(r["sign_pk"]), bytes(r["box_pk"])
        return d

    def put_contact(self, id: str, name: str, sign_pk: bytes, box_pk: bytes, state: str, hint: str | None = None) -> None:
        i = id.encode()
        self.conn.execute(
            "INSERT INTO contacts(id,name,sign_pk,box_pk,state,added_at,hint) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, hint=COALESCE(excluded.hint, hint)",
            (id, self._es(name, b"c-name|" + i), sign_pk, box_pk, state, now_ms(), self._es(hint, b"c-hint|" + i)))

    def get_contact(self, id: str) -> dict | None:
        return self._contact(self.conn.execute("SELECT * FROM contacts WHERE id=?", (id,)).fetchone())

    def list_contacts(self, states: Iterable[str] | None = None) -> list[dict]:
        wanted = None if states is None else set(states)
        rows = [self._contact(r) for r in self.conn.execute("SELECT * FROM contacts")]
        rows = [r for r in rows if wanted is None or r["state"] in wanted]
        return sorted(rows, key=lambda r: (r["alias"] or r["name"]).casefold())

    def update_contact(self, id: str, **fields: Any) -> None:
        allowed = {"state", "verified", "alias", "last_seen", "name", "hint"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("champ de contact non modifiable")
        i = id.encode()
        for k in ("name", "alias", "hint"):
            if k in fields:
                fields[k] = self._es(fields[k], f"c-{k}|".encode() + i)
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE contacts SET {sets} WHERE id=?", (*fields.values(), id))

    def delete_contact(self, id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM contacts WHERE id=?", (id,))
            c.execute("DELETE FROM outbox WHERE to_id=?", (id,))
            self._delete_conv(c, id)

    def _delete_conv(self, c: sqlite3.Connection, conv: str) -> None:
        c.execute("DELETE FROM receipts WHERE mid IN (SELECT mid FROM messages WHERE conv=?)", (conv,))
        c.execute("DELETE FROM reactions WHERE mid IN (SELECT mid FROM messages WHERE conv=?)", (conv,))
        c.execute("DELETE FROM messages WHERE conv=?", (conv,))
        c.execute("DELETE FROM conv_settings WHERE conv=?", (conv,))

    def delete_conversation(self, conv: str) -> None:
        with self.tx() as c:
            self._delete_conv(c, conv)

    # -- groupes (nom et membres chiffrés au repos) -----------------------------------------------
    def _group(self, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        g = r["gid"].encode()
        d = dict(r)
        d["name"] = self._ds(r["name"], b"g-name|" + g) or ""
        d["members"] = json.loads(self._ds(r["members"], b"g-members|" + g) or "[]")
        return d

    def put_group(self, gid: str, name: str, owner: str, members: list[str], version: int) -> None:
        g = gid.encode()
        self.conn.execute(
            "INSERT INTO groups(gid,name,owner,version,members,created_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(gid) DO UPDATE SET name=excluded.name, members=excluded.members, version=excluded.version, active=1",
            (gid, self._es(name, b"g-name|" + g), owner, version,
             self._es(json.dumps(sorted(set(members))), b"g-members|" + g), now_ms()))

    def get_group(self, gid: str) -> dict | None:
        return self._group(self.conn.execute("SELECT * FROM groups WHERE gid=?", (gid,)).fetchone())

    def list_groups(self) -> list[dict]:
        gs = [self._group(r) for r in self.conn.execute("SELECT * FROM groups WHERE active=1")]
        return sorted(gs, key=lambda g: g["name"].casefold())

    def deactivate_group(self, gid: str) -> None:
        self.conn.execute("UPDATE groups SET active=0 WHERE gid=?", (gid,))

    # -- réactions ----------------------------------------------------------------------------------
    def set_reaction(self, mid: str, sender: str, emoji: str | None) -> None:
        if emoji:
            self.conn.execute("INSERT INTO reactions(mid,sender,emoji,ts) VALUES(?,?,?,?) "
                              "ON CONFLICT(mid,sender) DO UPDATE SET emoji=excluded.emoji, ts=excluded.ts", (mid, sender, emoji, now_ms()))
        else:
            self.conn.execute("DELETE FROM reactions WHERE mid=? AND sender=?", (mid, sender))

    def reactions_for(self, mids: list[str]) -> dict[str, dict[str, list[str]]]:
        out: dict[str, dict[str, list[str]]] = {}
        for i in range(0, len(mids), 500):
            chunk = mids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in self.conn.execute(f"SELECT mid, sender, emoji FROM reactions WHERE mid IN ({q}) ORDER BY ts", chunk):
                out.setdefault(r["mid"], {}).setdefault(r["emoji"], []).append(r["sender"])
        return out

    # -- messages ----------------------------------------------------------------
    def add_message(self, *, mid: str, conv: str, sender: str, direction: str, body: str | None, ts: int,
                    status: str, kind: str = "text", extra: dict | None = None, reply_to: str | None = None,
                    ttl: int = 0, expires_at: int | None = None) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO messages(mid,conv,sender,direction,kind,body,extra,ts,status,reply_to,expires_at,ttl) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, conv, sender, direction, kind, self._enc(body, mid), self._enc(extra, mid), ts, status, reply_to, expires_at, ttl))
        return cur.rowcount == 1

    def _row_to_msg(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["body"] = self._dec_text(r["body"], r["mid"])
        d["extra"] = json.loads(self._dec_text(r["extra"], r["mid"]) or "null")
        return d

    def get_message(self, mid: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM messages WHERE mid=?", (mid,)).fetchone()
        return self._row_to_msg(r) if r else None

    def list_messages(self, conv: str, before: tuple[int, int] | None = None, limit: int = 100) -> list[dict]:
        if before:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE conv=? AND (ts,id)<(?,?) ORDER BY ts DESC,id DESC LIMIT ?", (conv, *before, limit))
        else:
            rows = self.conn.execute("SELECT * FROM messages WHERE conv=? ORDER BY ts DESC,id DESC LIMIT ?", (conv, limit))
        return [self._row_to_msg(r) for r in reversed(rows.fetchall())]

    def update_message(self, mid: str, **fields: Any) -> None:
        allowed = {"status", "read_at", "expires_at", "extra", "body"}
        if not set(fields) <= allowed:
            raise ValueError("champ de message non modifiable")
        if "extra" in fields:
            fields["extra"] = self._enc(fields["extra"], mid)
        if "body" in fields:
            fields["body"] = self._enc(fields["body"], mid)
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE messages SET {sets} WHERE mid=?", (*fields.values(), mid))

    def delete_message(self, mid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM receipts WHERE mid=?", (mid,))
            c.execute("DELETE FROM reactions WHERE mid=?", (mid,))
            c.execute("DELETE FROM messages WHERE mid=?", (mid,))

    def unread_mids(self, conv: str) -> list[str]:
        return [r["mid"] for r in self.conn.execute(
            "SELECT mid FROM messages WHERE conv=? AND direction='in' AND read_at IS NULL ORDER BY id", (conv,))]

    def mark_read(self, conv: str, now: int, ttl_default: int = 0) -> list[str]:
        mids = self.unread_mids(conv)
        with self.tx() as c:
            for mid in mids:
                c.execute("UPDATE messages SET read_at=?, status='read', "
                          "expires_at=CASE WHEN ttl>0 THEN ?+ttl*1000 ELSE expires_at END WHERE mid=?", (now, now, mid))
        return mids

    def unread_counts(self) -> dict[str, int]:
        return {r["conv"]: r["n"] for r in self.conn.execute(
            "SELECT conv, COUNT(*) n FROM messages WHERE direction='in' AND read_at IS NULL AND kind!='system' GROUP BY conv")}

    def last_messages(self) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE id IN (SELECT MAX(id) FROM messages GROUP BY conv)").fetchall()
        return {r["conv"]: self._row_to_msg(r) for r in rows}

    def search(self, query: str, conv: str | None = None, limit: int = 50, scan_limit: int = 50000) -> list[dict]:
        needle = query.casefold().strip()
        if not needle:
            return []
        sql = "SELECT * FROM messages WHERE kind IN ('text','file')" + (" AND conv=?" if conv else "") + " ORDER BY ts DESC,id DESC LIMIT ?"
        args: tuple = (conv, scan_limit) if conv else (scan_limit,)
        out: list[dict] = []
        for r in self.conn.execute(sql, args):
            m = self._row_to_msg(r)
            hay = (m["body"] or "") + " " + ((m["extra"] or {}).get("name", "") if isinstance(m["extra"], dict) else "")
            if needle in hay.casefold():
                out.append(m)
                if len(out) >= limit:
                    break
        return out

    def delete_expired(self, now: int) -> list[tuple[str, str]]:
        rows = self.conn.execute("SELECT mid, conv FROM messages WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
        with self.tx() as c:
            for r in rows:
                c.execute("DELETE FROM receipts WHERE mid=?", (r["mid"],))
                c.execute("DELETE FROM reactions WHERE mid=?", (r["mid"],))
                c.execute("DELETE FROM messages WHERE mid=?", (r["mid"],))
        return [(r["mid"], r["conv"]) for r in rows]

    # -- accusés par destinataire --------------------------------------------------
    def init_receipts(self, mid: str, peers: Iterable[str]) -> None:
        for p in peers:
            self.conn.execute("INSERT OR IGNORE INTO receipts(mid,peer,state) VALUES(?,?,'pending')", (mid, p))

    def advance_receipt(self, mid: str, peer: str, state: str) -> str | None:
        """Fait progresser (jamais reculer) l'état d'un destinataire. Retourne le nouveau statut global ou None."""
        r = self.conn.execute("SELECT state FROM receipts WHERE mid=? AND peer=?", (mid, peer)).fetchone()
        if r is None or RECEIPT_RANK[state] <= RECEIPT_RANK[r["state"]]:
            return None
        self.conn.execute("UPDATE receipts SET state=? WHERE mid=? AND peer=?", (state, mid, peer))
        return self.refresh_status(mid)

    def refresh_status(self, mid: str) -> str | None:
        m = self.conn.execute("SELECT status FROM messages WHERE mid=?", (mid,)).fetchone()
        if m is None or m["status"] == "failed":
            return None
        ranks = [RECEIPT_RANK[r["state"]] for r in self.conn.execute("SELECT state FROM receipts WHERE mid=?", (mid,))]
        if not ranks:
            return None
        status = _RANK_STATUS[min(ranks)]
        if status != m["status"]:
            self.conn.execute("UPDATE messages SET status=? WHERE mid=?", (status, mid))
        return status

    def receipts(self, mid: str) -> dict[str, str]:
        return {r["peer"]: r["state"] for r in self.conn.execute("SELECT peer,state FROM receipts WHERE mid=?", (mid,))}

    # -- boîte d'envoi -------------------------------------------------------------
    def outbox_add(self, env_mid: str, to_id: str, envelope: dict, kind: str = "msg", msg_mid: str | None = None) -> None:
        now = now_ms()
        self.conn.execute(
            "INSERT OR IGNORE INTO outbox(env_mid,msg_mid,to_id,envelope,kind,next_try,created_at) VALUES(?,?,?,?,?,?,?)",
            (env_mid, msg_mid, to_id, json.dumps(envelope), kind, now, now))

    def outbox_due(self, now: int, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM outbox WHERE state='queued' AND next_try<=? ORDER BY id LIMIT ?", (now, limit)).fetchall()
        return [{**dict(r), "envelope": json.loads(r["envelope"])} for r in rows]

    def outbox_next_time(self) -> int | None:
        r = self.conn.execute("SELECT MIN(next_try) t FROM outbox WHERE state='queued'").fetchone()
        return r["t"]

    def outbox_update(self, row_id: int, **fields: Any) -> None:
        if not set(fields) <= {"attempts", "next_try", "state"}:
            raise ValueError("champ non modifiable")
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE outbox SET {sets} WHERE id=?", (*fields.values(), row_id))

    def outbox_delete(self, row_id: int) -> None:
        self.conn.execute("DELETE FROM outbox WHERE id=?", (row_id,))

    def outbox_for_message(self, msg_mid: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT id,env_mid,to_id,state,attempts FROM outbox WHERE msg_mid=?", (msg_mid,))]

    def outbox_delete_for(self, msg_mid: str, to_id: str) -> None:
        self.conn.execute("DELETE FROM outbox WHERE msg_mid=? AND to_id=?", (msg_mid, to_id))

    def outbox_requeue_message(self, msg_mid: str) -> None:
        self.conn.execute("UPDATE outbox SET state='queued', attempts=0, next_try=? WHERE msg_mid=?", (now_ms(), msg_mid))

    def outbox_wake_pending(self) -> None:
        """Reconnexion : réessayer tout de suite ce qui n'a jamais été accepté par un transport."""
        self.conn.execute(
            "UPDATE outbox SET next_try=? WHERE state='queued' AND attempts>0", (now_ms(),))

    # -- déduplication -------------------------------------------------------------
    def is_seen(self, mid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM seen WHERE mid=?", (mid,)).fetchone() is not None

    def mark_seen(self, mid: str) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO seen(mid,ts) VALUES(?,?)", (mid, now_ms()))
        return cur.rowcount == 1

    def prune_seen(self, older_than_ms: int) -> None:
        self.conn.execute("DELETE FROM seen WHERE ts<?", (now_ms() - older_than_ms,))

    # -- conversation settings -------------------------------------------------------
    def conv_settings(self, conv: str) -> dict:
        r = self.conn.execute("SELECT * FROM conv_settings WHERE conv=?", (conv,)).fetchone()
        return dict(r) if r else {"conv": conv, "ttl": 0, "muted": 0, "pinned": 0}

    def set_conv_setting(self, conv: str, **fields: Any) -> None:
        if not set(fields) <= {"ttl", "muted", "pinned"}:
            raise ValueError("paramètre inconnu")
        self.conn.execute("INSERT OR IGNORE INTO conv_settings(conv) VALUES(?)", (conv,))
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE conv_settings SET {sets} WHERE conv=?", (*fields.values(), conv))

    # -- transferts de fichiers --------------------------------------------------------
    def put_transfer(self, **t: Any) -> None:
        cols = ",".join(t)
        self.conn.execute(f"INSERT OR REPLACE INTO transfers({cols}) VALUES({','.join('?' * len(t))})", tuple(t.values()))

    def get_transfer(self, fid: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM transfers WHERE fid=?", (fid,)).fetchone()
        return dict(r) if r else None

    def update_transfer(self, fid: str, **fields: Any) -> None:
        if not set(fields) <= {"next_idx", "state", "path"}:
            raise ValueError("champ non modifiable")
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE transfers SET {sets} WHERE fid=?", (*fields.values(), fid))

    def active_transfers(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM transfers WHERE state IN ('sending','receiving')")]
