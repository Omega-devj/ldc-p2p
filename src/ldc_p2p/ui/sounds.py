"""Sons de notification générés par le programme (aucun fichier audio embarqué, aucun droit d'auteur)."""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import wave
from pathlib import Path

SOUNDS = {
    "doux": [(660, 0.09), (880, 0.14)],
    "cristal": [(1175, 0.06), (1568, 0.06), (2093, 0.12)],
    "classique": [(880, 0.12), (0, 0.04), (880, 0.12)],
}
LABELS = {"doux": "Doux", "cristal": "Cristal", "classique": "Classique"}
_dir: Path | None = None


def _wav(name: str) -> Path:
    global _dir
    if _dir is None:
        _dir = Path(tempfile.mkdtemp(prefix="ldc-snd-"))
    path = _dir / f"{name}.wav"
    if path.exists():
        return path
    rate = 22050
    frames = bytearray()
    for freq, dur in SOUNDS.get(name, SOUNDS["doux"]):
        n = int(rate * dur)
        for i in range(n):
            env = min(1.0, i / (rate * 0.01), (n - i) / (rate * 0.03))  # attaque/relâchement doux, pas de claquement
            v = 0.0 if freq == 0 else math.sin(2 * math.pi * freq * i / rate) * 0.35 * env
            frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def play(name: str = "doux") -> None:
    try:
        path = _wav(name if name in SOUNDS else "doux")
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            from PySide6.QtWidgets import QApplication
            QApplication.beep()
    except Exception:
        pass
