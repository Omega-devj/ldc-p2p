import os
import sqlite3

import pytest

from ldc_p2p.crypto.cipher import FieldCipher
from ldc_p2p.storage.database import MIGRATIONS, Database, now_ms


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "data.db", FieldCipher(os.urandom(32)))
    yield d
    d.close()


def add(db, mid, conv="C1", direction="out", body="salut", ts=None, **kw):
    return db.add_message(mid=mid, conv=conv, sender="LDC-X", direction=direction, body=body,
                          ts=ts or now_ms(), status=kw.pop("status", "pending"), **kw)


def test_migrations_applied_and_idempotent(tmp_path):
    key = os.urandom(32)
    d = Database(tmp_path / "a.db", FieldCipher(key))
    assert d.schema_version == len(MIGRATIONS)
    d.close()
    d2 = Database(tmp_path / "a.db", FieldCipher(key))  # réouverture : pas de rejeu
    assert d2.schema_version == len(MIGRATIONS)
    d2.close()


def test_newer_database_refused(tmp_path):
    p = tmp_path / "n.db"
    c = sqlite3.connect(p)
    c.execute(f"PRAGMA user_version={len(MIGRATIONS) + 5}")
    c.close()
    with pytest.raises(RuntimeError):
        Database(p, FieldCipher(os.urandom(32)))


def test_body_encrypted_at_rest(tmp_path):
    p = tmp_path / "e.db"
    d = Database(p, FieldCipher(os.urandom(32)))
    add(d, "m1", body="phrase-ultra-secrete-123", extra={"name": "plan-secret.pdf"})
    assert d.get_message("m1")["body"] == "phrase-ultra-secrete-123"
    d.conn.execute("PRAGMA wal_checkpoint(FULL)")
    d.close()
    raw = b"".join(open(os.path.join(tmp_path, f), "rb").read() for f in os.listdir(tmp_path))
    assert b"phrase-ultra-secrete-123" not in raw and b"plan-secret" not in raw


def test_wrong_key_cannot_read(tmp_path):
    p = tmp_path / "k.db"
    d = Database(p, FieldCipher(os.urandom(32)))
    add(d, "m1")
    d.close()
    d2 = Database(p, FieldCipher(os.urandom(32)))
    with pytest.raises(ValueError):
        d2.get_message("m1")


def test_dedupe_and_stable_order(db):
    assert add(db, "a", ts=1000)
    assert not add(db, "a", ts=1000)  # même mid : ignoré
    add(db, "b", ts=1000)
    add(db, "c", ts=1000)  # même horodatage : ordre d'insertion conservé
    assert [m["mid"] for m in db.list_messages("C1")] == ["a", "b", "c"]


def test_pagination(db):
    for i in range(30):
        add(db, f"m{i:02d}", ts=1000 + i)
    page1 = db.list_messages("C1", limit=10)
    assert [m["mid"] for m in page1] == [f"m{i:02d}" for i in range(20, 30)]
    page2 = db.list_messages("C1", before=(page1[0]["ts"], page1[0]["id"]), limit=10)
    assert [m["mid"] for m in page2] == [f"m{i:02d}" for i in range(10, 20)]


def test_receipts_only_advance_and_aggregate(db):
    add(db, "g1")
    db.init_receipts("g1", ["A", "B"])
    assert db.advance_receipt("g1", "A", "delivered") == "pending"  # B pas encore
    assert db.advance_receipt("g1", "B", "sent") == "sent"
    assert db.advance_receipt("g1", "B", "delivered") == "delivered"
    assert db.advance_receipt("g1", "A", "sent") is None  # pas de recul
    assert db.get_message("g1")["status"] == "delivered"
    db.advance_receipt("g1", "A", "read")
    db.advance_receipt("g1", "B", "read")
    assert db.get_message("g1")["status"] == "read"


def test_failed_status_is_sticky(db):
    add(db, "f1", status="failed")
    db.init_receipts("f1", ["A"])
    assert db.advance_receipt("f1", "A", "delivered") is None
    assert db.get_message("f1")["status"] == "failed"


def test_unread_and_mark_read_starts_ttl(db):
    add(db, "i1", direction="in", status="received", ttl=60)
    add(db, "i2", direction="in", status="received")
    assert db.unread_counts() == {"C1": 2}
    t = 1_000_000
    assert db.mark_read("C1", t) == ["i1", "i2"]
    assert db.unread_counts() == {}
    assert db.get_message("i1")["expires_at"] == t + 60_000
    assert db.get_message("i2")["expires_at"] is None


def test_expiry_global(db):
    add(db, "e1", expires_at=500)
    add(db, "e2", expires_at=now_ms() + 10_000_000)
    add(db, "e3")
    gone = db.delete_expired(now_ms())
    assert gone == [("e1", "C1")]
    assert db.get_message("e1") is None and db.get_message("e2") is not None


def test_search_case_insensitive_and_scoped(db):
    add(db, "s1", body="Rendez-vous à la GARE demain")
    add(db, "s2", conv="C2", body="la gare est fermée")
    add(db, "s3", body="rien")
    assert {m["mid"] for m in db.search("gare")} == {"s1", "s2"}
    assert [m["mid"] for m in db.search("GARE", conv="C2")] == ["s2"]
    assert db.search("   ") == []


def test_outbox_flow(db):
    db.outbox_add("e1", "LDC-B", {"x": 1}, msg_mid="m1")
    db.outbox_add("e1", "LDC-B", {"x": 1}, msg_mid="m1")  # idempotent
    due = db.outbox_due(now_ms() + 1)
    assert len(due) == 1 and due[0]["envelope"] == {"x": 1}
    db.outbox_add("e1", "LDC-C", {"x": 1}, msg_mid="m1")  # même enveloppe, autre destinataire (groupe)
    assert len(db.outbox_due(now_ms() + 1)) == 2
    db.outbox_delete(db.outbox_due(now_ms() + 1)[1]["id"])
    rid = due[0]["id"]
    db.outbox_update(rid, attempts=3, next_try=now_ms() + 10 ** 7)
    assert db.outbox_due(now_ms()) == []
    db.outbox_wake_pending()
    assert len(db.outbox_due(now_ms() + 1)) == 1
    db.outbox_delete(rid)
    assert db.outbox_due(now_ms() + 1) == []


def test_seen_dedupe(db):
    assert db.mark_seen("x") and not db.mark_seen("x")


def test_delete_contact_cascades(db):
    db.put_contact("LDC-B", "Bob", b"s" * 32, b"b" * 32, "accepted")
    add(db, "m1", conv="LDC-B")
    db.outbox_add("e1", "LDC-B", {}, msg_mid="m1")
    db.delete_contact("LDC-B")
    assert db.get_contact("LDC-B") is None and db.list_messages("LDC-B") == []
    assert db.outbox_due(now_ms() + 1) == []


def test_update_whitelists(db):
    with pytest.raises(ValueError):
        db.update_contact("x", sign_pk=b"")
    with pytest.raises(ValueError):
        db.update_message("x", sender="evil")
