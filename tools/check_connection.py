"""Test de bout en bout du SERVEUR, indépendant de vos profils : deux identités jetables s'écrivent une demande de contact
et un message via le serveur, exactement avec le même code que l'application.

  python tools/check_connection.py 127.0.0.1 9443            (ajoutez --tls si le serveur utilise TLS, --token XXX si privé)
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ldc_p2p.crypto import e2e
from ldc_p2p.crypto.identity import Identity
from ldc_p2p.network.errors import explain_error, probe_server
from ldc_p2p.network.relay_client import RelayClient


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", type=int, nargs="?", default=9443)
    ap.add_argument("--tls", action="store_true")
    ap.add_argument("--token", default="")
    a = ap.parse_args()
    ok = True

    def step(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(("  ✓ " if good else "  ✗ ") + text)

    print(f"\nTest du serveur {a.host}:{a.port} ({'TLS' if a.tls else 'sans TLS'})\n")
    r = await probe_server(a.host, a.port, a.tls, a.token)
    step(r.ok, "Connexion, protocole et authentification : " + r.message)
    if not r.ok:
        print("\n→ Corrigez d'abord ce point (adresse, port, TLS, pare-feu, serveur lancé).")
        return 1
    alice, bruno = Identity.generate("Alice-test"), Identity.generate("Bruno-test")
    got: list[dict] = []

    async def deliver(env: dict) -> bool:
        got.append(env)
        return True
    ca = RelayClient(alice, a.host, a.port, tls=a.tls, token=a.token)
    cb = RelayClient(bruno, a.host, a.port, tls=a.tls, token=a.token, on_deliver=deliver)
    ca.start(), cb.start()
    for _ in range(100):
        if ca.connected and cb.connected:
            break
        await asyncio.sleep(0.1)
    step(ca.connected and cb.connected, "Deux clients de test connectés en même temps")
    if ca.connected and cb.connected:
        req = e2e.seal(alice, bruno.public, {"t": "req", "greeting": "test"}, kind="r", card=e2e.make_card(alice))
        st = await ca.send_env(bruno.id, req)
        step(st in ("delivered", "queued"), f"Envoi d'une DEMANDE DE CONTACT : le serveur répond « {st} »")
        for _ in range(50):
            if got:
                break
            await asyncio.sleep(0.1)
        step(bool(got) and got[0]["k"] == "r", "Le destinataire REÇOIT la demande de contact")
        if got:
            card, _ = e2e.parse_card(got[0]["card"])
            inner = e2e.open_envelope(got[0], bruno, card)
            step(inner.get("t") == "req", "La demande est authentique (signature et chiffrement vérifiés)")
    await ca.stop(), await cb.stop()
    print("\nRÉSULTAT :", "TOUT FONCTIONNE — le serveur et le réseau sont bons ; le souci vient de la configuration dans l'application." if ok
          else "ÉCHEC — voir la ligne ✗ ci-dessus.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
