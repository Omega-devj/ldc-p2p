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


GITHUB_UPDATE_URL = "https://github.com/Omega-devj/ldc-p2p/releases/latest/download"
ALLOWED_TOP = ("src/", "requirements.txt", "LANCER_LDC_P2P.bat", "LANCER_SERVEUR.bat", "README.md", "docs/", "deploy/", "tools/", "packaging/")


@dataclass(frozen=True)
class Update:
    version: str
    url: str
    sha256: str
    notes: str
    source_url: str = ""
    source_sha256: str = ""


def current_version() -> str:
    """Version réellement installée : celle gravée par le build/la mise à jour, sinon celle du paquet."""
    try:
        from . import _build
        if _build.BUILD:
            return _build.BUILD
    except ImportError:
        pass
    from . import __version__
    return __version__


def install_root() -> Path | None:
    """Racine d'une installation « depuis les sources » (None pour un exécutable PyInstaller)."""
    import sys
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parents[2]
    return root if (root / "src" / "ldc_p2p").is_dir() else None


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
        version, notes = str(doc["version"]), str(doc.get("notes", ""))[:2000]
        url, sha = str(doc.get("url", "")), str(doc.get("sha256", "")).lower()
        surl, ssha = str(doc.get("source_url", "")), str(doc.get("source_sha256", "")).lower()
    except KeyError as e:
        raise UpdateError("Manifeste incomplet") from e
    for u, h in ((url, sha), (surl, ssha)):
        if u and (not u.lower().startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", h)):
            raise UpdateError("Manifeste invalide")
    if not (url or surl):
        raise UpdateError("Manifeste sans fichier")
    return Update(version, url, sha, notes, surl, ssha) if is_newer(version, current) else None


def download_update(update: Update, dest_dir: Path, fetch: Callable[[str, int], bytes] | None = None, source: bool = False) -> Path:
    fetch = fetch or (lambda u, lim: _https_get(u, lim, timeout=120))
    url, want = (update.source_url, update.source_sha256) if source else (update.url, update.sha256)
    if not url:
        raise UpdateError("Cette version ne propose pas ce type de fichier")
    data = fetch(url, MAX_INSTALLER)
    if hashlib.sha256(data).hexdigest() != want:
        raise UpdateError("Empreinte SHA-256 incorrecte : fichier rejeté")
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1]) or "ldc-p2p-update"
    out = dest_dir / name
    out.write_bytes(data)
    return out


def apply_source_update(zip_path: Path, root: Path) -> Path:
    """Remplace src/ (et les fichiers autorisés) par ceux de l'archive vérifiée ; sauvegarde l'ancienne version.
    Aucun chemin sortant de la racine, aucun fichier hors liste blanche. Retourne le dossier de sauvegarde."""
    import shutil
    import tempfile
    import time
    import zipfile
    tmp = Path(tempfile.mkdtemp(prefix="ldc-update-"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            top = {n.split("/", 1)[0] for n in names if n.strip("/")}
            prefix = (top.pop() + "/") if len(top) == 1 and not any(n.startswith(("src/", "requirements.txt")) for n in names) else ""
            total = 0
            for info in z.infolist():
                rel = info.filename[len(prefix):] if info.filename.startswith(prefix) else info.filename
                if not rel or rel.endswith("/"):
                    continue
                target = (tmp / rel).resolve()
                if not str(target).startswith(str(tmp.resolve())) or ".." in Path(rel).parts or Path(rel).is_absolute():
                    raise UpdateError("Archive invalide (chemin dangereux)")
                if not rel.startswith(ALLOWED_TOP) or rel.lower().endswith((".db", ".pem", ".ldckey")):
                    continue
                total += info.file_size
                if total > 200 * 1024 * 1024:
                    raise UpdateError("Archive trop volumineuse")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(info))
        if not (tmp / "src" / "ldc_p2p" / "__init__.py").exists():
            raise UpdateError("Archive incomplète : src/ldc_p2p manquant")
        backup = root / f".update-backup-{int(time.time())}"
        backup.mkdir()
        moved: list[str] = []
        try:
            for entry in tmp.iterdir():
                dest = root / entry.name
                if dest.exists():
                    shutil.move(str(dest), str(backup / entry.name))
                    moved.append(entry.name)
                shutil.move(str(entry), str(dest))
        except Exception:
            for name in moved:  # restauration en cas d'échec
                if (root / name).exists():
                    shutil.rmtree(root / name, ignore_errors=True) if (root / name).is_dir() else (root / name).unlink()
                shutil.move(str(backup / name), str(root / name))
            raise
        old = sorted(root.glob(".update-backup-*"))[:-2]  # on garde les 2 dernières sauvegardes
        for b in old:
            shutil.rmtree(b, ignore_errors=True)
        return backup
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
