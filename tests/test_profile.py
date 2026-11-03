import pytest

from ldc_p2p import paths, profile
from ldc_p2p.crypto.identity import Identity, InvalidPassword

FAST = 2 ** 10


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDC_P2P_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_create_unlock_and_paths_use_id_not_name(home):
    p = profile.create_profile("../../etc/passwd", "motdepasse1", scrypt_n=FAST)
    assert p.directory.parent == paths.profiles_root() and p.directory.name == p.identity.id
    assert p.identity.name == "../../etc/passwd".replace("\x00", "")[:40]
    p.db.close()
    assert paths.list_profiles() == [p.identity.id]
    p2 = profile.unlock_profile(p.identity.id, "motdepasse1")
    assert p2.identity.id == p.identity.id
    p2.db.close()
    with pytest.raises(InvalidPassword):
        profile.unlock_profile(p.identity.id, "mauvais mdp!")


def test_invalid_id_rejected_for_paths():
    for bad in ("../x", "LDC-short", "", "LDC-AAAAAA-BBBBBB-CCCCCC-DDDD/.."):
        with pytest.raises(ValueError):
            paths.profile_dir(bad)


def test_duplicate_profile_refused(home):
    p = profile.create_profile("A", "motdepasse1", scrypt_n=FAST)
    p.db.close()
    with pytest.raises(ValueError):
        profile.create_profile("A", "motdepasse1", p.identity, scrypt_n=FAST)


def test_restore_from_recovery_code_gives_same_identity(home):
    p = profile.create_profile("Alice", "motdepasse1", scrypt_n=FAST)
    code, ident = p.identity.recovery_code(), p.identity.id
    p.db.close()
    profile.delete_profile(ident)
    assert paths.list_profiles() == []
    r = profile.restore_from_recovery_code(code, "Alice", "autre-mdp-1", scrypt_n=FAST)
    assert r.identity.id == ident
    r.db.close()


def test_delete_only_touches_profile_dir(home):
    keep = paths.data_dir() / "autre-fichier.txt"
    keep.write_text("ne pas toucher")
    other = profile.create_profile("B", "motdepasse1", scrypt_n=FAST)
    other.db.close()
    p = profile.create_profile("A", "motdepasse1", scrypt_n=FAST)
    p.db.close()
    profile.delete_profile(p.identity.id)
    assert keep.exists() and (other.directory / "keystore.json").exists() and not p.directory.exists()


def test_backup_roundtrip_and_wrong_identity(home, tmp_path):
    a = profile.create_profile("A", "motdepasse1", scrypt_n=FAST)
    bob = Identity.generate("Bob")
    a.db.put_contact(bob.id, "Bob", bob.public.sign_pk, bob.public.box_pk, "accepted")
    a.db.update_contact(bob.id, verified=1, alias="Bobby")
    a.db.put_group("0123456789abcdef", "Copains", a.identity.id, [a.identity.id, bob.id], 3)
    out = tmp_path / "save.ldcbackup"
    profile.export_backup(a.db, a.identity, out, "phrase secrete", scrypt_n=FAST)
    assert b"Bob" not in out.read_bytes()
    code = a.identity.recovery_code()
    a.db.close()
    profile.delete_profile(a.identity.id)
    r = profile.restore_from_recovery_code(code, "A", "motdepasse1", scrypt_n=FAST)
    assert profile.import_backup(r.db, r.identity, out, "phrase secrete") == {"contacts": 1, "groups": 1}
    c = r.db.get_contact(bob.id)
    assert c["verified"] == 1 and c["alias"] == "Bobby"
    assert r.db.get_group("0123456789abcdef")["version"] == 3
    with pytest.raises(InvalidPassword):
        profile.import_backup(r.db, r.identity, out, "mauvaise phrase")
    other = profile.create_profile("Z", "motdepasse1", scrypt_n=FAST)
    with pytest.raises(InvalidPassword):
        profile.import_backup(other.db, other.identity, out, "phrase secrete")
    r.db.close(); other.db.close()
