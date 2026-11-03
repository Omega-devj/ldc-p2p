import json

import pytest

from ldc_p2p.crypto import e2e
from ldc_p2p.crypto.identity import (Identity, InvalidPassword, InvalidRecoveryCode, Keystore,
                                     PublicIdentity, clean_name, safety_number, ID_RE)

FAST = 2 ** 10  # scrypt rapide pour les tests uniquement


def test_id_is_self_certifying_and_stable():
    a = Identity.generate("Alice")
    assert ID_RE.match(a.id)
    assert Identity(a.seed, "autre pseudo").id == a.id  # le pseudo n'influence pas l'ID
    assert Identity.generate("Alice").id != a.id


def test_recovery_code_roundtrip_and_typo_detection():
    a = Identity.generate("Alice")
    code = a.recovery_code()
    assert Identity.from_recovery_code(code, "Alice").id == a.id
    assert Identity.from_recovery_code(code.lower().replace("-", " "), "x").id == a.id
    bad = code[:-1] + ("A" if code[-1] != "A" else "B")
    with pytest.raises(InvalidRecoveryCode):
        Identity.from_recovery_code(bad, "x")


def test_keystore_wrong_password_and_roundtrip(tmp_path):
    a = Identity.generate("Alice")
    p = tmp_path / "keystore.json"
    Keystore.create(p, a, "correct horse", n=FAST)
    assert Keystore.unlock(p, "correct horse").id == a.id
    with pytest.raises(InvalidPassword):
        Keystore.unlock(p, "wrong password")
    raw = p.read_text()
    assert a.seed.hex() not in raw and "Alice" not in raw  # rien en clair
    Keystore.change_password(p, "correct horse", "new password!", n=FAST)
    assert Keystore.unlock(p, "new password!").id == a.id
    with pytest.raises(InvalidPassword):
        Keystore.unlock(p, "correct horse")


def test_keystore_rejects_short_password(tmp_path):
    with pytest.raises(ValueError):
        Keystore.create(tmp_path / "k.json", Identity.generate("A"), "short", n=FAST)


def test_clean_name_strips_bidi_and_controls():
    assert clean_name("Bob\u202eevil\x00") == "Bobevil"
    assert clean_name("   ") == "Anonyme"
    assert len(clean_name("x" * 500)) == 40


def test_seal_open_roundtrip_and_confidentiality():
    a, b = Identity.generate("A"), Identity.generate("B")
    env = e2e.seal(a, b.public, {"t": "msg", "text": "secret très secret"})
    assert "secret" not in json.dumps(env)
    inner = e2e.open_envelope(env, b, a.public)
    assert inner["text"] == "secret très secret"


def test_third_party_cannot_read():
    a, b, eve = Identity.generate("A"), Identity.generate("B"), Identity.generate("E")
    env = e2e.seal(a, b.public, {"t": "msg", "text": "x"})
    with pytest.raises(e2e.EnvelopeError):
        e2e.open_envelope(env, eve, a.public)  # destiné à B


def test_impersonation_with_own_key_is_rejected():
    """Eve se fait passer pour Alice : elle signe avec SA clé mais annonce l'ID d'Alice."""
    a, b, eve = Identity.generate("A"), Identity.generate("B"), Identity.generate("E")
    env = e2e.seal(eve, b.public, {"t": "msg", "text": "je suis Alice"})
    env["from"] = a.id  # usurpation de l'identifiant
    with pytest.raises(e2e.EnvelopeError):
        e2e.open_envelope(env, b, a.public)  # clé épinglée d'Alice : signature invalide
    # et sans changer l'ID, la clé d'Eve ne correspond pas à l'ID épinglé d'Alice
    env2 = e2e.seal(eve, b.public, {"t": "msg", "text": "x"})
    with pytest.raises(e2e.EnvelopeError):
        e2e.open_envelope(env2, b, a.public)


def test_tampering_is_detected():
    a, b = Identity.generate("A"), Identity.generate("B")
    env = e2e.seal(a, b.public, {"t": "msg", "text": "hello"})
    for field, val in (("ts", env["ts"] + 1), ("id", "f" * 32), ("ct", env["ct"][:-4] + "AAAA")):
        bad = dict(env)
        bad[field] = val
        with pytest.raises(e2e.EnvelopeError):
            e2e.open_envelope(bad, b, a.public)


def test_redirecting_envelope_to_other_recipient_fails():
    a, b, c = Identity.generate("A"), Identity.generate("B"), Identity.generate("C")
    env = e2e.seal(a, b.public, {"t": "msg", "text": "pour B"})
    env["to"] = c.id
    with pytest.raises(e2e.EnvelopeError):
        e2e.open_envelope(env, c, a.public)


@pytest.mark.parametrize("bad", [None, [], {}, {"v": 2}, {"v": 1, "k": "x"}, "texte"])
def test_malformed_envelopes_rejected(bad):
    with pytest.raises(e2e.EnvelopeError):
        e2e.validate_envelope(bad)


def test_unknown_extra_field_rejected():
    a, b = Identity.generate("A"), Identity.generate("B")
    env = e2e.seal(a, b.public, {"t": "msg"})
    env["extra"] = 1
    with pytest.raises(e2e.EnvelopeError):
        e2e.validate_envelope(env)


def test_padding_hides_exact_length():
    a, b = Identity.generate("A"), Identity.generate("B")
    l1 = len(e2e.seal(a, b.public, {"t": "msg", "text": "a"})["ct"])
    l2 = len(e2e.seal(a, b.public, {"t": "msg", "text": "abcdefghij"})["ct"])
    assert l1 == l2


def test_card_roundtrip():
    a = Identity.generate("Alice")
    code = e2e.encode_card(e2e.make_card(a, "relay.example:9443"))
    pub, hint = e2e.parse_card(e2e.decode_card(code))
    assert pub.id == a.id and hint == "relay.example:9443" and pub.name == "Alice"
    assert e2e.parse_card(e2e.decode_card("ldc://add/" + code))[0].id == a.id


def test_card_forgery_rejected():
    a, eve = Identity.generate("Alice"), Identity.generate("Eve")
    card = e2e.make_card(a)
    forged = dict(card, sk=e2e.b64(eve.public.sign_pk))  # clé d'Eve, ID d'Alice
    with pytest.raises(e2e.CardError):
        e2e.parse_card(forged)
    renamed = dict(card, name="Autre")  # le pseudo est signé
    with pytest.raises(e2e.CardError):
        e2e.parse_card(renamed)
    # carte entièrement refabriquée par Eve mais avec l'ID d'Alice
    card2 = e2e.make_card(eve)
    card2["id"] = a.id
    with pytest.raises(e2e.CardError):
        e2e.parse_card(card2)


def test_card_garbage():
    for bad in ("", "hello", "ldc1:@@@", "ldc1:" + "A" * 10):
        with pytest.raises(e2e.CardError):
            e2e.parse_card(e2e.decode_card(bad))


def test_safety_number_symmetric():
    a, b = Identity.generate("A"), Identity.generate("B")
    assert safety_number(a.public, b.public) == safety_number(b.public, a.public)
    assert safety_number(a.public, b.public) != safety_number(a.public, Identity.generate("C").public)
