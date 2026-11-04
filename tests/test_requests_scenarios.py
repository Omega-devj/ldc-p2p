"""Scénarios réels de demandes de contact : accents, ajout croisé, destinataire hors ligne, serveur redémarré."""
import asyncio

from conftest import wait_for
from ldc_p2p.server.relay_server import RelayServer


async def test_accents_and_emoji_names(make_node, server):
    a, b = await make_node("Zoé-Ñandú 🔥", server.port), await make_node("Inès «B»", server.port)
    await a.online(), await b.online()
    await a.engine.add_contact_from_code(b.engine.my_card_code(), "Salut, c'est Zoé ! é à ü 🎉")
    await wait_for(lambda: b.engine.db.get_contact(a.identity.id) is not None, 5)
    assert b.engine.db.get_contact(a.identity.id)["name"] == "Zoé-Ñandú 🔥"


async def test_simultaneous_mutual_requests(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    await asyncio.gather(a.engine.add_contact_from_code(b.engine.my_card_code()), b.engine.add_contact_from_code(a.engine.my_card_code()))
    await wait_for(lambda: a.engine.db.get_contact(b.identity.id)["state"] == "accepted" and b.engine.db.get_contact(a.identity.id)["state"] == "accepted", 8)


async def test_request_to_offline_user_then_login(make_node, server):
    a = await make_node("alice", server.port)
    await a.online()
    from ldc_p2p.crypto.identity import Identity
    b = await make_node("bob", 0)                      # Bob existe mais n'est pas connecté
    code = b.engine.my_card_code()
    await a.engine.add_contact_from_code(code, "coucou")
    await asyncio.sleep(0.5)
    await b.stop()
    b2 = await make_node("bob", server.port, identity=b.identity, db_name="bob.db")
    await wait_for(lambda: b2.engine.db.get_contact(a.identity.id) is not None, 8)


async def test_request_when_sender_not_yet_connected(make_node, server):
    """L'expéditeur ajoute AVANT d'être connecté (serveur configuré après) : la demande doit partir à la connexion."""
    b = await make_node("bob", server.port)
    await b.online()
    a = await make_node("alice", 0)
    await a.engine.add_contact_from_code(b.engine.my_card_code(), "hello")
    await a.stop()
    a2 = await make_node("alice", server.port, identity=a.identity, db_name="alice.db")
    await wait_for(lambda: b.engine.db.get_contact(a.identity.id) is not None, 10)
    await a2.online()


async def test_request_survives_server_restart(make_node):
    srv = RelayServer("127.0.0.1", 0)
    await srv.start()
    port = srv.port
    a, b = await make_node("alice", port), await make_node("bob", port)
    await a.online(), await b.online()
    await srv.stop()
    await wait_for(lambda: not a.engine.relay.connected)
    await a.engine.add_contact_from_code(b.engine.my_card_code(), "pendant la panne")
    srv2 = RelayServer("127.0.0.1", port)
    await srv2.start()
    try:
        await wait_for(lambda: b.engine.db.get_contact(a.identity.id) is not None, 20)
    finally:
        await srv2.stop()
