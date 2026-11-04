import asyncio

import pytest

from conftest import befriend, wait_for
from ldc_p2p.crypto import e2e
from ldc_p2p.engine import EngineError
from ldc_p2p.server.relay_server import RelayServer
from ldc_p2p.storage.database import now_ms


def texts(node, conv):
    return [m for m in node.engine.db.list_messages(conv) if m["kind"] != "system"]


async def pair(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    await befriend(a, b)
    return a, b


# ----------------------------------------------------------------- contacts
async def test_contact_request_accept_flow(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    c = await a.engine.add_contact_from_code(b.engine.my_card_code(), "Coucou")
    assert c["state"] == "pending_out"
    await wait_for(lambda: b.count("contact_request") == 1)
    req = b.engine.db.get_contact(a.identity.id)
    assert req["state"] == "pending_in" and req["name"] == "alice"
    with pytest.raises(EngineError):  # pas de message avant acceptation
        await a.engine.send_text(b.identity.id, "trop tôt")
    await b.engine.accept_request(a.identity.id)
    await wait_for(lambda: a.engine.db.get_contact(b.identity.id)["state"] == "accepted")
    assert b.engine.db.get_contact(a.identity.id)["state"] == "accepted"
    assert a.engine.contact_safety_number(b.identity.id) == b.engine.contact_safety_number(a.identity.id)


async def test_mutual_add_auto_accepts(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    await a.engine.add_contact_from_code(b.engine.my_card_code())
    await b.engine.add_contact_from_code(a.engine.my_card_code())
    await wait_for(lambda: a.engine.db.get_contact(b.identity.id)["state"] == "accepted"
                   and b.engine.db.get_contact(a.identity.id)["state"] == "accepted")


async def test_own_card_and_garbage_rejected(make_node, server):
    a = await make_node("alice", server.port)
    with pytest.raises(EngineError):
        await a.engine.add_contact_from_code(a.engine.my_card_code())
    with pytest.raises(EngineError):
        await a.engine.add_contact_from_code("n'importe quoi")


async def test_forged_card_cannot_add_fake_contact(make_node, server):
    a, eve = await make_node("alice", server.port), await make_node("eve", server.port)
    from ldc_p2p.crypto.identity import Identity
    bob = Identity.generate("bob")
    card = e2e.make_card(eve.identity)
    card["id"] = bob.id  # Eve prétend être Bob
    with pytest.raises(EngineError):
        await a.engine.add_contact_from_code(e2e.encode_card(card))


async def test_decline_and_block_silence_sender(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    await a.engine.add_contact_from_code(b.engine.my_card_code())
    await wait_for(lambda: b.engine.db.get_contact(a.identity.id) is not None)
    await b.engine.block(a.identity.id)
    # Alice (qui se croit acceptée) force l'envoi : Bob ne doit rien enregistrer
    a.engine.db.update_contact(b.identity.id, state="accepted")
    await a.engine.send_text(b.identity.id, "tu m'entends ?")
    await asyncio.sleep(0.6)
    assert texts(b, a.identity.id) == []
    assert b.count("message") == 0


# ----------------------------------------------------------------- messages
async def test_message_lifecycle_pending_sent_delivered_read(make_node, server):
    a, b = await pair(make_node, server)
    mid = await a.engine.send_text(b.identity.id, "Salut Bob 👋")
    assert a.engine.db.get_message(mid)["body"] == "Salut Bob 👋"  # enregistré localement d'abord
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "delivered")
    m = b.engine.db.get_message(mid)
    assert m["body"] == "Salut Bob 👋" and m["direction"] == "in" and m["conv"] == a.identity.id
    assert b.engine.db.unread_counts() == {a.identity.id: 1}
    await b.engine.mark_read(a.identity.id)
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "read")
    assert b.engine.db.unread_counts() == {}


async def test_read_receipts_can_be_disabled(make_node, server):
    a, b = await pair(make_node, server)
    b.engine.settings.read_receipts = False
    mid = await a.engine.send_text(b.identity.id, "x")
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "delivered")
    await b.engine.mark_read(a.identity.id)
    await asyncio.sleep(0.4)
    assert a.engine.db.get_message(mid)["status"] == "delivered"


async def test_offline_recipient_gets_message_later(make_node, server):
    a, b = await pair(make_node, server)
    await b.stop()
    mid = await a.engine.send_text(b.identity.id, "pour plus tard")
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "sent")  # le relais l'a gardé, pas « delivered »
    await asyncio.sleep(0.3)
    assert a.engine.db.get_message(mid)["status"] == "sent"
    b2 = await make_node("bob", server.port, identity=b.identity, db_name="bob.db")
    await wait_for(lambda: b2.engine.db.get_message(mid) is not None)
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "delivered")


