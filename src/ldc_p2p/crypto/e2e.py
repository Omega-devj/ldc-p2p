"""Chiffrement de bout en bout des enveloppes et cartes de contact.

Construction (primitives libsodium via PyNaCl, aucune cryptographie maison) :
  * contenu : NaCl `crypto_box` (X25519 + XSalsa20-Poly1305) entre clés statiques
    -> confidentialité + authenticité de l'expéditeur ;
  * en-tête : signature Ed25519 de (version, id, from, to, ts, type, nonce, ciphertext)
    -> liaison cryptographique au destinataire, protection contre la substitution ;
  * identité : l'ID LDC est un hash des clés publiques. L'expéditeur annoncé doit
    correspondre à la clé *épinglée* du contact, jamais à une clé fournie par le paquet.

Limites assumées (documentées dans SECURITY.md) : pas de confidentialité persistante
(forward secrecy) ni de ratchet en v2.0 ; protocole non audité.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
import uuid

from nacl.exceptions import CryptoError
from nacl.public import Box, PublicKey

from .identity import ID_RE, Identity, PublicIdentity, b64, clean_name, unb64

ENV_VERSION = 1
MAX_CT_B64 = 2_000_000
PAD_BLOCK = 256
_ENV_KEYS = {"v", "id", "from", "to", "ts", "k", "n", "ct", "sig", "card"}


class EnvelopeError(Exception):
    pass


class CardError(Exception):
    pass


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def new_mid() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)


def _pad(data: bytes) -> bytes:
    body = struct.pack(">I", len(data)) + data
    return body + b"\x00" * (-len(body) % PAD_BLOCK)


def _unpad(data: bytes) -> bytes:
    if len(data) < 4:
        raise EnvelopeError("contenu trop court")
    (n,) = struct.unpack(">I", data[:4])
    if n > len(data) - 4:
        raise EnvelopeError("longueur incohérente")
    return data[4:4 + n]


def _signed_part(env: dict) -> bytes:
    return b"ldc-env-v1\0" + canonical({k: v for k, v in env.items() if k != "sig"})


def seal(me: Identity, recipient: PublicIdentity, inner: dict, kind: str = "m",
         card: dict | None = None, mid: str | None = None, ts: int | None = None) -> dict:
    if kind not in ("m", "r"):
        raise ValueError("type d'enveloppe inconnu")
    if kind == "r" and card is None:
        raise ValueError("une requête doit contenir la carte de l'expéditeur")
    nonce = os.urandom(24)
    plaintext = _pad(json.dumps(inner, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    ct = Box(me.box_sk, PublicKey(recipient.box_pk)).encrypt(plaintext, nonce).ciphertext
    env = {"v": ENV_VERSION, "id": mid or new_mid(), "from": me.id, "to": recipient.id,
           "ts": ts or now_ms(), "k": kind, "n": b64(nonce), "ct": b64(ct)}
    if card is not None:
        env["card"] = card
    env["sig"] = b64(me.sign(_signed_part(env)))
    return env


def validate_envelope(env: object) -> dict:
    """Validation stricte du schéma, avant toute opération cryptographique."""
    if not isinstance(env, dict) or not set(env) <= _ENV_KEYS:
        raise EnvelopeError("enveloppe mal formée")
    try:
        if env["v"] != ENV_VERSION or env["k"] not in ("m", "r"):
            raise EnvelopeError("version ou type non supporté")
        for f in ("id", "from", "to", "n", "ct", "sig"):
            if not isinstance(env[f], str):
                raise EnvelopeError("champ invalide")
        if not (isinstance(env["id"], str) and 8 <= len(env["id"]) <= 64 and env["id"].isalnum()):
            raise EnvelopeError("identifiant de message invalide")
        if not (ID_RE.match(env["from"]) and ID_RE.match(env["to"])):
            raise EnvelopeError("identifiant LDC invalide")
        if not isinstance(env["ts"], int) or isinstance(env["ts"], bool) or env["ts"] < 0:
            raise EnvelopeError("horodatage invalide")
        if len(env["ct"]) > MAX_CT_B64 or len(env["n"]) != 32 or len(env["sig"]) != 88:
            raise EnvelopeError("tailles invalides")
        if env["k"] == "r" and not isinstance(env.get("card"), dict):
            raise EnvelopeError("requête sans carte")
    except KeyError as e:
        raise EnvelopeError("champ manquant") from e
    return env


def open_envelope(env: dict, me: Identity, sender: PublicIdentity) -> dict:
    """Vérifie la signature avec la clé *épinglée* de l'expéditeur puis déchiffre."""
    validate_envelope(env)
    if env["to"] != me.id:
        raise EnvelopeError("enveloppe destinée à une autre identité")
    if env["from"] != sender.id:
        raise EnvelopeError("l'expéditeur ne correspond pas à la clé connue")
    try:
        if not sender.verify(unb64(env["sig"]), _signed_part(env)):
            raise EnvelopeError("signature invalide")
        plaintext = Box(me.box_sk, PublicKey(sender.box_pk)).decrypt(unb64(env["ct"]), unb64(env["n"]))
        inner = json.loads(_unpad(plaintext).decode("utf-8"))
    except (CryptoError, ValueError, UnicodeDecodeError) as e:
        raise EnvelopeError("déchiffrement impossible") from e
    if not isinstance(inner, dict) or not isinstance(inner.get("t"), str):
        raise EnvelopeError("contenu invalide")
    return inner


