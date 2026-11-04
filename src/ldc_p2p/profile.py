"""Profils locaux : création, déverrouillage, restauration, sauvegarde, suppression ciblée."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .crypto.cipher import FieldCipher
from .crypto.identity import (SCRYPT_N, Identity, InvalidPassword, Keystore, atomic_write, b64, open_blob,
                              seal_blob, unb64)
from .storage.database import Database


@dataclass
class Profile:
    identity: Identity
    db: Database
    directory: Path


def write_hint(directory: Path, name: str) -> None:
    """Pseudo affiché sur l'écran de déverrouillage (seule info non chiffrée du profil, volontairement minimale)."""
    atomic_write(directory / "meta.json", json.dumps({"name": name}).encode())


def profile_label(ldc_id: str) -> str:
    try:
        return json.loads((paths.profile_dir(ldc_id) / "meta.json").read_text("utf-8")).get("name") or "Profil"
    except (OSError, ValueError):
        return "Profil"


def _open(identity: Identity, directory: Path) -> Profile:
    return Profile(identity, Database(directory / "data.db", FieldCipher(identity.db_key)), directory)


def create_profile(name: str, password: str, identity: Identity | None = None, scrypt_n: int = SCRYPT_N) -> Profile:
    ident = identity or Identity.generate(name)
    d = paths.profile_dir(ident.id)
    if (d / "keystore.json").exists():
        raise ValueError("Ce profil existe déjà sur cet ordinateur. Déverrouillez-le.")
    Keystore.create(d / "keystore.json", ident, password, scrypt_n)
    write_hint(d, ident.name)
    return _open(ident, d)


def unlock_profile(ldc_id: str, password: str) -> Profile:
    d = paths.profile_dir(ldc_id)
    ident = Keystore.unlock(d / "keystore.json", password)
    return _open(ident, d)


def restore_from_recovery_code(code: str, name: str, password: str, scrypt_n: int = SCRYPT_N) -> Profile:
    ident = Identity.from_recovery_code(code, name)
    return create_profile(name, password, ident, scrypt_n)


def delete_profile(ldc_id: str) -> None:
    """Supprime UNIQUEMENT le dossier du profil (chemin validé, jamais de glob)."""
    d = paths.profile_dir(ldc_id).resolve()
    root = paths.profiles_root().resolve()
    if d.parent != root:
        raise ValueError("Chemin de profil invalide")
    shutil.rmtree(d)


# -- sauvegarde chiffrée (contacts, groupes, réglages ; pas l'historique, pas la clé) -----------
def export_backup(db: Database, identity: Identity, path: Path, passphrase: str, scrypt_n: int = SCRYPT_N) -> None:
    if len(passphrase) < 8:
        raise ValueError("La phrase secrète doit contenir au moins 8 caractères")
    doc = {
        "id": identity.id,
        "contacts": [{"id": c["id"], "name": c["name"], "alias": c["alias"], "sk": b64(bytes(c["sign_pk"])),
                      "bk": b64(bytes(c["box_pk"])), "state": c["state"], "verified": c["verified"]}
                     for c in db.list_contacts() if c["state"] in ("accepted", "blocked", "group")],
        "groups": db.list_groups(),
        "settings": {k: v for k, v in db.all_settings().items() if k not in ("download_dir",)},
    }
    blob = seal_blob(passphrase, json.dumps(doc).encode(), b"ldc-backup-v1|" + identity.id.encode(), scrypt_n)
    atomic_write(path, json.dumps({"format": "ldc-backup", "version": 1, "id": identity.id, **blob}).encode())


def import_backup(db: Database, identity: Identity, path: Path, passphrase: str) -> dict[str, int]:
    try:
        wrapper = json.loads(Path(path).read_text("utf-8"))
        if wrapper.get("format") != "ldc-backup" or wrapper.get("id") != identity.id:
            raise InvalidPassword("Cette sauvegarde appartient à une autre identité")
        doc = json.loads(open_blob(passphrase, wrapper, b"ldc-backup-v1|" + identity.id.encode()))
    except (OSError, json.JSONDecodeError, KeyError) as e:
        raise InvalidPassword("Fichier de sauvegarde illisible") from e
    from .crypto.identity import PublicIdentity
    added = 0
    for c in doc.get("contacts", []):
        sk, bk = unb64(c["sk"]), unb64(c["bk"])
        if PublicIdentity(sk, bk).id != c["id"] or c["state"] not in ("accepted", "blocked", "group"):
            continue  # entrée incohérente ignorée
        if db.get_contact(c["id"]) is None:
            db.put_contact(c["id"], c["name"], sk, bk, c["state"])
            if c.get("alias"):
                db.update_contact(c["id"], alias=c["alias"])
            if c.get("verified"):
                db.update_contact(c["id"], verified=1)
            added += 1
    groups = 0
    for g in doc.get("groups", []):
        if db.get_group(g["gid"]) is None and identity.id in g["members"]:
            db.put_group(g["gid"], g["name"], g["owner"], g["members"], g["version"])
            groups += 1
    for k, v in doc.get("settings", {}).items():
        if db.get_setting(k) is None:
            db.set_setting(k, v)
    return {"contacts": added, "groups": groups}


def export_history(db: Database, identity: Identity, path: Path, passphrase: str, names: dict[str, str] | None = None,
                   scrypt_n: int = SCRYPT_N) -> int:
    """Exporte tout l'historique (texte + noms de fichiers) dans un fichier chiffré par phrase secrète."""
    if len(passphrase) < 8:
        raise ValueError("La phrase secrète doit contenir au moins 8 caractères")
    out = []
    for r in db.conn.execute("SELECT mid FROM messages WHERE kind IN ('text','file') ORDER BY ts, id").fetchall():
        m = db.get_message(r["mid"])
        out.append({"conv": m["conv"], "sender": m["sender"], "ts": m["ts"], "direction": m["direction"], "kind": m["kind"],
                    "text": m["body"], "file": (m["extra"] or {}).get("name") if isinstance(m["extra"], dict) and m["kind"] == "file" else None})
    blob = seal_blob(passphrase, json.dumps({"id": identity.id, "names": names or {}, "messages": out}).encode(),
                     b"ldc-history-v1|" + identity.id.encode(), scrypt_n)
    atomic_write(path, json.dumps({"format": "ldc-history", "version": 1, "id": identity.id, **blob}).encode())
    return len(out)