async def test_server_down_message_kept_and_sent_after_reconnect(make_node, tmp_path):
    srv = RelayServer("127.0.0.1", 0)
    await srv.start()
    port = srv.port
    a, b = await make_node("alice", port), await make_node("bob", port)
    await a.online(), await b.online()
    await befriend(a, b)
    await srv.stop()
    await wait_for(lambda: not a.engine.relay.connected)
    mid = await a.engine.send_text(b.identity.id, "pendant la panne")
    await asyncio.sleep(0.5)
    assert a.engine.db.get_message(mid)["status"] == "pending"  # honnête : pas « envoyé »
    srv2 = RelayServer("127.0.0.1", port)
    await srv2.start()
    try:
        await wait_for(lambda: a.engine.db.get_message(mid)["status"] in ("delivered", "sent"), timeout=15)
        await wait_for(lambda: b.engine.db.get_message(mid) is not None, timeout=15)
    finally:
        await srv2.stop()


async def test_outbox_survives_restart(make_node, tmp_path):
    srv = RelayServer("127.0.0.1", 0)
    await srv.start()
    port = srv.port
    a, b = await make_node("alice", port), await make_node("bob", port)
    await a.online(), await b.online()
    await befriend(a, b)
    await a.stop()
    # Alice relance sans connexion au serveur, écrit, puis ferme l'application
    a_off = await make_node("alice", 0, identity=a.identity, db_name="alice.db")
    mid = await a_off.engine.send_text(b.identity.id, "écrit hors ligne")
    await a_off.stop()
    # Nouvelle session avec le serveur : le message doit partir tout seul
    a2 = await make_node("alice", port, identity=a.identity, db_name="alice.db")
    try:
        await wait_for(lambda: b.engine.db.get_message(mid) is not None, timeout=15)
        await wait_for(lambda: a2.engine.db.get_message(mid)["status"] == "delivered", timeout=15)
    finally:
        await srv.stop()


async def test_duplicate_envelope_is_stored_once_and_reacked(make_node, server):
    a, b = await pair(make_node, server)
    pub_b = b.identity.public
    env = e2e.seal(a.identity, pub_b, {"t": "msg", "k": "text", "text": "une seule fois", "ts": now_ms()})
    for _ in range(3):
        assert await b.engine._handle_envelope(env, "relay") is True
    assert len(texts(b, a.identity.id)) == 1
    assert b.count("message") == 1


async def test_impersonation_and_garbage_do_not_create_messages(make_node, server):
    a, b = await pair(make_node, server)
    eve = await make_node("eve", server.port)
    env = e2e.seal(eve.identity, b.identity.public, {"t": "msg", "k": "text", "text": "je suis Alice", "ts": now_ms()})
    env["from"] = a.identity.id
    assert await b.engine._handle_envelope(env, "relay") is True  # confirmé pour purge…
    assert texts(b, a.identity.id) == []         # …mais jamais affiché
    for junk in ({}, {"v": 1}, "x", None):
        assert await b.engine._handle_envelope(junk, "relay") is True