# --- Cartes de contact ---------------------------------------------------------
def make_card(me: Identity, hint: str | None = None) -> dict:
    pub = me.public
    card = {"v": 1, "id": pub.id, "name": me.name, "sk": b64(pub.sign_pk), "bk": b64(pub.box_pk)}
    if hint:
        card["rv"] = hint[:200]
    card["sig"] = b64(me.sign(b"ldc-card-v1\0" + canonical(card)))
    return card


def parse_card(card: object) -> tuple[PublicIdentity, str | None]:
    """Valide une carte : l'ID doit être le hash des clés ET la carte doit être auto-signée."""
    if not isinstance(card, dict) or card.get("v") != 1:
        raise CardError("Carte de contact invalide")
    try:
        sk, bk = unb64(card["sk"]), unb64(card["bk"])
        if len(sk) != 32 or len(bk) != 32:
            raise CardError("Clés de taille invalide")
        pub = PublicIdentity(sk, bk, clean_name(str(card.get("name", ""))))
        if pub.id != card.get("id"):
            raise CardError("L'identifiant ne correspond pas aux clés (carte falsifiée)")
        body = {k: v for k, v in card.items() if k != "sig"}
        if not pub.verify(unb64(card["sig"]), b"ldc-card-v1\0" + canonical(body)):
            raise CardError("Signature de la carte invalide")
    except (KeyError, ValueError, TypeError) as e:
        raise CardError("Carte de contact illisible") from e
    rv = card.get("rv")
    return pub, (rv if isinstance(rv, str) else None)


def encode_card(card: dict) -> str:
    return "ldc1:" + base64.urlsafe_b64encode(canonical(card)).decode("ascii").rstrip("=")


def decode_card(text: str) -> dict:
    t = "".join((text or "").split())
    for prefix in ("ldc://add/", "ldc://add?c="):
        if t.startswith(prefix):
            t = t[len(prefix):]
    if not t.startswith("ldc1:"):
        raise CardError("Ce texte n'est pas un code d'invitation LDC")
    try:
        raw = base64.urlsafe_b64decode(t[5:] + "=" * (-len(t[5:]) % 4))
        card = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise CardError("Code d'invitation illisible") from e
    if not isinstance(card, dict):
        raise CardError("Code d'invitation illisible")
    return card
