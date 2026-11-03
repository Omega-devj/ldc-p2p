"""Génère packaging/ldc-p2p.ico et resources/logo.png depuis le logo dessiné par code."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication

from ldc_p2p.ui.icons import logo_pixmap


def main() -> None:
    app = QApplication([])
    from PIL import Image
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = []
    tmp = ROOT / "packaging" / ".icon-tmp"
    tmp.mkdir(exist_ok=True)
    for s in sizes:
        pm = logo_pixmap(s)
        pm.setDevicePixelRatio(1.0)
        pm = pm.scaled(s, s)
        f = tmp / f"{s}.png"
        pm.save(str(f))
        imgs.append(Image.open(f).convert("RGBA"))
    imgs[-1].save(ROOT / "packaging" / "ldc-p2p.ico", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    imgs[-1].save(ROOT / "src" / "ldc_p2p" / "resources" / "logo.png")
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    print("Icône générée :", ROOT / "packaging" / "ldc-p2p.ico")


if __name__ == "__main__":
    main()
