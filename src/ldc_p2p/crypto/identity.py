"""Identité cryptographique LDC.

Une identité = une graine de 32 octets (code de récupération) dont on dérive
 - une clé de signature Ed25519,
 - une clé d'échange X25519 (chiffrement de bout en bout),
 - une clé de chiffrement de la base locale.
L'identifiant LDC est auto-certifiant : c'est un hash des deux clés publiques.
Il ne dépend ni du pseudo, ni de l'ordinateur, ni d'une adresse IP.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from nacl.exceptions import BadSignatureError
from nacl.public import PrivateKey
from nacl.signing import SigningKey, VerifyKey

ID_RE = re.compile(r"^LDC-[A-Z2-7]{6}(-[A-Z2-7]{6}){3}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


class InvalidPassword(Exception):
    pass


class InvalidRecoveryCode(Exception):
    pass


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def clean_name(name: str, default: str = "Anonyme") -> str:
    """Retire caractères de contrôle et marqueurs bidirectionnels (usurpation visuelle)."""
    name = _CTRL.sub("", name or "").strip()
    return name[:40] or default


def derive_ldc_id(sign_pk: bytes, box_pk: bytes) -> str:
    digest = hashlib.sha256(b"ldc-id-v1" + sign_pk + box_pk).digest()
    s = base64.b32encode(digest[:15]).decode("ascii")  # 24 caractères
    return "LDC-" + "-".join(s[i:i + 6] for i in range(0, 24, 6))


def safety_number(a: "PublicIdentity", b: "PublicIdentity") -> str:
    """Numéro de sécurité identique des deux côtés, à comparer de vive voix."""
    ka, kb = a.sign_pk + a.box_pk, b.sign_pk + b.box_pk
    lo, hi = sorted([ka, kb])
    d = hashlib.sha256(b"ldc-safety-v1" + lo + hi).digest()
    groups = [f"{int.from_bytes(d[i * 5:(i + 1) * 5], 'big') % 100000:05d}" for i in range(6)]
    return " ".join(groups)


@dataclass(frozen=True)
class PublicIdentity:
    sign_pk: bytes
    box_pk: bytes
    name: str = ""

    @property
    def id(self) -> str:
        return derive_ldc_id(self.sign_pk, self.box_pk)

    def verify(self, signature: bytes, data: bytes) -> bool:
        try:
            VerifyKey(self.sign_pk).verify(data, signature)
            return True
        except (BadSignatureError, ValueError):
            return False


def _hkdf(seed: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"ldc-p2p", info=info).derive(seed)


class Identity:
    def __init__(self, seed: bytes, name: str):
        if len(seed) != 32:
            raise ValueError("graine invalide")
        self.seed = seed
        self.name = clean_name(name)
        self._sign = SigningKey(_hkdf(seed, b"ldc-sign-v1"))
        self.box_sk = PrivateKey(_hkdf(seed, b"ldc-box-v1"))
        self.db_key = _hkdf(seed, b"ldc-db-v1")

    @classmethod
    def generate(cls, name: str) -> "Identity":
        return cls(os.urandom(32), name)

    @property
    def public(self) -> PublicIdentity:
        return PublicIdentity(bytes(self._sign.verify_key), bytes(self.box_sk.public_key), self.name)

    @property
    def id(self) -> str:
        return self.public.id

    def sign(self, data: bytes) -> bytes:
        return self._sign.sign(data).signature

    # --- Code de récupération -------------------------------------------------
    def recovery_code(self) -> str:
        raw = self.seed + hashlib.sha256(b"ldc-rc" + self.seed).digest()[:2]
        s = base64.b32encode(raw).decode("ascii").rstrip("=")
        return "-".join(s[i:i + 5] for i in range(0, len(s), 5))

    @classmethod
    def from_recovery_code(cls, code: str, name: str) -> "Identity":
        s = re.sub(r"[\s-]", "", code or "").upper()
        try:
            raw = base64.b32decode(s + "=" * (-len(s) % 8))
        except Exception as e:
            raise InvalidRecoveryCode("Code de récupération illisible") from e
        canonical_form = base64.b32encode(raw).decode("ascii").rstrip("=")
        if (len(raw) != 34 or canonical_form != s
                or hashlib.sha256(b"ldc-rc" + raw[:32]).digest()[:2] != raw[32:]):
            raise InvalidRecoveryCode("Code de récupération invalide (faute de frappe ?)")
        return cls(raw[:32], name)


# --- Coffre de clés chiffré par mot de passe -----------------------------------
SCRYPT_N = 2 ** 17


def _kdf_key(password: str, salt: bytes, n: int, r: int = 8, p: int = 1) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def seal_blob(password: str, payload: bytes, aad: bytes, n: int = SCRYPT_N) -> dict:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _kdf_key(password, salt, n)
    ct = ChaCha20Poly1305(key).encrypt(nonce, payload, aad)
    return {"kdf": {"name": "scrypt", "n": n, "r": 8, "p": 1, "salt": b64(salt)}, "nonce": b64(nonce), "ct": b64(ct)}


def open_blob(password: str, blob: dict, aad: bytes) -> bytes:
    try:
        kdf = blob["kdf"]
        if kdf["name"] != "scrypt" or not (2 ** 10 <= int(kdf["n"]) <= 2 ** 20):
            raise InvalidPassword("Paramètres de dérivation non supportés")
        key = _kdf_key(password, unb64(kdf["salt"]), int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
        return ChaCha20Poly1305(key).decrypt(unb64(blob["nonce"]), unb64(blob["ct"]), aad)
    except InvalidTag as e:
        raise InvalidPassword("Mot de passe incorrect") from e
    except (KeyError, ValueError, TypeError) as e:
        raise InvalidPassword("Fichier illisible ou corrompu") from e


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Keystore:
    """Fichier keystore.json : seed + pseudo chiffrés par mot de passe (scrypt + ChaCha20-Poly1305)."""

    @staticmethod
    def create(path: Path, identity: Identity, password: str, n: int = SCRYPT_N) -> None:
        if len(password) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
        payload = json.dumps({"seed": b64(identity.seed), "name": identity.name}).encode()
        blob = seal_blob(password, payload, b"ldc-keystore-v1|" + identity.id.encode(), n)
        doc = {"format": "ldc-keystore", "version": 1, "id": identity.id, **blob}
        atomic_write(Path(path), json.dumps(doc, indent=1).encode())

    @staticmethod
    def unlock(path: Path, password: str) -> Identity:
        try:
            doc = json.loads(Path(path).read_text("utf-8"))
            if doc.get("format") != "ldc-keystore" or doc.get("version") != 1:
                raise InvalidPassword("Format de coffre inconnu")
            raw = open_blob(password, doc, b"ldc-keystore-v1|" + doc["id"].encode())
            p = json.loads(raw)
            ident = Identity(unb64(p["seed"]), p["name"])
        except (OSError, json.JSONDecodeError, KeyError) as e:
            raise InvalidPassword("Coffre de clés illisible") from e
        if ident.id != doc["id"]:
            raise InvalidPassword("Coffre de clés incohérent")
        return ident

    @staticmethod
    def change_password(path: Path, old: str, new: str, n: int = SCRYPT_N) -> None:
        ident = Keystore.unlock(path, old)
        Keystore.create(path, ident, new, n)
