"""Exécuté par GitHub Actions : fabrique l'archive des sources, grave le numéro de build, signe le manifeste.

  python tools/make_release.py <version> <cle_privee_b64> <base_url_assets> [installateur.exe]
Produit dist/ : ldc-p2p-src-<version>.zip, manifest.json, manifest.sig
"""
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["src", "requirements.txt", "LANCER_LDC_P2P.bat", "LANCER_SERVEUR.bat", "README.md", "docs", "deploy", "tools", "packaging"]


def main() -> None:
    version, key_b64, base = sys.argv[1:4]
    installer = Path(sys.argv[4]) if len(sys.argv) > 4 and Path(sys.argv[4]).exists() else None
    (ROOT / "src" / "ldc_p2p" / "_build.py").write_text(f'BUILD = "{version}"\n')
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    zpath = dist / f"ldc-p2p-src-{version}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in INCLUDE:
            p = ROOT / name
            files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
            for f in files:
                rel = f.relative_to(ROOT).as_posix()
                if "__pycache__" in rel or rel.endswith((".pyc", ".db", ".pem", ".ldckey")):
                    continue
                z.write(f, rel)
    doc = {"version": version, "notes": "Mise à jour automatique (nouvelle modification du dépôt).",
           "source_url": f"{base}/{zpath.name}", "source_sha256": hashlib.sha256(zpath.read_bytes()).hexdigest()}
    if installer:
        doc["url"] = f"{base}/{installer.name}"
        doc["sha256"] = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = json.dumps(doc, indent=1).encode()
    (dist / "manifest.json").write_bytes(manifest)
    (dist / "manifest.sig").write_text(base64.b64encode(SigningKey(base64.b64decode(key_b64)).sign(manifest).signature).decode())
    print("Release", version, "prête dans", dist)


if __name__ == "__main__":
    main()
