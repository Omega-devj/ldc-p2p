"""Découverte sur réseau local SANS serveur : annonces UDP en diffusion, avec étiquette rotative.

Chaque annonce contient seulement un port et une étiquette = SHA-256(« ldc-lan » | ID | jour)[:8]. Un contact qui
connaît votre ID recalcule l'étiquette et se connecte directement (la poignée de main authentifie ensuite
mutuellement les deux côtés). Un curieux du réseau ne peut ni retrouver votre ID ni vous suivre d'un jour à l'autre.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import time
from typing import Callable

log = logging.getLogger("ldc.lan")
LAN_PORT = 47891


def lan_tag(ldc_id: str, day: int | None = None) -> str:
    day = int(time.time() // 86400) if day is None else day
    return hashlib.sha256(f"ldc-lan|{ldc_id}|{day}".encode()).hexdigest()[:16]


class LanDiscovery(asyncio.DatagramProtocol):
    def __init__(self, my_id: str, listen_port: int, contact_ids: Callable[[], list[str]],
                 on_peer: Callable[[str, str, int], None], port: int = LAN_PORT, targets: list[str] | None = None,
                 interval: float = 6.0):
        self.my_id, self.listen_port, self.contact_ids, self.on_peer = my_id, listen_port, contact_ids, on_peer
        self.port, self.interval = port, interval
        self.targets = targets or ["255.255.255.255"]
        self.transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", self.port))
        sock.setblocking(False)
        self.transport, _ = await loop.create_datagram_endpoint(lambda: self, sock=sock)
        self._task = asyncio.create_task(self._announce_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.transport:
            self.transport.close()

    async def _announce_loop(self) -> None:
        while True:
            msg = json.dumps({"ldc": 1, "tag": lan_tag(self.my_id), "port": self.listen_port}).encode()
            for t in self.targets:
                try:
                    self.transport.sendto(msg, (t, self.port))
                except OSError:
                    pass
            await asyncio.sleep(self.interval)

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            d = json.loads(data[:512])
            tag, port = d["tag"], int(d["port"])
            if d.get("ldc") != 1 or not (0 < port < 65536) or not isinstance(tag, str):
                return
        except (ValueError, KeyError, TypeError):
            return
        if tag == lan_tag(self.my_id):
            return  # notre propre annonce
        day = int(time.time() // 86400)
        for cid in self.contact_ids():
            if tag in (lan_tag(cid, day), lan_tag(cid, day - 1)):
                self.on_peer(cid, addr[0], port)
                return