async def test_text_limits(make_node, server):
    a, b = await pair(make_node, server)
    with pytest.raises(EngineError):
        await a.engine.send_text(b.identity.id, "   ")
    with pytest.raises(EngineError):
        await a.engine.send_text(b.identity.id, "x" * 9000)
    mid = await a.engine.send_text(b.identity.id, "<b>html</b> & 'quotes' \" \u202e évil")
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    assert b.engine.db.get_message(mid)["body"].startswith("<b>html</b>")  # stocké tel quel, échappé à l'affichage


async def test_failed_after_max_age_then_retry(make_node, tmp_path):
    a = await make_node("alice", 0)
    from ldc_p2p.crypto.identity import Identity
    bob = Identity.generate("bob")
    pub = bob.public
    a.engine.db.put_contact(pub.id, "bob", pub.sign_pk, pub.box_pk, "accepted")
    mid = await a.engine.send_text(pub.id, "perdu ?")
    a.engine.db.conn.execute("UPDATE outbox SET created_at=?", (now_ms() - 20 * 86400 * 1000,))
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "failed")
    assert a.count("message_status") >= 1
    await a.engine.retry_message(mid)
    assert a.engine.db.get_message(mid)["status"] == "pending"


async def test_delete_for_everyone(make_node, server):
    a, b = await pair(make_node, server)
    mid = await a.engine.send_text(b.identity.id, "oups")
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    await a.engine.delete_message(mid, for_everyone=True)
    await wait_for(lambda: b.engine.db.get_message(mid) is None)
    assert a.engine.db.get_message(mid) is None


async def test_cannot_delete_others_messages_remotely(make_node, server):
    a, b = await pair(make_node, server)
    mid = await b.engine.send_text(a.identity.id, "de Bob")
    await wait_for(lambda: a.engine.db.get_message(mid) is not None)
    env = e2e.seal(b.identity, a.identity.public, {"t": "del", "m": mid})
    await a.engine._handle_envelope(env, "relay")
    assert a.engine.db.get_message(mid) is None  # Bob est bien l'auteur : suppression légitime
    mid2 = await a.engine.send_text(b.identity.id, "d'Alice")
    await wait_for(lambda: b.engine.db.get_message(mid2) is not None)
    env2 = e2e.seal(a.identity, b.identity.public, {"t": "del", "m": mid2})
    # Bob reçoit « supprime ton message mid2 » d'Alice : OK (Alice est l'auteur)
    await b.engine._handle_envelope(env2, "relay")
    mid3 = await b.engine.send_text(a.identity.id, "message de Bob")
    await wait_for(lambda: a.engine.db.get_message(mid3) is not None)
    env3 = e2e.seal(a.identity, b.identity.public, {"t": "del", "m": mid3})  # Alice tente d'effacer un message de Bob chez Bob
    await b.engine._handle_envelope(env3, "relay")
    assert b.engine.db.get_message(mid3) is not None


# ----------------------------------------------------------------- éphémère, recherche
async def test_disappearing_messages_sync_and_expire(make_node, server):
    a, b = await pair(make_node, server)
    await a.engine.set_conv_ttl(b.identity.id, 60)
    mid = await a.engine.send_text(b.identity.id, "ce message va disparaître")
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    assert b.engine.db.conv_settings(a.identity.id)["ttl"] == 60  # le minuteur est partagé
    assert a.engine.db.get_message(mid)["expires_at"] is not None  # expéditeur : dès l'envoi
    assert b.engine.db.get_message(mid)["expires_at"] is None      # destinataire : dès la lecture
    await b.engine.mark_read(a.identity.id)
    assert b.engine.db.get_message(mid)["expires_at"] is not None
    for n in (a, b):  # on avance l'horloge : la maintenance globale supprime sans conversation ouverte
        n.engine.db.conn.execute("UPDATE messages SET expires_at=? WHERE mid=?", (now_ms() - 1, mid))
        n.engine.run_maintenance()
        assert n.engine.db.get_message(mid) is None


