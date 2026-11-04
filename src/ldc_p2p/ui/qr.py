"""Code QR du code d'invitation (bibliothèque segno, 100 % Python, sans dépendance)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap


def qr_pixmap(text: str, scale: int = 5, border: int = 3) -> QPixmap | None:
    try:
        import segno
    except ImportError:
        return None
    qr = segno.make(text, error="l", micro=False)
    rows = [list(r) for r in qr.matrix]
    n = len(rows)
    size = (n + 2 * border) * scale
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#111111"))
    for y, row in enumerate(rows):
        for x, v in enumerate(row):
            if v:
                p.drawRect((x + border) * scale, (y + border) * scale, scale, scale)
    p.end()
    return QPixmap.fromImage(img)
