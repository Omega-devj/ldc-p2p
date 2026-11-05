"""Moteur de messagerie LDC (sans Qt) : identité -> contacts -> messages fiables.

Garanties visées (et testées) :
  * un message est enregistré localement AVANT tout envoi réseau (jamais perdu silencieusement) ;
  * statuts honnêtes : pending -> sent (accepté par un transport) -> delivered (le destinataire a
    persisté et accusé réception de bout en bout) -> read ; failed (récupérable via retry) ;
  * envoi idempotent : identifiant unique par message, déduplication à la réception ;
  * reprise automatique avec backoff, reprise après redémarrage (la boîte d'envoi est en base) ;
  * seuls les contacts acceptés, non bloqués, avec clé épinglée, peuvent écrire.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

from ..crypto import e2e
from ..crypto.e2e import EnvelopeError, decode_card, make_card, new_mid, now_ms, open_envelope, parse_card, seal, validate_envelope
from ..crypto.identity import ID_RE, Identity, PublicIdentity, clean_name, safety_number
from ..network.direct import DirectManager, local_addresses
from ..network.relay_client import ChannelUnavailable, RelayClient
from ..storage.database import Database
from .events import EventBus
from .settings import STATUSES, EngineSettings

log = logging.getLogger("ldc.engine")

MAX_TEXT = 8000
MAX_AGE_MS = 14 * 86400 * 1000        # au-delà : échec définitif (récupérable manuellement)
RESEND_AFTER_MS = 12 * 3600 * 1000    # message accepté par le serveur mais jamais acquitté
MAX_PENDING_REQUESTS = 100
ALLOWED_REACTIONS = ("👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "🎉")
SEEN_RETENTION_MS = 30 * 86400 * 1000


def backoff_ms(attempts: int) -> int:
    return int(min(900, 5 * 3 ** min(attempts, 6)) * 1000)


class EngineError(Exception):
    """Erreur destinée à être affichée telle quelle à l'utilisateur."""