async def test_search(make_node, server):
    a, b = await pair(make_node, server)
    for t in ("Réunion jeudi", "courses", "jeudi prochain"):
        await a.engine.send_text(b.identity.id, t)
    await wait_for(lambda: len(texts(b, a.identity.id)) == 3)
    assert len(b.engine.search("JEUDI")) == 2
    assert len(a.engine.search("jeudi", conv=b.identity.id)) == 2


# ----------------------------------------------------------------- présence
async def test_presence_and_status(make_node, server):
    a, b = await make_node("alice", server.port), await make_node("bob", server.port)
    await a.online(), await b.online()
    await befriend(a, b)
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "online")
    await b.engine.set_status("dnd")
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "dnd")
    await b.engine.set_status("invisible")
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "offline")
    await b.engine.set_status("online")
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "online")
    await b.stop()
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "offline")


async def test_typing_indicator(make_node, server):
    a, b = await pair(make_node, server)
    await wait_for(lambda: a.engine.presence_of(b.identity.id) == "online")
    await a.engine.send_typing(b.identity.id)
    await wait_for(lambda: b.count("typing") == 1)


# ----------------------------------------------------------------- direct
async def test_direct_link_established_and_used_without_server(make_node, server):
    a, b = await make_node("alice", server.port, direct=True), await make_node("bob", server.port, direct=True)
    await a.online(), await b.online()
    await befriend(a, b)
    await wait_for(lambda: a.engine.is_direct(b.identity.id) and b.engine.is_direct(a.identity.id), timeout=10)
    await server.stop()  # plus de serveur : la conversation continue en direct
    mid = await a.engine.send_text(b.identity.id, "sans serveur !")
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    await wait_for(lambda: a.engine.db.get_message(mid)["status"] == "delivered")


# ----------------------------------------------------------------- groupes
async def trio(make_node, server):
    a, b, c = [await make_node(n, server.port) for n in ("alice", "bob", "carol")]
    for n in (a, b, c):
        await n.online()
    await befriend(a, b)
    await befriend(a, c)
    return a, b, c


async def test_group_create_message_and_membership(make_node, server):
    a, b, c = await trio(make_node, server)
    conv = await a.engine.create_group("Les copains", [b.identity.id, c.identity.id])
    gid = conv[4:]
    await wait_for(lambda: b.engine.db.get_group(gid) and c.engine.db.get_group(gid))
    # Bob et Carol ne sont PAS contacts : ils se connaissent seulement via le groupe
    assert b.engine.db.get_contact(c.identity.id)["state"] == "group"
    mid = await b.engine.send_text(conv, "Bonjour le groupe")
    await wait_for(lambda: a.engine.db.get_message(mid) is not None and c.engine.db.get_message(mid) is not None)
    assert c.engine.db.get_message(mid)["conv"] == conv and c.engine.db.get_message(mid)["sender"] == b.identity.id
    await wait_for(lambda: b.engine.db.get_message(mid)["status"] == "delivered")
    # un membre « group » ne peut pas écrire en privé
    with pytest.raises(EngineError):
        await b.engine.send_text(c.identity.id, "dm interdit")
    await a.engine.group_remove_member(conv, c.identity.id)
    await wait_for(lambda: not c.engine.db.get_group(gid)["active"])
    with pytest.raises(EngineError):
        await c.engine.send_text(conv, "je suis parti")


async def test_group_message_from_non_member_dropped(make_node, server):
    a, b, c = await trio(make_node, server)
    conv = await a.engine.create_group("G", [b.identity.id])
    await wait_for(lambda: b.engine.db.get_group(conv[4:]))
    inner = {"t": "msg", "k": "text", "text": "intrus", "g": conv[4:], "ts": now_ms()}
    env = e2e.seal(c.identity, b.identity.public, inner)  # Carol n'est pas membre (et pas contact de Bob)
    await b.engine._handle_envelope(env, "relay")
    assert b.engine.db.list_messages(conv) == [] or all(m["kind"] == "system" for m in b.engine.db.list_messages(conv))


