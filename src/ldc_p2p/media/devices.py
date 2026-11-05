"""Entrées/sorties réelles : micro et haut-parleurs (sounddevice), caméra (OpenCV). Imports paresseux."""
from __future__ import annotations

import asyncio
from collections import deque

from .base import CallsUnavailable

RATE = 48000
FRAME = 960           # 20 ms à 48 kHz
VIDEO_SIZE = (640, 480)


def _sd():
    try:
        import sounddevice as sd
        return sd
    except Exception as e:  # ImportError ou PortAudio absent
        raise CallsUnavailable("Composant audio manquant (sounddevice/PortAudio)") from e


def _cv2():
    try:
        import cv2
        return cv2
    except Exception as e:
        raise CallsUnavailable("Composant vidéo manquant (OpenCV)") from e


def list_audio_devices() -> tuple[list[str], list[str]]:
    try:
        sd = _sd()
        devs = sd.query_devices()
    except Exception:
        return [], []
    ins = sorted({d["name"] for d in devs if d["max_input_channels"] > 0})
    outs = sorted({d["name"] for d in devs if d["max_output_channels"] > 0})
    return ins, outs


def _find(sd, name: str, kind: str):
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d[f"max_{kind}_channels"] > 0:
            return i
    return None


class MicSource:
    """Micro : blocs de 20 ms, cadencés par l'horloge de la carte son."""

    def __init__(self, device: str = ""):
        self.device = device
        self._q: asyncio.Queue | None = None
        self._stream = None

    async def start(self) -> None:
        import numpy as np
        sd = _sd()
        loop = asyncio.get_running_loop()
        self._q = asyncio.Queue(maxsize=50)

        def cb(indata, frames, t, status):
            data = indata.copy().reshape(-1).astype(np.int16)
            def put():
                if self._q.full():
                    self._q.get_nowait()
                self._q.put_nowait(data)
            loop.call_soon_threadsafe(put)
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                      device=_find(sd, self.device, "input"), callback=cb)
        self._stream.start()

    async def read(self):
        return await self._q.get()

    async def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class SpeakerSink:
    def __init__(self, device: str = ""):
        self.device = device
        self._buf: deque = deque()
        self._stream = None
        self.volume = 1.0

    async def start(self) -> None:
        import numpy as np
        sd = _sd()

        def cb(outdata, frames, t, status):
            need = frames
            out = np.zeros(frames, dtype=np.int16)
            pos = 0
            while need > 0 and self._buf:
                chunk = self._buf[0]
                take = min(need, len(chunk))
                out[pos:pos + take] = chunk[:take]
                if take == len(chunk):
                    self._buf.popleft()
                else:
                    self._buf[0] = chunk[take:]
                pos += take
                need -= take
            outdata[:, 0] = (out * self.volume).astype(np.int16)
        self._stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                       device=_find(sd, self.device, "output"), callback=cb)
        self._stream.start()

    def write(self, samples) -> None:
        if len(self._buf) > 25:      # > 500 ms de retard : on rattrape
            self._buf.clear()
        self._buf.append(samples)

    async def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class CvCamera:
    def __init__(self, camera: str = ""):
        self.index = int(camera) if camera.isdigit() else 0
        self.cap = None

    async def start(self) -> None:
        cv2 = _cv2()
        loop = asyncio.get_running_loop()

        def open_():
            cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") and __import__("sys").platform == "win32" else cv2.VideoCapture(self.index)
            if not cap.isOpened():
                raise CallsUnavailable("Caméra introuvable ou déjà utilisée")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, VIDEO_SIZE[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_SIZE[1])
            return cap
        self.cap = await loop.run_in_executor(None, open_)

    async def read(self):
        cv2 = _cv2()
        loop = asyncio.get_running_loop()

        def grab():
            ok, frame = self.cap.read()
            if not ok:
                return None
            frame = cv2.resize(frame, VIDEO_SIZE)
            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return await loop.run_in_executor(None, grab)

    async def stop(self) -> None:
        if self.cap is not None:
            cap, self.cap = self.cap, None
            await asyncio.get_running_loop().run_in_executor(None, cap.release)


def list_cameras(max_index: int = 4) -> list[str]:
    try:
        cv2 = _cv2()
    except CallsUnavailable:
        return []
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            found.append(str(i))
        cap.release()
    return found
