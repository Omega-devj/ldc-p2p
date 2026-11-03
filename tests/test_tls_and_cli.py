import asyncio
import datetime
import ipaddress
import socket
import ssl
import subprocess
import sys
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from conftest import wait_for
from ldc_p2p.crypto import e2e
from ldc_p2p.crypto.identity import Identity
from ldc_p2p.network.relay_client import RelayClient
from ldc_p2p.server.relay_server import RelayServer


def make_cert(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    c, k = tmp_path / "c.pem", tmp_path / "k.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return c, k


async def test_tls_connection_and_untrusted_cert_refused(tmp_path):
    c, k = make_cert(tmp_path)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(c, k)
    srv = RelayServer("127.0.0.1", 0, ssl_ctx=sctx)
    await srv.start()
    try:
        trusting = ssl.create_default_context(cafile=str(c))
        a, b = Identity.generate("A"), Identity.generate("B")
        got = []

        async def deliver(env):
            got.append(env)
            return True
        ra = RelayClient(a, "127.0.0.1", srv.port, tls=True, ssl_ctx=trusting)
        rb = RelayClient(b, "127.0.0.1", srv.port, tls=True, ssl_ctx=trusting, on_deliver=deliver)
        ra.start(), rb.start()
        await wait_for(lambda: ra.connected and rb.connected)
        assert await ra.send_env(b.id, e2e.seal(a, b.public, {"t": "msg", "text": "tls"})) == "delivered"
        await wait_for(lambda: len(got) == 1)
        # client qui ne fait pas confiance au certificat : refus
        rc = RelayClient(Identity.generate("C"), "127.0.0.1", srv.port, tls=True, backoff_max=0.2)
        rc.start()
        await asyncio.sleep(1.0)
        assert not rc.connected and "CERTIFICATE" in (rc.last_error or "").upper()
        await asyncio.gather(ra.stop(), rb.stop(), rc.stop())
    finally:
        await srv.stop()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_server_cli_runs_without_qt(tmp_path):
    port = free_port()
    code = "import sys; sys.modules['PySide6'] = None; sys.argv = ['x', '--host', '127.0.0.1', '--port', '%d', '--db', r'%s']; " \
           "from ldc_p2p.server.__main__ import main; main()" % (port, tmp_path / "r.db")
    p = subprocess.Popen([sys.executable, "-c", code], env={"PYTHONPATH": "src", "PATH": ""}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        ident = Identity.generate("A")
        rc = RelayClient(ident, "127.0.0.1", port, backoff_max=0.3)
        rc.start()
        await wait_for(lambda: rc.connected, timeout=10)
        await rc.stop()
    finally:
        p.terminate()
        p.wait(5)