async def test_group_invite_forgery_rejected(make_node, server):
    a, b, c = await trio(make_node, server)
    from ldc_p2p.crypto.identity import Identity, b64
    mallory = Identity.generate("mallory")
    members = [{"id": a.identity.id, "name": "a", "sk": b64(a.identity.public.sign_pk), "bk": b64(a.identity.public.box_pk)},
               {"id": b.identity.id, "name": "b", "sk": b64(mallory.public.sign_pk), "bk": b64(mallory.public.box_pk)}]  # clé de Mallory sous l'ID de Bob
    inner = {"t": "ginv", "g": "0123456789abcdef", "name": "Piège", "owner": a.identity.id, "v": 1, "m": members}
    await c.engine._handle_envelope(e2e.seal(a.identity, c.identity.public, inner), "relay")
    assert c.engine.db.get_group("0123456789abcdef") is None


async def test_group_unknown_yet_message_is_deferred_then_delivered(make_node, server):
    a, b, c = await trio(make_node, server)
    conv = await a.engine.create_group("G", [b.identity.id, c.identity.id])
    # Carol reçoit un message de groupe de Bob AVANT l'invitation : on le met de côté puis on le traite
    gid = conv[4:]
    await wait_for(lambda: c.engine.db.get_group(gid))
    c.engine.db.conn.execute("DELETE FROM groups WHERE gid=?", (gid,))
    c.engine.db.conn.execute("DELETE FROM contacts WHERE id=?", (b.identity.id,))
    c.engine.db.put_contact(b.identity.id, "bob", b.identity.public.sign_pk, b.identity.public.box_pk, "group")
    c.engine._pub_cache.clear()
    env = e2e.seal(b.identity, c.identity.public, {"t": "msg", "k": "text", "text": "en avance", "g": gid, "ts": now_ms()})
    assert await c.engine._handle_envelope(env, "relay") is False  # non confirmé, mis en attente
    await a.engine.group_rename(conv, "G2")  # nouvelle version : Carol reçoit l'invitation
    await wait_for(lambda: c.engine.db.get_message(env["id"]) is not None)


# ----------------------------------------------------------------- fichiers
async def test_file_transfer_accept_integrity(make_node, server, tmp_path):
    a, b = await pair(make_node, server)
    src = tmp_path / "données.bin"
    import os
    payload = os.urandom(300_000)
    src.write_bytes(payload)
    mid = await a.engine.send_file(b.identity.id, src)
    await wait_for(lambda: b.engine.transfer(mid) is not None)
    assert b.engine.transfer(mid)["state"] == "offered"
    await b.engine.accept_file(mid)
    await wait_for(lambda: b.engine.transfer(mid)["state"] == "done", timeout=20)
    out = b.engine.transfer(mid)["path"]
    assert open(out, "rb").read() == payload
    await wait_for(lambda: a.engine.transfer(mid)["state"] == "done")


async def test_file_decline_and_limits(make_node, server, tmp_path):
    a, b = await pair(make_node, server)
    empty = tmp_path / "vide.txt"
    empty.write_bytes(b"")
    with pytest.raises(EngineError):
        await a.engine.send_file(b.identity.id, empty)
    with pytest.raises(EngineError):
        await a.engine.send_file(b.identity.id, tmp_path / "inexistant")
    f = tmp_path / "a.txt"
    f.write_text("bonjour")
    mid = await a.engine.send_file(b.identity.id, f)
    await wait_for(lambda: b.engine.transfer(mid) is not None)
    await b.engine.decline_file(mid)
    await wait_for(lambda: a.engine.transfer(mid)["state"] == "declined")


