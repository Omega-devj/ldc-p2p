import asyncio
import os

import pytest

from ldc_p2p.crypto.cipher import FieldCipher
from ldc_p2p.crypto.identity import Identity
from ldc_p2p.engine import Engine, EngineSettings
from ldc_p2p.server.relay_server import RelayServer
from ldc_p2p.storage.database import Database


async def wait_for(cond, timeout=8.0, step=0.02):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return
        await asyncio.sleep(step)
    raise AssertionError("condition non atteinte dans le délai")


class Node:
    """Un utilisateur complet : identité + base + moteur."""

    def __init__(self, tmp_path, name, port, direct=False, identity=None, db_name=None):
        self.name, self.tmp = name, tmp_path
        self.identity = identity or Identity.generate(name)
        self.db_path = tmp_path / (db_name or f"{name}.db")
        self.port, self.direct = port, direct
        self.events: list[tuple[str, dict]] = []
        self.engine: Engine | None = None

    def _build(self):
        db = Database(self.db_path, FieldCipher(self.identity.db_key))
        s = EngineSettings.load(db)
        s.server_host, s.server_port, s.direct_enabled = ("127.0.0.1" if self.port else ""), self.port or 9443, self.direct
        e = Engine(self.identity, db, s, download_dir=self.tmp / f"dl-{self.name}")
        e.allow_loopback_candidates = True
        e.events.on_any(lambda n, **d: self.events.append((n, d)))
        return e

    async def start(self):
        self.engine = self._build()
        await self.engine.start()
        return self

    async def stop(self):
        if self.engine:
            await self.engine.stop()
            self.engine.db.close()
            self.engine = None

    async def online(self):
        await wait_for(lambda: self.engine.relay and self.engine.relay.connected)
        return self

    def count(self, name):
        return sum(1 for n, _ in self.events if n == name)


@pytest.fixture
async def server():
    s = RelayServer("127.0.0.1", 0)
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def make_node(tmp_path):
    nodes: list[Node] = []

    async def make(name, port, direct=False, **kw):
        n = await Node(tmp_path, name, port, direct, **kw).start()
        nodes.append(n)
        return n

    yield make
    for n in nodes:
        await n.stop()


async def befriend(a: Node, b: Node):
    """a ajoute b, b accepte : les deux sont contacts acceptés."""
    await a.engine.add_contact_from_code(b.engine.my_card_code(), "salut")
    await wait_for(lambda: (c := b.engine.db.get_contact(a.identity.id)) and c["state"] == "pending_in")
    await b.engine.accept_request(a.identity.id)
    await wait_for(lambda: (c := a.engine.db.get_contact(b.identity.id)) and c["state"] == "accepted")