class DeferEnvelope(Exception):
    """Enveloppe valide mais dont le contexte (ex. groupe) n'est pas encore connu."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class ChatEngine:
    def __init__(self, identity: Identity, db: Database, settings: EngineSettings | None = None,
                 download_dir: Path | None = None):
        self.identity, self.db = identity, db
        self.settings = settings or EngineSettings.load(db)
        self.events = EventBus()
        self.download_dir = Path(download_dir or self.settings.download_dir or (Path.home() / "Downloads" / "LDC P2P"))
        self.relay: RelayClient | None = None
        self.direct: DirectManager | None = None
        self.server_online: dict[str, bool] = {}
        self.peer_status: dict[str, str] = {}
        self._pub_cache: dict[str, PublicIdentity] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wake = asyncio.Event()
        self._stopping = False
        self._deferred: dict[str, list[dict]] = {}
        self.activity: deque = deque(maxlen=300)   # journal lisible (sans contenu de message)
        self.recv_count = 0
        self.reject_reasons: Counter = Counter()
        self._selftests: dict[str, asyncio.Future] = {}
        self._init_extensions()

    def note(self, text: str) -> None:
        """Entrée du journal d'activité (affichée dans le diagnostic ; jamais de contenu de message)."""
        self.activity.append((time.time(), text))
        log.info(text)

    def _reject(self, reason: str) -> bool:
        self.reject_reasons[reason] += 1
        self.note(f"← enveloppe ignorée : {reason}")
        return True

    def _init_extensions(self) -> None:  # surchargé par les mixins
        pass

    # ------------------------------------------------------------------ cycle de vie
    @property
    def me(self) -> str:
        return self.identity.id

    def spawn(self, coro, name: str = "task") -> asyncio.Task:
        t = asyncio.create_task(coro, name=name)
        self._tasks.add(t)

        def done(task: asyncio.Task) -> None:
            self._tasks.discard(task)
            if not task.cancelled() and task.exception():
                log.error("Tâche %s en erreur: %r", name, task.exception())

        t.add_done_callback(done)
        return t

    async def start(self) -> None:
        s = self.settings
        if s.direct_enabled:
            await self._start_direct()
            await self._start_lan()
        if s.server_host:
            self.relay = RelayClient(self.identity, s.server_host, s.server_port, tls=s.server_tls,
                                     visible=s.status != "invisible", token=s.server_token, on_deliver=self._on_relay_env,
                                     on_presence=self._on_server_presence, on_state=self._on_relay_state)
            self.relay.start()
        self.spawn(self._outbox_loop(), "outbox")
        self.spawn(self._maintenance_loop(), "maintenance")
        self._resume_extensions()
        self.events.emit("started")

    def _resume_extensions(self) -> None:
        pass

    async def _start_lan(self) -> None:
        """Découverte des contacts sur le réseau local, sans aucun serveur."""
        self.lan = None
        if not (self.settings.lan_discovery and self.direct):
            return
        from ..network.lan import LanDiscovery
        try:
            self.lan = LanDiscovery(self.me, self.direct.port, lambda: [c["id"] for c in self.db.list_contacts({"accepted"})], self._on_lan_peer)
            await self.lan.start()
        except OSError as e:
            log.info("Découverte réseau local indisponible: %s", e)
            self.lan = None

    def _on_lan_peer(self, cid: str, host: str, port: int) -> None:
        if self.direct and not self.direct.link_for(cid) and self._candidate_allowed(host):
            self.note(f"réseau local : contact …{cid[-6:]} trouvé à {host}")
            self.spawn(self._try_direct(cid, [[host, port]]), "lan-direct")

    async def connect_direct_manual(self, contact_id: str, host: str, port: int = 0) -> bool:
        """Connexion directe sans serveur ni découverte : adresse saisie à la main (VPN, IPv6, port redirigé)."""
        pub = self._link_lookup(contact_id)
        if not self.direct or pub is None:
            raise EngineError("Connexions directes désactivées ou contact inconnu.")
        ok = await self.direct.connect(pub, [(host.strip(), port or self.direct.port)])
        self.events.emit("presence", id=contact_id)
        return ok

    async def _start_direct(self) -> None:
        s = self.settings
        self.direct = DirectManager(self.identity, self._link_lookup, self._on_link_env, "0.0.0.0", s.listen_port)
        try:
            await self.direct.start()
        except OSError as e:
            log.warning("Écoute directe impossible: %s", e)
            self.direct = None

    async def set_direct_enabled(self, enabled: bool) -> None:
        """Applique immédiatement le réglage des connexions directes (sans redémarrage)."""
        self.settings.direct_enabled = enabled
        self.settings.save(self.db)
        if enabled and self.direct is None:
            await self._start_direct()
            for c in self.db.list_contacts({"accepted"}):
                if self.server_online.get(c["id"]):
                    self.spawn(self._on_contact_online(c["id"]), "contact-online")
        elif not enabled and self.direct is not None:
            d, self.direct = self.direct, None
            if getattr(self, "lan", None):
                await self.lan.stop()
                self.lan = None
            await d.stop()
        self.events.emit("presence", id=None)

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self.relay:
            await self.relay.stop()
        if getattr(self, "lan", None):
            await self.lan.stop()
        if self.direct:
            await self.direct.stop()
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def apply_server_settings(self) -> None:
        """Après modification du serveur dans les paramètres : redémarrer le client relais."""
        self.settings.save(self.db)
        self.spawn(self._restart_relay(), "restart-relay")

    async def _restart_relay(self) -> None:
        if self.relay:
            await self.relay.stop()
            self.relay = None
        s = self.settings
        if s.server_host and not self._stopping:
            self.relay = RelayClient(self.identity, s.server_host, s.server_port, tls=s.server_tls,
                                     visible=s.status != "invisible", token=s.server_token, on_deliver=self._on_relay_env,
                                     on_presence=self._on_server_presence, on_state=self._on_relay_state)
            self.relay.start()
        else:
            self.events.emit("connection_state", state="disconnected", info={})

    # ------------------------------------------------------------------ identité / contacts
    @property
    def server_hint(self) -> str | None:
        s = self.settings
        return f"{s.server_host}:{s.server_port}" if s.server_host else None

    def my_card_code(self) -> str:
        return e2e.encode_card(make_card(self.identity, self.server_hint))

    def pub_of(self, contact_id: str) -> PublicIdentity | None:
        if contact_id in self._pub_cache:
            return self._pub_cache[contact_id]
        c = self.db.get_contact(contact_id)
        if c is None:
            return None
        pub = PublicIdentity(bytes(c["sign_pk"]), bytes(c["box_pk"]), c["name"])
        if pub.id != contact_id:  # base altérée : on refuse d'utiliser cette clé
            log.error("Contact incohérent en base, ignoré")
            return None
        self._pub_cache[contact_id] = pub
        return pub

    def _link_lookup(self, peer_id: str) -> PublicIdentity | None:
        c = self.db.get_contact(peer_id)
        return self.pub_of(peer_id) if c and c["state"] == "accepted" else None

    def contact_safety_number(self, contact_id: str) -> str:
        pub = self.pub_of(contact_id)
        if pub is None:
            raise EngineError("Contact inconnu")
        return safety_number(self.identity.public, pub)

    async def add_contact_from_code(self, code: str, greeting: str = "") -> dict:
        try:
            pub, hint = parse_card(decode_card(code))
        except e2e.CardError as e:
            raise EngineError(str(e)) from e
        if pub.id == self.me:
            raise EngineError("C'est votre propre code d'invitation.")
        existing = self.db.get_contact(pub.id)
        if existing:
            st = existing["state"]
            if st == "blocked":
                raise EngineError("Ce contact est bloqué. Débloquez-le d'abord.")
            if st == "pending_in":
                await self.accept_request(pub.id)
                return dict(self.db.get_contact(pub.id))
            if st == "accepted":
                raise EngineError("Ce contact est déjà dans votre liste.")
            if st == "pending_out":
                return dict(existing)
        self.db.put_contact(pub.id, pub.name, pub.sign_pk, pub.box_pk, "pending_out", hint)
        self._pub_cache.pop(pub.id, None)
        await self._queue_request(pub, greeting)
        self.events.emit("contact_update", id=pub.id)
        return dict(self.db.get_contact(pub.id))

    def request_status(self, contact_id: str) -> str:
        """'offline' (en attente de connexion au serveur), 'queued' (en cours d'envoi), 'sent' (remise au serveur)."""
        row = self.db.conn.execute("SELECT 1 FROM outbox WHERE to_id=? AND kind='req' AND state='queued'", (contact_id,)).fetchone()
        if row is None:
            return "sent"
        return "queued" if (self.relay and self.relay.connected) else "offline"

    async def request_contact(self, contact_id: str, greeting: str = "") -> None:
        """Transforme un membre de groupe en contact direct."""
        c = self.db.get_contact(contact_id)
        pub = self.pub_of(contact_id)
        if c is None or pub is None or c["state"] != "group":
            raise EngineError("Contact introuvable")
        self.db.update_contact(contact_id, state="pending_out")
        await self._queue_request(pub, greeting)
        self.events.emit("contact_update", id=contact_id)

    async def _queue_request(self, pub: PublicIdentity, greeting: str) -> None:
        env = seal(self.identity, pub, {"t": "req", "greeting": clean_name(greeting, "")[:200] if greeting else ""},
                   kind="r", card=make_card(self.identity, self.server_hint))
        self.db.outbox_add(env["id"], pub.id, env, kind="req")
        self._wake.set()

    async def accept_request(self, contact_id: str) -> None:
        c, pub = self.db.get_contact(contact_id), self.pub_of(contact_id)
        if c is None or pub is None or c["state"] != "pending_in":
            raise EngineError("Aucune demande en attente pour ce contact")
        self.db.update_contact(contact_id, state="accepted")
        env = seal(self.identity, pub, {"t": "req_ok"})
        self.db.outbox_add(env["id"], contact_id, env, kind="resp")
        self._wake.set()
        self._contact_became_accepted(contact_id)

    def _contact_became_accepted(self, contact_id: str) -> None:
        self.events.emit("contact_update", id=contact_id)
        self.events.emit("conversation_update", conv=contact_id)
        self.spawn(self._refresh_watch(), "watch")

    async def decline_request(self, contact_id: str) -> None:
        c = self.db.get_contact(contact_id)
        if c and c["state"] == "pending_in":
            self.db.delete_contact(contact_id)
            self._pub_cache.pop(contact_id, None)
            self.events.emit("contact_update", id=contact_id)

    async def cancel_request(self, contact_id: str) -> None:
        c = self.db.get_contact(contact_id)
        if c and c["state"] == "pending_out":
            self.db.delete_contact(contact_id)
            self._pub_cache.pop(contact_id, None)
            self.events.emit("contact_update", id=contact_id)

    async def block(self, contact_id: str) -> None:
        if not self.db.get_contact(contact_id):
            raise EngineError("Contact inconnu")
        self.db.update_contact(contact_id, state="blocked")
        for row in self.db.conn.execute("SELECT id FROM outbox WHERE to_id=?", (contact_id,)).fetchall():
            self.db.outbox_delete(row["id"])
        if self.direct and (l := self.direct.link_for(contact_id)):
            l.close()
        self.peer_status.pop(contact_id, None)
        self.events.emit("contact_update", id=contact_id)
        self.spawn(self._refresh_watch(), "watch")

    async def unblock(self, contact_id: str) -> None:
        c = self.db.get_contact(contact_id)
        if c and c["state"] == "blocked":
            self.db.update_contact(contact_id, state="accepted")
            self.events.emit("contact_update", id=contact_id)
            self.spawn(self._refresh_watch(), "watch")

    async def delete_contact(self, contact_id: str) -> None:
        self.db.delete_contact(contact_id)
        self._pub_cache.pop(contact_id, None)
        self.events.emit("contact_update", id=contact_id)
        self.events.emit("conversation_update", conv=contact_id)
        self.spawn(self._refresh_watch(), "watch")

    def set_verified(self, contact_id: str, verified: bool) -> None:
        self.db.update_contact(contact_id, verified=1 if verified else 0)
        self.events.emit("contact_update", id=contact_id)

    def set_alias(self, contact_id: str, alias: str) -> None:
        self.db.update_contact(contact_id, alias=clean_name(alias, "") or None)
        self.events.emit("contact_update", id=contact_id)

    # ------------------------------------------------------------------ transport
    async def _transmit(self, to_id: str, env: dict, ephemeral: bool = False) -> str:
        if self.direct and (link := self.direct.link_for(to_id)):
            try:
                return await link.send_env(env)
            except (ConnectionError, OSError):
                pass
        if self.relay and self.relay.connected:
            return await self.relay.send_env(to_id, env, ephemeral)
        raise ChannelUnavailable("aucun transport disponible")

    async def _send_ctl(self, to_id: str, inner: dict, ephemeral: bool = False) -> None:
        """Message de contrôle sans reprise (accusés, frappe, présence)."""
        pub = self.pub_of(to_id)
        if pub is None:
            return
        try:
            await self._transmit(to_id, seal(self.identity, pub, inner), ephemeral)
        except (ChannelUnavailable, ConnectionError, OSError, EnvelopeError) as e:
            log.debug("Contrôle %s non envoyé: %s", inner.get("t"), e)

    def _enqueue(self, to_id: str, inner: dict, kind: str = "msg", mid: str | None = None, msg_mid: str | None = None) -> str:
        pub = self.pub_of(to_id)
        if pub is None:
            raise EngineError("Contact inconnu")
        env = seal(self.identity, pub, inner, mid=mid)
        self.db.outbox_add(env["id"], to_id, env, kind=kind, msg_mid=msg_mid)
        self._wake.set()
        return env["id"]

    # ------------------------------------------------------------------ boîte d'envoi
    async def _outbox_loop(self) -> None:
        while not self._stopping:
            try:
                await self._process_outbox()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erreur dans la boîte d'envoi")
            nxt = self.db.outbox_next_time()
            delay = 60.0 if nxt is None else min(60.0, max(0.05, (nxt - now_ms()) / 1000))
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _process_outbox(self) -> None:
        for row in self.db.outbox_due(now_ms()):
            if self._stopping:
                return
            await self._deliver_row(row)

    async def _deliver_row(self, row: dict) -> None:
        rid, to_id, msg_mid, kind = row["id"], row["to_id"], row["msg_mid"], row["kind"]
        c = self.db.get_contact(to_id)
        if c is None or c["state"] == "blocked":
            self.db.outbox_delete(rid)
            return
        if kind == "msg" and c["state"] not in ("accepted", "group"):
            self.db.outbox_update(rid, next_try=now_ms() + 60_000)
            return
        age = now_ms() - row["created_at"]
        try:
            status = await self._transmit(to_id, row["envelope"])
        except (ChannelUnavailable, ConnectionError, OSError) as e:
            attempts = row["attempts"] + 1
            self.note(f"→ {kind} pour …{to_id[-6:]} NON envoyé ({e}) — nouvel essai dans {backoff_ms(attempts) // 1000} s")
            if age > MAX_AGE_MS:
                self._fail_row(row, "délai dépassé")
            else:
                self.db.outbox_update(rid, attempts=attempts, next_try=now_ms() + backoff_ms(attempts))
            log.debug("Envoi différé (%s)", e)
            return
        if status.startswith("rejected"):
            reason = status.split(":", 1)[-1]
            if reason in ("rate", "mailbox_full") and age <= MAX_AGE_MS:
                self.db.outbox_update(rid, attempts=row["attempts"] + 1,
                                      next_try=now_ms() + (30_000 if reason == "rate" else 600_000))
            else:
                self._fail_row(row, reason)
            return
        self.note(f"→ {kind} pour …{to_id[-6:]} : serveur = {status}")
        # accepté par un transport (direct, ou serveur qui a stocké/livré)
        if kind == "msg" and msg_mid:
            self._advance(msg_mid, to_id, "sent")
            self.db.outbox_update(rid, attempts=0, next_try=now_ms() + RESEND_AFTER_MS)
        else:
            self.db.outbox_delete(rid)

    def _fail_row(self, row: dict, reason: str) -> None:
        self.db.outbox_update(row["id"], state="failed")
        if row["msg_mid"]:
            self.db.conn.execute("UPDATE messages SET status='failed' WHERE mid=? AND status IN ('pending','sent')", (row["msg_mid"],))
            self.events.emit("message_status", mid=row["msg_mid"], status="failed")
        log.info("Échec d'envoi (%s)", reason)

    def _advance(self, mid: str, peer: str, state: str) -> None:
        new = self.db.advance_receipt(mid, peer, state)
        if new:
            self.events.emit("message_status", mid=mid, status=new)

    async def retry_message(self, mid: str) -> None:
        m = self.db.get_message(mid)
        if not m or m["direction"] != "out":
            raise EngineError("Message introuvable")
        self.db.update_message(mid, status="pending")
        self.db.outbox_requeue_message(mid)
        self.events.emit("message_status", mid=mid, status="pending")
        self._wake.set()

    # ------------------------------------------------------------------ envoi de messages
    def _conv_recipients(self, conv: str) -> list[str]:
        if ID_RE.match(conv):
            c = self.db.get_contact(conv)
            if c is None or c["state"] != "accepted":
                raise EngineError("Ce contact n'a pas (encore) accepté votre demande.")
            return [conv]
        raise EngineError("Conversation inconnue")

    def _msg_extra_inner(self, conv: str) -> dict:
        return {}

    async def send_text(self, conv: str, text: str, reply_to: str | None = None, mentions: list[str] | None = None) -> str:
        text = (text or "").strip()
        if not text:
            raise EngineError("Message vide")
        if len(text) > MAX_TEXT:
            raise EngineError(f"Message trop long (maximum {MAX_TEXT} caractères)")
        men = [m for m in dict.fromkeys(mentions or []) if ID_RE.match(m)][:20]
        return self._send_message(conv, "text", text, extra={"men": men} if men else None, reply_to=reply_to,
                                  inner_extra={"men": men} if men else None)

    def _send_message(self, conv: str, kind: str, body: str, extra: dict | None, reply_to: str | None = None,
                      mid: str | None = None, inner_extra: dict | None = None) -> str:
        peers = self._conv_recipients(conv)
        mid = mid or new_mid()
        ts = now_ms()
        ttl = self.db.conv_settings(conv)["ttl"]
        with self.db.tx():
            self.db.add_message(mid=mid, conv=conv, sender=self.me, direction="out", body=body, ts=ts, status="pending",
                                kind=kind, extra=extra, reply_to=reply_to, ttl=ttl,
                                expires_at=ts + ttl * 1000 if ttl > 0 else None)
            self.db.init_receipts(mid, peers)
            inner = {"t": "msg", "k": kind, "text": body, "reply": reply_to, "ttl": ttl, "ts": ts,
                     **self._msg_extra_inner(conv), **({"x": extra} if extra else {}), **(inner_extra or {})}
            for p in peers:
                self._enqueue(p, inner, kind="msg", mid=mid, msg_mid=mid)
        self.events.emit("message", conv=conv, mid=mid, direction="out")
        self.events.emit("conversation_update", conv=conv)
        return mid

    async def delete_message(self, mid: str, for_everyone: bool = False) -> None:
        m = self.db.get_message(mid)
        if not m:
            return
        if for_everyone and m["direction"] == "out":
            for peer in self.db.receipts(mid):
                try:
                    self._enqueue(peer, {"t": "del", "m": mid}, kind="ctl")
                except EngineError:
                    pass
        for row in self.db.outbox_for_message(mid):
            self.db.outbox_delete(row["id"])
        self.db.delete_message(mid)
        self.events.emit("message_deleted", mid=mid, conv=m["conv"])
        self.events.emit("conversation_update", conv=m["conv"])

    async def edit_message(self, mid: str, new_text: str) -> None:
        new_text = (new_text or "").strip()
        m = self.db.get_message(mid)
        if not m or m["direction"] != "out" or m["kind"] != "text":
            raise EngineError("Seuls vos messages texte peuvent être modifiés.")
        if not new_text or len(new_text) > MAX_TEXT:
            raise EngineError("Texte invalide")
        extra = dict(m["extra"]) if isinstance(m["extra"], dict) else {}
        extra["edited"] = now_ms()
        self.db.update_message(mid, body=new_text, extra=extra)
        for peer in self.db.receipts(mid):
            try:
                self._enqueue(peer, {"t": "edit", "m": mid, "text": new_text}, kind="ctl")
            except EngineError:
                pass
        self.events.emit("message_updated", mid=mid, conv=m["conv"])
        self.events.emit("conversation_update", conv=m["conv"])

    # ------------------------------------------------------------------ réactions
    def _can_react(self, m: dict | None, peer: str) -> bool:
        if not m or m["kind"] == "system":
            return False
        if m["conv"].startswith("grp:"):
            g = self.db.get_group(m["conv"][4:])
            return bool(g and peer in g["members"])
        return m["conv"] == peer

    async def react(self, mid: str, emoji: str) -> None:
        """Ajoute, remplace ou retire (même emoji une 2ᵉ fois) la réaction de l'utilisateur."""
        if emoji not in ALLOWED_REACTIONS:
            raise EngineError("Réaction non disponible")
        m = self.db.get_message(mid)
        if not m or m["kind"] == "system":
            raise EngineError("Message introuvable")
        current = self.db.reactions_for([mid]).get(mid, {})
        mine = next((e for e, who in current.items() if self.me in who), None)
        new = "" if mine == emoji else emoji
        self.db.set_reaction(mid, self.me, new or None)
        for peer in self._conv_recipients(m["conv"]):
            self._enqueue(peer, {"t": "react", "m": mid, "e": new}, kind="ctl")
        self.events.emit("message_updated", mid=mid, conv=m["conv"])

    def _on_reaction(self, sender: str, inner: dict) -> None:
        mid, e = inner.get("m"), inner.get("e")
        if not isinstance(mid, str) or not isinstance(e, str) or (e and e not in ALLOWED_REACTIONS):
            return
        m = self.db.get_message(mid)
        if self._can_react(m, sender):
            self.db.set_reaction(mid, sender, e or None)
            self.events.emit("message_updated", mid=mid, conv=m["conv"])

    # ------------------------------------------------------------------ noms ressemblants
    def lookalikes(self, contact_id: str, name: str | None = None) -> list[dict]:
        """Autres contacts portant le même nom (usurpation visuelle possible : les identifiants, eux, diffèrent)."""
        c = self.db.get_contact(contact_id)
        n = (name if name is not None else (c["name"] if c else "")).casefold().strip()
        return [o for o in self.db.list_contacts() if o["id"] != contact_id and o["state"] != "blocked"
                and n and (o["name"].casefold().strip() == n or (o["alias"] or "").casefold().strip() == n)]

    def preview_card(self, code: str) -> tuple[PublicIdentity, list[dict]]:
        try:
            pub, _ = parse_card(decode_card(code))
        except e2e.CardError as e:
            raise EngineError(str(e)) from e
        return pub, self.lookalikes(pub.id, pub.name)

    async def send_typing(self, conv: str) -> None:
        if not self.settings.typing_indicators or self.settings.status == "invisible":
            return
        for p in self._typing_targets(conv):
            if self.server_online.get(p) or (self.direct and self.direct.link_for(p)):
                await self._send_ctl(p, {"t": "typing", "g": self._group_of(conv)}, ephemeral=True)

    def _typing_targets(self, conv: str) -> list[str]:
        c = self.db.get_contact(conv) if ID_RE.match(conv) else None
        return [conv] if c and c["state"] == "accepted" else []

    def _group_of(self, conv: str) -> str | None:
        return None

    async def mark_read(self, conv: str) -> None:
        mids = self.db.mark_read(conv, now_ms())
        if not mids:
            return
        self.events.emit("conversation_update", conv=conv)
        if not self.settings.read_receipts or self.settings.status == "invisible":
            return
        by_sender: dict[str, list[str]] = {}
        for mid in mids:
            m = self.db.get_message(mid)
            if m and m["kind"] != "system" and m["sender"] != self.me:
                by_sender.setdefault(m["sender"], []).append(mid)
        for sender, lst in by_sender.items():
            for i in range(0, len(lst), 100):
                await self._send_ctl(sender, {"t": "read", "m": lst[i:i + 100]})

    async def set_conv_ttl(self, conv: str, seconds: int) -> None:
        if seconds < 0 or seconds > 30 * 86400:
            raise EngineError("Durée invalide")
        self.db.set_conv_setting(conv, ttl=seconds)
        self.events.emit("conversation_update", conv=conv)

    # ------------------------------------------------------------------ réception
    async def _on_relay_env(self, env: dict) -> bool:
        return await self._handle_envelope(env, "relay")

    async def _on_link_env(self, peer_id: str, env: dict) -> bool:
        if env.get("from") != peer_id:
            return True
        return await self._handle_envelope(env, "direct")

    async def _handle_envelope(self, env: dict, via: str) -> bool:
        """Retourne True quand l'enveloppe est traitée (ou définitivement inutilisable) : on peut confirmer."""
        try:
            validate_envelope(env)
        except EnvelopeError:
            return self._reject("format invalide")
        if env["to"] != self.me:
            return self._reject("destinée à une autre identité")
        sender = env["from"]
        self.recv_count += 1
        self.note(f"← reçu ({'demande' if env['k'] == 'r' else 'message'}) de …{sender[-6:]} via {via}")
        if sender == self.me:  # test aller-retour avec le serveur
            try:
                inner = open_envelope(env, self.identity, self.identity.public)
                fut = self._selftests.pop(inner.get("n", ""), None) if inner.get("t") == "selftest" else None
                if fut and not fut.done():
                    fut.set_result(True)
            except EnvelopeError:
                pass
            return True
        if env["k"] == "r":
            return self._handle_request(env)
        contact = self.db.get_contact(sender)
        if contact is None:
            return self._reject(f"expéditeur …{sender[-6:]} absent de vos contacts")
        if contact["state"] == "blocked":
            return self._reject("expéditeur bloqué")
        pub = self.pub_of(sender)
        if pub is None:
            return self._reject("clé du contact illisible")
        try:
            inner = open_envelope(env, self.identity, pub)
        except EnvelopeError as e:
            return self._reject(f"signature/déchiffrement refusé ({e})")
        t = inner["t"]
        state = contact["state"]
        allowed = {"accepted": None, "pending_out": {"req_ok"}, "group": {"msg", "ginv", "gleave", "typing", "ack", "read", "react"}}
        if state in allowed and allowed[state] is not None and t not in allowed[state]:
            return True
        if state == "pending_in":
            return self._reject("demande de contact pas encore acceptée")
        seen_key = f"{sender}:{env['id']}"
        if self.db.is_seen(seen_key):  # doublon : on ré-acquitte sans retraiter
            if t == "msg":
                self.spawn(self._send_ctl(sender, {"t": "ack", "m": env["id"]}), "re-ack")
            return True
        try:
            await self._dispatch(sender, contact, env, inner)
        except DeferEnvelope as d:
            queue = self._deferred.setdefault(d.key, [])
            if len(queue) < 200:
                queue.append(env)
            return False  # non confirmé : le serveur le redonnera si besoin
        except Exception:
            log.exception("Traitement du message %s en échec", t)
            return False  # non marqué « vu » : un nouvel essai retraitera
        self.db.mark_seen(seen_key)  # seulement après traitement réussi : aucun message perdu sur erreur
        return True

    def _handle_request(self, env: dict) -> bool:
        try:
            pub, hint = parse_card(env["card"])
            if pub.id != env["from"]:
                return True
            inner = open_envelope(env, self.identity, pub)
        except (e2e.CardError, EnvelopeError):
            return True
        if inner["t"] != "req" or not self.db.mark_seen(f"{pub.id}:{env['id']}"):
            return True
        existing = self.db.get_contact(pub.id)
        if existing:
            st = existing["state"]
            if st == "blocked":
                return self._reject("demande d'un contact bloqué")
            if st == "pending_out":
                self.db.update_contact(pub.id, state="accepted")
                self._pub_cache.pop(pub.id, None)
                try:
                    self._enqueue(pub.id, {"t": "req_ok"}, kind="resp")
                except EngineError:
                    pass
                self._contact_became_accepted(pub.id)
            elif st == "accepted":
                try:
                    self._enqueue(pub.id, {"t": "req_ok"}, kind="resp")
                except EngineError:
                    pass
            elif st == "group":
                self.db.update_contact(pub.id, state="pending_in")
                self.events.emit("contact_request", id=pub.id)
                self.events.emit("contact_update", id=pub.id)
            return True
        pending = len(self.db.list_contacts({"pending_in"}))
        if pending >= MAX_PENDING_REQUESTS:
            return True
        self.db.put_contact(pub.id, pub.name, pub.sign_pk, pub.box_pk, "pending_in", hint)
        greeting = str(inner.get("greeting", ""))[:200]
        if greeting:
            self.db.add_message(mid=f"greet-{pub.id}-{env['id']}", conv=pub.id, sender=pub.id, direction="in",
                                body=greeting, ts=now_ms(), status="received", kind="system")
        self.note(f"✓ demande de contact reçue de « {pub.name} » (…{pub.id[-6:]})")
        self.events.emit("contact_request", id=pub.id)
        self.events.emit("contact_update", id=pub.id)
        return True

    async def _dispatch(self, sender: str, contact: Any, env: dict, inner: dict) -> None:
        t = inner["t"]
        if t == "msg":
            await self._on_msg(sender, env, inner)
        elif t == "ack":
            if isinstance(inner.get("m"), str):
                self._advance(inner["m"], sender, "delivered")
                for row in self.db.outbox_for_message(inner["m"]):
                    if row["to_id"] == sender:
                        self.db.outbox_delete(row["id"])
        elif t == "read":
            for mid in [m for m in inner.get("m", []) if isinstance(m, str)][:100]:
                self._advance(mid, sender, "read")
        elif t == "req_ok":
            if contact["state"] == "pending_out":
                self.db.update_contact(sender, state="accepted")
                self._pub_cache.pop(sender, None)
                self._contact_became_accepted(sender)
        elif t == "typing":
            self.events.emit("typing", conv=self._typing_conv(sender, inner), peer=sender)
        elif t == "presence":
            s = inner.get("s")
            if s in ("online", "away", "dnd"):
                self.peer_status[sender] = s
                self.events.emit("presence", id=sender)
        elif t == "cand":
            self.spawn(self._try_direct(sender, inner.get("a")), "direct")
        elif t == "react":
            self._on_reaction(sender, inner)
        elif t == "edit":
            m = self.db.get_message(str(inner.get("m")))
            text = inner.get("text")
            if (m and m["sender"] == sender and m["direction"] == "in" and m["kind"] == "text"
                    and isinstance(text, str) and 0 < len(text) <= MAX_TEXT):
                extra = dict(m["extra"]) if isinstance(m["extra"], dict) else {}
                extra["edited"] = now_ms()
                self.db.update_message(m["mid"], body=text, extra=extra)
                self.events.emit("message_updated", mid=m["mid"], conv=m["conv"])
                self.events.emit("conversation_update", conv=m["conv"])
        elif t == "del":
            m = self.db.get_message(str(inner.get("m")))
            if m and m["sender"] == sender and m["direction"] == "in":
                self.db.delete_message(m["mid"])
                self.events.emit("message_deleted", mid=m["mid"], conv=m["conv"])
                self.events.emit("conversation_update", conv=m["conv"])
        else:
            await self._dispatch_ext(sender, contact, env, inner)

    async def _dispatch_ext(self, sender: str, contact: Any, env: dict, inner: dict) -> None:
        log.debug("Type de message ignoré: %s", inner.get("t"))

    def _typing_conv(self, sender: str, inner: dict) -> str:
        return sender

    def _incoming_conv(self, sender: str, inner: dict) -> str | None:
        return sender

    async def _on_msg(self, sender: str, env: dict, inner: dict) -> None:
        conv = self._incoming_conv(sender, inner)
        if conv is None:
            return
        kind = inner.get("k", "text")
        text = inner.get("text")
        if kind not in ("text", "file") or not isinstance(text, str) or len(text) > MAX_TEXT * 2:
            return
        ttl = inner.get("ttl") if isinstance(inner.get("ttl"), int) and 0 <= inner["ttl"] <= 30 * 86400 else 0
        reply = inner.get("reply") if isinstance(inner.get("reply"), str) else None
        extra = inner.get("x") if isinstance(inner.get("x"), dict) else None
        men = [m for m in (inner.get("men") if isinstance(inner.get("men"), list) else []) if isinstance(m, str) and ID_RE.match(m)][:20]
        if men and kind == "text":
            extra = {"men": men}
        ts = min(env["ts"], now_ms())  # un horodatage futur n'est pas honoré
        fresh = self.db.add_message(mid=env["id"], conv=conv, sender=sender, direction="in", body=text, ts=ts,
                                    status="received", kind=kind, extra=extra, reply_to=reply, ttl=ttl)
        if not fresh:
            return
        if ttl != self.db.conv_settings(conv)["ttl"]:
            self.db.set_conv_setting(conv, ttl=ttl)
            label = f"Messages éphémères : {ttl // 60} min" if ttl else "Messages éphémères désactivés"
            self.db.add_message(mid=f"sys-{env['id']}", conv=conv, sender=sender, direction="in", body=label,
                                ts=ts, status="received", kind="system")
        if kind == "file":
            self._on_file_offer(sender, conv, env["id"], extra or {})
        await self._send_ctl(sender, {"t": "ack", "m": env["id"]})
        self.events.emit("message", conv=conv, mid=env["id"], direction="in")
        self.events.emit("conversation_update", conv=conv)

    # ------------------------------------------------------------------ présence & connexions
    def _on_relay_state(self, state: str, info: dict) -> None:
        self.note(f"serveur : {state}" + (f" ({info.get('error')})" if info.get("error") else ""))
        self.events.emit("connection_state", state=state, info=info)
        if state == "connected":
            self.db.outbox_wake_pending()
            self._wake.set()
            self.spawn(self._after_connect(), "after-connect")
        elif state in ("disconnected", "replaced"):
            self.server_online.clear()
            self.events.emit("presence", id=None)

    async def _after_connect(self) -> None:
        await self._refresh_watch()

    async def _refresh_watch(self) -> None:
        if not (self.relay and self.relay.connected):
            return
        ids = [c["id"] for c in self.db.list_contacts({"accepted"}) if c["state"] != "blocked"]
        try:
            await self.relay.watch(ids)
        except Exception as e:
            log.debug("watch en échec: %s", e)

    def _on_server_presence(self, states: dict) -> None:
        for cid, online in states.items():
            if not isinstance(online, bool):
                continue
            was = self.server_online.get(cid, False)
            self.server_online[cid] = online
            if not online:
                self.peer_status.pop(cid, None)
            if online and not was:
                self.spawn(self._on_contact_online(cid), "contact-online")
        self.events.emit("presence", id=None)

    async def _on_contact_online(self, cid: str) -> None:
        c = self.db.get_contact(cid)
        if not c or c["state"] != "accepted":
            return
        if self.settings.status != "invisible":
            await self._send_ctl(cid, {"t": "presence", "s": "online" if self.settings.status == "online" else self.settings.status},
                                 ephemeral=True)
        if self.direct and not self.direct.link_for(cid) and self.relay:
            addrs = [[ip, self.direct.port] for ip in local_addresses()]
            if self.relay.observed_ip:
                addrs.append([self.relay.observed_ip, self.direct.port])
            if addrs:
                await self._send_ctl(cid, {"t": "cand", "a": addrs}, ephemeral=True)
        self._contact_online_ext(cid)

    def _contact_online_ext(self, cid: str) -> None:
        pass

    async def _try_direct(self, peer_id: str, addrs: Any) -> None:
        pub = self._link_lookup(peer_id)
        if not self.direct or pub is None or not isinstance(addrs, list):
            return
        cands: list[tuple[str, int]] = []
        for a in addrs[:8]:
            if isinstance(a, list) and len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], int) and 0 < a[1] < 65536 and len(a[0]) < 64:
                if self._candidate_allowed(a[0]):
                    cands.append((a[0], a[1]))
        if cands and await self.direct.connect(pub, cands):
            self.events.emit("presence", id=peer_id)

    allow_loopback_candidates = False  # uniquement pour les tests locaux

    def _candidate_allowed(self, host: str) -> bool:
        """Un contact ne doit pas pouvoir nous faire sonder des adresses sensibles (loopback, multicast, lien-local…)."""
        import ipaddress
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False  # pas de noms d'hôte : uniquement des adresses IP littérales
        if ip.is_loopback:
            return self.allow_loopback_candidates
        return not (ip.is_multicast or ip.is_unspecified or ip.is_link_local or ip.is_reserved)

    def presence_of(self, contact_id: str) -> str:
        if self.direct and self.direct.link_for(contact_id):
            return self.peer_status.get(contact_id, "online")
        if not self.server_online.get(contact_id):
            return "offline"
        return self.peer_status.get(contact_id, "online")

    def is_direct(self, contact_id: str) -> bool:
        return bool(self.direct and self.direct.link_for(contact_id))

    async def set_status(self, status: str) -> None:
        if status not in STATUSES:
            raise EngineError("Statut inconnu")
        self.settings.status = status
        self.settings.save(self.db)
        if self.relay:
            try:
                await self.relay.set_visible(status != "invisible")
            except Exception as e:
                log.debug("changement de visibilité en échec: %s", e)
        if status != "invisible":
            for c in self.db.list_contacts({"accepted"}):
                if self.server_online.get(c["id"]) or self.is_direct(c["id"]):
                    await self._send_ctl(c["id"], {"t": "presence", "s": status}, ephemeral=True)
        self.events.emit("presence", id=None)

    async def self_test(self, timeout: float = 6.0) -> str:
        """Aller-retour complet : on s'envoie un message chiffré PAR LE SERVEUR et on vérifie qu'il revient."""
        if not (self.relay and self.relay.connected):
            return "✗ Vous n'êtes pas connecté au serveur (voir Paramètres › Réseau)."
        n = new_mid()
        env = seal(self.identity, self.identity.public, {"t": "selftest", "n": n})
        fut = asyncio.get_running_loop().create_future()
        self._selftests[n] = fut
        t0 = time.monotonic()
        try:
            status = await self.relay.send_env(self.me, env, ephemeral=True)
            if not status.startswith("delivered"):
                return f"✗ Le serveur a répondu « {status} » (il ne vous remet pas vos propres messages)."
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return "✗ Le serveur a accepté le message mais il n'est jamais revenu : problème de réception côté application."
        except Exception as e:
            return f"✗ Échec : {e}"
        finally:
            self._selftests.pop(n, None)
        return f"✓ Aller-retour réussi en {int((time.monotonic() - t0) * 1000)} ms : l'envoi ET la réception fonctionnent."

    # ------------------------------------------------------------------ maintenance
    async def _maintenance_loop(self) -> None:
        while not self._stopping:
            self.run_maintenance()
            await asyncio.sleep(20)

    def run_maintenance(self) -> None:
        gone = self.db.delete_expired(now_ms())  # global : indépendant de la conversation affichée
        for mid, conv in gone:
            self.events.emit("message_deleted", mid=mid, conv=conv)
            self.events.emit("conversation_update", conv=conv)
        self.db.prune_seen(SEEN_RETENTION_MS)

    # ------------------------------------------------------------------ lecture pour l'interface
    def messages(self, conv: str, before: tuple[int, int] | None = None, limit: int = 100) -> list[dict]:
        return self.db.list_messages(conv, before, limit)

    def search(self, query: str, conv: str | None = None) -> list[dict]:
        return self.db.search(query, conv)

    def contacts(self, states: set[str] | None = None) -> list[dict]:
        return [dict(c) for c in self.db.list_contacts(states)]

    def conversations(self) -> list[dict]:
        unread, last = self.db.unread_counts(), self.db.last_messages()
        out: list[dict] = []
        for c in self.db.list_contacts({"accepted", "pending_out", "pending_in"}):
            out.append(self._conv_summary(c["id"], "dm", c["alias"] or c["name"], c["state"], unread, last))
        out.extend(self._group_conversations(unread, last))
        out.sort(key=lambda x: (-x["pinned"], -(x["last"]["ts"] if x["last"] else 0), x["title"].lower()))
        return out

    def _group_conversations(self, unread: dict, last: dict) -> list[dict]:
        return []

    def _conv_summary(self, conv: str, kind: str, title: str, state: str, unread: dict, last: dict) -> dict:
        cs = self.db.conv_settings(conv)
        return {"conv": conv, "kind": kind, "title": title, "state": state, "unread": unread.get(conv, 0),
                "last": last.get(conv), "ttl": cs["ttl"], "muted": bool(cs["muted"]), "pinned": cs["pinned"],
                "presence": self.presence_of(conv) if kind == "dm" and state == "accepted" else "offline"}

    def display_name(self, contact_id: str) -> str:
        if contact_id == self.me:
            return "Moi"
        c = self.db.get_contact(contact_id)
        return (c["alias"] or c["name"]) if c else "Inconnu"

    # ------------------------------------------------------------------ extension (fichiers) : valeurs par défaut
    def _on_file_offer(self, sender: str, conv: str, mid: str, extra: dict) -> None:
        pass