async def test_malicious_file_offer_name_and_fields(make_node, server, tmp_path):
    a, b = await pair(make_node, server)
    from ldc_p2p.engine import safe_filename
    assert safe_filename("../../Windows/system32/evil.exe") == "evil.exe"
    assert safe_filename("CON") == "_CON"
    assert safe_filename("..") == "fichier"
    bad = {"fid": "../../x", "name": "a", "size": 10, "sha256": "zz", "n": 1, "chunk": 98304}
    env = e2e.seal(a.identity, b.identity.public, {"t": "msg", "k": "file", "text": "a", "x": bad, "ts": now_ms()})
    await b.engine._handle_envelope(env, "relay")
    assert b.engine.db.get_message(env["id"]) is None  # offre invalide rejetée


async def test_file_hash_mismatch_detected(make_node, server, tmp_path):
    a, b = await pair(make_node, server)
    f = tmp_path / "x.bin"
    f.write_bytes(b"A" * 200_000)
    mid = await a.engine.send_file(b.identity.id, f)
    await wait_for(lambda: b.engine.transfer(mid) is not None)
    # le destinataire croit recevoir un autre contenu : empreinte annoncée différente
    b.engine.db.conn.execute("UPDATE transfers SET sha256=? WHERE mid=?", ("0" * 64, mid))
    await b.engine.accept_file(mid)
    await wait_for(lambda: b.engine.transfer(mid)["state"] == "failed", timeout=20)
    assert not list((tmp_path / "dl-bob").glob("x*.bin"))  # aucun fichier corrompu exposé
    await wait_for(lambda: a.engine.transfer(mid)["state"] == "failed")


async def test_edit_message_only_by_author(make_node, server):
    a, b = await pair(make_node, server)
    mid = await a.engine.send_text(b.identity.id, "fote de frappe")
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    await a.engine.edit_message(mid, "faute de frappe corrigée")
    await wait_for(lambda: b.engine.db.get_message(mid)["body"] == "faute de frappe corrigée")
    assert b.engine.db.get_message(mid)["extra"]["edited"] > 0
    with pytest.raises(EngineError):
        await b.engine.edit_message(mid, "je réécris Alice")  # pas l'auteur
    forged = e2e.seal(b.identity, a.identity.public, {"t": "edit", "m": mid, "text": "piraté"})
    await a.engine._handle_envelope(forged, "relay")  # Bob prétend modifier un message d'Alice (chez Alice, sender=Alice)
    assert a.engine.db.get_message(mid)["body"] == "faute de frappe corrigée"


def test_direct_candidate_filter():
    from ldc_p2p.engine import Engine
    from ldc_p2p.crypto.identity import Identity
    from ldc_p2p.crypto.cipher import FieldCipher
    from ldc_p2p.storage.database import Database
    import os
    ident = Identity.generate("x")
    e = Engine(ident, Database(":memory:", FieldCipher(os.urandom(32))))
    ok = ["192.168.1.20", "10.0.0.5", "203.0.113.7", "2001:db8::1"]
    bad = ["127.0.0.1", "169.254.1.1", "224.0.0.1", "0.0.0.0", "localhost", "exemple.fr", "::1", "fe80::1"]
    assert all(e._candidate_allowed(h) for h in ok)
    assert not any(e._candidate_allowed(h) for h in bad)


async def test_reactions_sync_toggle_and_authorization(make_node, server):
    a, b, c = await trio(make_node, server)
    mid = await a.engine.send_text(b.identity.id, "réagis !")
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    await b.engine.react(mid, "👍")
    await wait_for(lambda: a.engine.db.reactions_for([mid]).get(mid) == {"👍": [b.identity.id]})
    await b.engine.react(mid, "❤️")  # remplace
    await wait_for(lambda: a.engine.db.reactions_for([mid]).get(mid) == {"❤️": [b.identity.id]})
    await b.engine.react(mid, "❤️")  # même emoji : retire
    await wait_for(lambda: a.engine.db.reactions_for([mid]).get(mid) is None)
    with pytest.raises(EngineError):
        await b.engine.react(mid, "💩")
    # Carol (étrangère à cette conversation) ne peut pas réagir
    forged = e2e.seal(c.identity, a.identity.public, {"t": "react", "m": mid, "e": "👍"})
    await a.engine._handle_envelope(forged, "relay")
    assert a.engine.db.reactions_for([mid]).get(mid) is None


