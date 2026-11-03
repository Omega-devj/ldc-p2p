"""Cadrage des paquets : 4 octets de longueur (big-endian) + JSON UTF-8.

TCP est un flux d'octets : on lit toujours exactement `n` octets (`readexactly`),
on refuse les paquets au-delà de MAX_FRAME avant d'allouer quoi que ce soit.
"""
from __future__ import annotations

import asyncio
import json
import struct

MAX_FRAME = 3 * 1024 * 1024
PROTOCOL_VERSION = 1


class FrameError(Exception):
    pass


def encode_frame(obj: dict, max_size: int = MAX_FRAME) -> bytes:
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > max_size:
        raise FrameError("paquet trop volumineux")
    return struct.pack(">I", len(data)) + data


async def read_frame(reader: asyncio.StreamReader, max_size: int = MAX_FRAME) -> dict:
    try:
        header = await reader.readexactly(4)
        (n,) = struct.unpack(">I", header)
        if n == 0 or n > max_size:
            raise FrameError("taille de paquet invalide")
        data = await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise ConnectionError("connexion fermée") from e
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FrameError("JSON invalide") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("t"), str):
        raise FrameError("paquet sans type")
    return obj


async def write_frame(writer: asyncio.StreamWriter, obj: dict, max_size: int = MAX_FRAME) -> None:
    writer.write(encode_frame(obj, max_size))
    await writer.drain()
