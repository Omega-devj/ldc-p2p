"""Groupes privés.

Modèle : le propriétaire fait autorité sur la liste des membres (version croissante). Chaque message
de groupe est chiffré séparément pour chaque membre (même mécanisme que les messages privés), donc
le serveur ne voit jamais le contenu ni la liste des membres. Les membres inconnus sont conservés
comme contacts de type « group » (clés épinglées via leur identifiant auto-certifié) : ils ne
peuvent pas vous écrire en privé tant que vous n'avez pas accepté une demande de contact.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..crypto.e2e import new_mid, now_ms
from ..crypto.identity import ID_RE, b64, clean_name, derive_ldc_id, unb64
from .core import ChatEngine, DeferEnvelope, EngineError

log = logging.getLogger("ldc.groups")
GROUP_PREFIX = "grp:"
MAX_GROUP_MEMBERS = 50
_GID_RE = re.compile(r"^[0-9a-f]{16}$")


class GroupsMixin(ChatEngine):
    # -- aiguillage des conversations ---------------------------------------------
    def _conv_recipients(self, conv: str) -> list[str]:
        if not conv.startswith(GROUP_PREFIX):
            return super()._conv_recipients(conv)
        g = self.db.get_group(conv[len(GROUP_PREFIX):])
        if not g or not g["active"]:
            raise EngineError("Ce groupe n'existe plus.")
        peers = []
        for m in g["members"]:
            if m == self.me:
                continue
            c = self.db.get_contact(m)
            if c and c["state"] in ("accepted", "group"):
                peers.append(m)
        if not peers:
            raise EngineError("Aucun membre joignable dans ce groupe.")
        return peers

    def _msg_extra_inner(self, conv: str) -> dict:
        return {"g": conv[len(GROUP_PREFIX):]} if conv.startswith(GROUP_PREFIX) else super()._msg_extra_inner(conv)

    def _typing_targets(self, conv: str) -> list[str]:
        if conv.startswith(GROUP_PREFIX):
            try:
                return self._conv_recipients(conv)
            except EngineError:
                return []
        return super()._typing_targets(conv)

    def _group_of(self, conv: str) -> str | None:
        return conv[len(GROUP_PREFIX):] if conv.startswith(GROUP_PREFIX) else None

    def _typing_conv(self, sender: str, inner: dict) -> str:
        g = inner.get("g")
        return GROUP_PREFIX + g if isinstance(g, str) and self._is_member(g, sender) else sender

    def _is_member(self, gid: str, peer: str) -> bool:
        g = self.db.get_group(gid) if isinstance(gid, str) and _GID_RE.match(gid) else None
        return bool(g and g["active"] and peer in g["members"] and self.me in g["members"])

    def _incoming_conv(self, sender: str, inner: dict) -> str | None:
        g = inner.get("g")
        if g is None:
            c = self.db.get_contact(sender)
            return sender if c and c["state"] == "accepted" else None
        if not isinstance(g, str) or not _GID_RE.match(g):
            return None
        if self.db.get_group(g) is None:
            raise DeferEnvelope(g)
        return GROUP_PREFIX + g if self._is_member(g, sender) else None

    def _group_conversations(self, unread: dict, last: dict) -> list[dict]:
        out = []
        for g in self.db.list_groups():
            conv = GROUP_PREFIX + g["gid"]
            s = self._conv_summary(conv, "group", g["name"], "accepted", unread, last)
            s["members"] = g["members"]
            s["owner"] = g["owner"]
            out.append(s)
        return out

    # -- gestion ---------------------------------------------------------------------
    def _member_entries(self, ids: list[str]) -> list[dict]:
        out = []
        for i in ids:
            pub = self.identity.public if i == self.me else self.pub_of(i)
            if pub is None:
                raise EngineError("Membre inconnu")
            name = self.identity.name if i == self.me else pub.name
            out.append({"id": i, "name": name, "sk": b64(pub.sign_pk), "bk": b64(pub.box_pk)})
        return out

    def _broadcast_group(self, gid: str, also: list[str] | None = None) -> None:
        g = self.db.get_group(gid)
        assert g is not None
        inner = {"t": "ginv", "g": gid, "name": g["name"], "owner": g["owner"], "v": g["version"],
                 "m": self._member_entries(g["members"])}
        for peer in set(g["members"]) | set(also or []):
            if peer != self.me and self.db.get_contact(peer):
                self._enqueue(peer, inner, kind="ctl")

    def _system_note(self, conv: str, text: str) -> None:
        self.db.add_message(mid=f"sys-{new_mid()}", conv=conv, sender=self.me, direction="in", body=text,
                            ts=now_ms(), status="received", kind="system", )
        self.db.conn.execute("UPDATE messages SET read_at=? WHERE kind='system' AND read_at IS NULL", (now_ms(),))
        self.events.emit("conversation_update", conv=conv)

    async def create_group(self, name: str, member_ids: list[str]) -> str:
        name = clean_name(name, "")
        if not name:
            raise EngineError("Donnez un nom au groupe.")
        ids = list(dict.fromkeys(member_ids))
        if not ids or len(ids) + 1 > MAX_GROUP_MEMBERS:
            raise EngineError(f"Un groupe contient de 2 à {MAX_GROUP_MEMBERS} membres.")
        for i in ids:
            c = self.db.get_contact(i)
            if not c or c["state"] != "accepted":
                raise EngineError("Seuls vos contacts acceptés peuvent rejoindre un groupe.")
        gid = new_mid()[:16]
        self.db.put_group(gid, name, self.me, [self.me, *ids], 1)
        self._broadcast_group(gid)
        self._system_note(GROUP_PREFIX + gid, f"Groupe « {name} » créé")
        self._wake.set()
        self.events.emit("conversation_update", conv=GROUP_PREFIX + gid)
        return GROUP_PREFIX + gid

    def _owned_group(self, conv: str) -> dict:
        g = self.db.get_group(conv[len(GROUP_PREFIX):]) if conv.startswith(GROUP_PREFIX) else None
        if not g or not g["active"]:
            raise EngineError("Groupe introuvable")
        if g["owner"] != self.me:
            raise EngineError("Seul le créateur du groupe peut le modifier.")
        return g

    async def group_add_members(self, conv: str, ids: list[str]) -> None:
        g = self._owned_group(conv)
        members = list(dict.fromkeys(g["members"] + ids))
        if len(members) > MAX_GROUP_MEMBERS:
            raise EngineError("Groupe complet")
        for i in ids:
            c = self.db.get_contact(i)
            if not c or c["state"] not in ("accepted", "group"):
                raise EngineError("Contact inconnu")
        self.db.put_group(g["gid"], g["name"], self.me, members, g["version"] + 1)
        self._broadcast_group(g["gid"])
        self._wake.set()
        self.events.emit("conversation_update", conv=conv)

    async def group_remove_member(self, conv: str, member: str) -> None:
        g = self._owned_group(conv)
        if member == self.me:
            raise EngineError("Utilisez « Supprimer le groupe ».")
        members = [m for m in g["members"] if m != member]
        self.db.put_group(g["gid"], g["name"], self.me, members, g["version"] + 1)
        self._broadcast_group(g["gid"], also=[member])  # le membre retiré apprend qu'il n'est plus dans la liste
        self._wake.set()
        self.events.emit("conversation_update", conv=conv)

    async def group_rename(self, conv: str, name: str) -> None:
        g = self._owned_group(conv)
        name = clean_name(name, "")
        if not name:
            raise EngineError("Nom invalide")
        self.db.put_group(g["gid"], name, self.me, g["members"], g["version"] + 1)
        self._broadcast_group(g["gid"])
        self._wake.set()
        self.events.emit("conversation_update", conv=conv)

    async def leave_group(self, conv: str) -> None:
        g = self.db.get_group(conv[len(GROUP_PREFIX):]) if conv.startswith(GROUP_PREFIX) else None
        if not g:
            raise EngineError("Groupe introuvable")
        if g["owner"] == self.me:  # le propriétaire dissout : plus personne n'est membre
            self.db.put_group(g["gid"], g["name"], self.me, [self.me], g["version"] + 1)
            self._broadcast_group(g["gid"], also=g["members"])
        else:
            self._enqueue(g["owner"], {"t": "gleave", "g": g["gid"]}, kind="ctl")
        self.db.deactivate_group(g["gid"])
        self._wake.set()
        self.events.emit("conversation_update", conv=conv)

    # -- réception ----------------------------------------------------------------------
    async def _dispatch_ext(self, sender: str, contact: Any, env: dict, inner: dict) -> None:
        t = inner["t"]
        if t == "ginv":
            await self._on_group_invite(sender, contact, inner)
        elif t == "gleave":
            g = self.db.get_group(str(inner.get("g")))
            if g and g["active"] and g["owner"] == self.me and sender in g["members"]:
                members = [m for m in g["members"] if m != sender]
                self.db.put_group(g["gid"], g["name"], self.me, members, g["version"] + 1)
                self._broadcast_group(g["gid"])
                self._wake.set()
                self.events.emit("conversation_update", conv=GROUP_PREFIX + g["gid"])
        else:
            await super()._dispatch_ext(sender, contact, env, inner)

    async def _on_group_invite(self, sender: str, contact: Any, inner: dict) -> None:
        gid, name, owner, version, raw = inner.get("g"), inner.get("name"), inner.get("owner"), inner.get("v"), inner.get("m")
        if not (isinstance(gid, str) and _GID_RE.match(gid) and isinstance(name, str) and isinstance(version, int)
                and isinstance(owner, str) and ID_RE.match(owner) and isinstance(raw, list) and 0 < len(raw) <= MAX_GROUP_MEMBERS):
            return
        if sender != owner:
            return
        existing = self.db.get_group(gid)
        if existing:
            if existing["owner"] != owner or version <= existing["version"]:
                return
        elif contact["state"] != "accepted":
            return  # on ne rejoint de nouveaux groupes que sur invitation d'un contact accepté
        entries: dict[str, tuple[str, bytes, bytes]] = {}
        try:
            for m in raw:
                sk, bk = unb64(m["sk"]), unb64(m["bk"])
                if len(sk) != 32 or len(bk) != 32 or derive_ldc_id(sk, bk) != m["id"]:
                    return  # entrée falsifiée : on rejette toute la mise à jour
                entries[m["id"]] = (clean_name(str(m.get("name", ""))), sk, bk)
        except (KeyError, ValueError, TypeError):
            return
        if self.me not in entries:
            if existing and existing["active"]:
                self.db.deactivate_group(gid)
                self._system_note(GROUP_PREFIX + gid, "Vous n'êtes plus membre de ce groupe")
            return
        for mid_, (nm, sk, bk) in entries.items():
            if mid_ != self.me and self.db.get_contact(mid_) is None:
                self.db.put_contact(mid_, nm, sk, bk, "group")
                self._pub_cache.pop(mid_, None)
        self.db.put_group(gid, clean_name(name, "Groupe"), owner, list(entries), version)
        self._system_note(GROUP_PREFIX + gid, "Vous avez rejoint le groupe" if not existing else "Le groupe a été mis à jour")
        self.events.emit("contact_update", id=None)
        for env in self._deferred.pop(gid, []):
            await self._handle_envelope(env, "deferred")
