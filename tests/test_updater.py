import base64
import hashlib
import json

import pytest
from nacl.signing import SigningKey

from ldc_p2p import updater

SK = SigningKey.generate()
PK = bytes(SK.verify_key)


def make(version="2.1.0", payload=b"installateur", url="https://exemple.fr/setup.exe", sha=None):
    manifest = json.dumps({"version": version, "url": url, "sha256": sha or hashlib.sha256(payload).hexdigest(), "notes": "n"}).encode()
    return manifest, base64.b64encode(SK.sign(manifest).signature).decode(), payload


def fetcher(files):
    return lambda url, limit: files[url.rsplit("/", 1)[-1]] if url.rsplit("/", 1)[-1] in files else (_ for _ in ()).throw(KeyError(url))


def test_version_ordering():
    assert updater.is_newer("2.0.1", "2.0.0")
    assert updater.is_newer("2.0.0", "2.0.0a1") and updater.is_newer("2.0.0rc1", "2.0.0b2")
    assert not updater.is_newer("2.0.0", "2.0.0") and not updater.is_newer("1.9.9", "2.0.0")
    with pytest.raises(updater.UpdateError):
        updater.parse_version("latest")


def test_valid_update_found_and_downloaded(tmp_path):
    m, sig, payload = make()
    files = {"manifest.json": m, "manifest.sig": sig.encode(), "setup.exe": payload}
    up = updater.check_for_update("https://exemple.fr", "2.0.0", PK, fetcher(files))
    assert up and up.version == "2.1.0"
    out = updater.download_update(up, tmp_path, fetcher(files))
    assert out.read_bytes() == payload


def test_no_update_when_current():
    m, sig, _ = make("2.0.0")
    files = {"manifest.json": m, "manifest.sig": sig.encode()}
    assert updater.check_for_update("https://exemple.fr", "2.0.0", PK, fetcher(files)) is None


def test_forged_manifest_rejected():
    m, sig, _ = make()
    evil = m.replace(b"2.1.0", b"9.9.9")
    files = {"manifest.json": evil, "manifest.sig": sig.encode()}
    with pytest.raises(updater.UpdateError):
        updater.check_for_update("https://exemple.fr", "2.0.0", PK, fetcher(files))
    other = SigningKey.generate()
    sig2 = base64.b64encode(other.sign(m).signature).decode()
    with pytest.raises(updater.UpdateError):
        updater.check_for_update("https://exemple.fr", "2.0.0", PK, fetcher({"manifest.json": m, "manifest.sig": sig2.encode()}))


def test_tampered_installer_rejected(tmp_path):
    m, sig, _ = make(payload=b"bon fichier")
    files = {"manifest.json": m, "manifest.sig": sig.encode(), "setup.exe": b"fichier piege"}
    up = updater.check_for_update("https://exemple.fr", "2.0.0", PK, fetcher(files))
    with pytest.raises(updater.UpdateError):
        updater.download_update(up, tmp_path, fetcher(files))
    assert not list(tmp_path.iterdir())


def test_http_refused():
    with pytest.raises(updater.UpdateError):
        updater._https_get("http://exemple.fr/manifest.json", 100)
    m, sig, _ = make(url="http://exemple.fr/setup.exe")
    with pytest.raises(updater.UpdateError):
        updater.check_for_update("https://exemple.fr", "2.0.0", PK, fetcher({"manifest.json": m, "manifest.sig": sig.encode()}))
