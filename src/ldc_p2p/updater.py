"""Mises à jour sûres : manifeste signé (Ed25519) + empreinte SHA-256 du programme d'installation.

Principe : l'application n'exécute JAMAIS un fichier téléchargé sans (1) manifeste authentique signé par la
clé de publication, (2) empreinte identique, (3) accord explicite de l'utilisateur. HTTPS obligatoire.
Aucune URL par défaut : tant qu'aucune source n'est configurée, la fonction est désactivée.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .crypto.identity import unb64

MAX_MANIFEST = 64 * 1024
MAX_INSTALLER = 400 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Update:
    version: str
    url: str
    sha256: str
    notes: str


def parse_version(v: str) -> tuple[int, ...]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|b|rc)(\d+))?", v.strip())
    if not m:
        raise UpdateError(f"Version illisible : {v!r}")
    major, minor, patch, pre, n = m.groups()
    rank = {"a": 0, "b": 1, "rc": 2, None: 3}[pre]
    return (int(major), int(minor), int(patch), rank, int(n or 0))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def verify_manifest(manifest_bytes: bytes, signature_b64: str, public_key: bytes) -> dict:
    if len(manifest_bytes) > MAX_MANIFEST:
        raise UpdateError("Manifeste trop volumineux")
    try:
        VerifyKey(public_key).verify(manifest_bytes, unb64(signature_b64.strip()))
    except (BadSignatureError, ValueError) as e:
        raise UpdateError("Signature du manifeste invalide : mise à jour ignorée") from e
    try:
        doc = json.loads(manifest_bytes)
    except json.JSONDecodeError as e:
        raise UpdateError("Manifeste illisible") from e
    if not isinstance(doc, dict):
        raise UpdateError("Manifeste illisible")
    return doc


def _https_get(url: str, limit: int, timeout: float = 15.0) -> bytes:
    if not url.lower().startswith("https://"):
        raise UpdateError("Les mises à jour doivent être servies en HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": "LDC-P2P-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (schéma vérifié ci-dessus)
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Réponse trop volumineuse")
    return data


def check_for_update(base_url: str, current: str, public_key: bytes, fetch: Callable[[str, int], bytes] | None = None) -> Update | None:
    fetch = fetch or (lambda u, lim: _https_get(u, lim))
    base = base_url.rstrip("/")
    manifest = fetch(f"{base}/manifest.json", MAX_MANIFEST)
    sig = fetch(f"{base}/manifest.sig", 1024).decode("ascii", "ignore")
    doc = verify_manifest(manifest, sig, public_key)
    try:
        version, url, sha, notes = str(doc["version"]), str(doc["url"]), str(doc["sha256"]).lower(), str(doc.get("notes", ""))[:2000]
    except KeyError as e:
        raise UpdateError("Manifeste incomplet") from e
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or not url.lower().startswith("https://"):
        raise UpdateError("Manifeste invalide")
    return Update(version, url, sha, notes) if is_newer(version, current) else None


def download_update(update: Update, dest_dir: Path, fetch: Callable[[str, int], bytes] | None = None) -> Path:
    fetch = fetch or (lambda u, lim: _https_get(u, lim, timeout=120))
    data = fetch(update.url, MAX_INSTALLER)
    if hashlib.sha256(data).hexdigest() != update.sha256:
        raise UpdateError("Empreinte SHA-256 incorrecte : fichier rejeté")
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", update.url.rsplit("/", 1)[-1]) or "ldc-p2p-setup.exe"
    out = dest_dir / name
    out.write_bytes(data)
    return out
