"""Outil de publication (à exécuter sur la machine de build, JAMAIS dans l'application).

  python tools/release_sign.py keygen release.key            # crée la clé de publication (garder secrète)
  python tools/release_sign.py sign release.key setup.exe 2.0.0 https://exemple.fr/ldc/setup.exe "Notes"
      -> écrit manifest.json et manifest.sig à publier à côté de l'installateur
La clé publique affichée par `keygen` doit être placée dans src/ldc_p2p/release_key.py avant de compiler.
"""
import base64
import hashlib
import json
import sys
from pathlib import Path

from nacl.signing import SigningKey


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "keygen":
        sk = SigningKey.generate()
        Path(sys.argv[2]).write_bytes(base64.b64encode(bytes(sk)))
        print("Clé publique (hex) :", bytes(sk.verify_key).hex())
    elif cmd == "sign":
        key, installer, version, url = sys.argv[2:6]
        notes = sys.argv[6] if len(sys.argv) > 6 else ""
        sk = SigningKey(base64.b64decode(Path(key).read_bytes()))
        manifest = json.dumps({"version": version, "url": url, "notes": notes,
                               "sha256": hashlib.sha256(Path(installer).read_bytes()).hexdigest()}, indent=1).encode()
        Path("manifest.json").write_bytes(manifest)
        Path("manifest.sig").write_text(base64.b64encode(sk.sign(manifest).signature).decode())
        print("manifest.json et manifest.sig écrits.")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