async def test_mentions_and_group_reaction(make_node, server):
    a, b, c = await trio(make_node, server)
    conv = await a.engine.create_group("G", [b.identity.id, c.identity.id])
    await wait_for(lambda: b.engine.db.get_group(conv[4:]) and c.engine.db.get_group(conv[4:]))
    mid = await a.engine.send_text(conv, "salut @bob", mentions=[b.identity.id, "pas-un-id"])
    await wait_for(lambda: b.engine.db.get_message(mid) is not None)
    assert b.engine.db.get_message(mid)["extra"] == {"men": [b.identity.id]}
    await c.engine.react(mid, "🎉")
    await wait_for(lambda: b.engine.db.reactions_for([mid]).get(mid) == {"🎉": [c.identity.id]})


async def test_lookalike_warning(make_node, server):
    a, b = await pair(make_node, server)
    from ldc_p2p.crypto.identity import Identity
    fake = Identity.generate("bob")  # même pseudo que le vrai Bob, autre identifiant
    code = e2e.encode_card(e2e.make_card(fake))
    pub, look = a.engine.preview_card(code)
    assert pub.id == fake.id and [c["id"] for c in look] == [b.identity.id]


async def test_cancel_file_both_sides(make_node, server, tmp_path):
    import os
    a, b = await pair(make_node, server)
    f = tmp_path / "gros.bin"
    f.write_bytes(os.urandom(2_000_000))
    mid = await a.engine.send_file(b.identity.id, f)
    await wait_for(lambda: b.engine.transfer(mid) is not None)
    await b.engine.accept_file(mid)
    await wait_for(lambda: b.engine.transfer(mid)["next_idx"] > 0)
    await a.engine.cancel_file(mid)
    await wait_for(lambda: b.engine.transfer(mid)["state"] == "cancelled")
    assert a.engine.transfer(mid)["state"] == "cancelled"
    assert not list((tmp_path / "dl-bob").glob(".ldc-*.part"))


async def test_direct_toggle_applies_live(make_node, server):
    a = await make_node("alice", server.port, direct=False)
    assert a.engine.direct is None
    await a.engine.set_direct_enabled(True)
    assert a.engine.direct is not None and a.engine.direct.port > 0
    await a.engine.set_direct_enabled(False)
    assert a.engine.direct is None


def test_migration_from_v2_plaintext_names(tmp_path):
    import os, sqlite3
    from ldc_p2p.crypto.cipher import FieldCipher
    from ldc_p2p.storage import database as dbm
    key = os.urandom(32)
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    for stmt in filter(str.strip, dbm.MIGRATIONS[0].split(";")):
        c.execute(stmt)
    for stmt in filter(str.strip, dbm.MIGRATIONS[1].split(";")):
        c.execute(stmt)
    c.execute("PRAGMA user_version=2")
    c.execute("INSERT INTO contacts(id,name,alias,sign_pk,box_pk,state,added_at) VALUES('LDC-AAAAAA-BBBBBB-CCCCCC-DDDDDD','Camille','Cam',x'00',x'00','accepted',1)")
    c.execute("INSERT INTO groups(gid,name,owner,members,created_at) VALUES('0123456789abcdef','Famille','o','[\"a\",\"b\"]',1)")
    c.commit(); c.close()
    d = dbm.Database(path, FieldCipher(key))
    assert d.schema_version == len(dbm.MIGRATIONS)
    assert d.get_contact("LDC-AAAAAA-BBBBBB-CCCCCC-DDDDDD")["name"] == "Camille"
    assert d.get_group("0123456789abcdef")["members"] == ["a", "b"]
    d.close()
    raw = path.read_bytes() + (tmp_path / "old.db-wal").read_bytes() if (tmp_path / "old.db-wal").exists() else path.read_bytes()
    assert b"Camille" not in raw and b"Famille" not in raw
