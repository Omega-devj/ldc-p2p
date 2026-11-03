"""Lancement du serveur : python -m ldc_p2p.server --port 9443 --db ldc-relay.db"""
from __future__ import annotations

import argparse
import asyncio
import logging
import ssl

from .relay_server import RelayServer


def main() -> None:
    ap = argparse.ArgumentParser(description="Serveur de rendez-vous et de relais LDC P2P")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9443)
    ap.add_argument("--db", default="ldc-relay.db", help="fichier SQLite de la boîte aux lettres (enveloppes chiffrées)")
    ap.add_argument("--tls-cert")
    ap.add_argument("--tls-key")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctx = None
    if args.tls_cert and args.tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.tls_cert, args.tls_key)
    elif args.host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("Pas de TLS : le contenu reste chiffré de bout en bout, mais les métadonnées "
                        "(identifiants, IP) transitent en clair. Utilisez --tls-cert/--tls-key en production.")

    async def run() -> None:
        srv = RelayServer(args.host, args.port, args.db, ctx)
        await srv.start()
        try:
            await asyncio.Event().wait()
        finally:
            await srv.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
