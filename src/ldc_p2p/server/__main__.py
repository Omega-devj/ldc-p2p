"""Lancement du serveur : python -m ldc_p2p.server --port 9443 --db ldc-relay.db

Toutes les options existent aussi en variables d'environnement (pratique pour Docker) :
LDC_HOST, LDC_PORT, LDC_DB, LDC_TLS_CERT, LDC_TLS_KEY, LDC_TOKEN, LDC_ALLOW_FILE, LDC_BLOCK_FILE,
LDC_STATUS_PORT, LDC_STATUS_HOST, LDC_LOG_FILE.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import ssl

from .relay_server import RelayServer


def main() -> None:
    env = os.environ.get
    ap = argparse.ArgumentParser(description="Serveur de rendez-vous et de relais LDC P2P")
    ap.add_argument("--host", default=env("LDC_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(env("LDC_PORT", "9443")))
    ap.add_argument("--db", default=env("LDC_DB", "ldc-relay.db"), help="boîte aux lettres (enveloppes chiffrées uniquement)")
    ap.add_argument("--tls-cert", default=env("LDC_TLS_CERT"))
    ap.add_argument("--tls-key", default=env("LDC_TLS_KEY"))
    ap.add_argument("--token", default=env("LDC_TOKEN", ""), help="serveur privé : jeton d'accès exigé des clients")
    ap.add_argument("--allow-file", default=env("LDC_ALLOW_FILE"), help="liste d'identifiants LDC autorisés (un par ligne)")
    ap.add_argument("--block-file", default=env("LDC_BLOCK_FILE"), help="liste d'adresses IP bloquées (une par ligne)")
    ap.add_argument("--status-port", type=int, default=int(env("LDC_STATUS_PORT", "0")) or None, help="page de statut JSON (sans donnée personnelle)")
    ap.add_argument("--status-host", default=env("LDC_STATUS_HOST", "127.0.0.1"))
    ap.add_argument("--log-file", default=env("LDC_LOG_FILE"))
    args = ap.parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.handlers.RotatingFileHandler(args.log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    ctx = None
    if args.tls_cert and args.tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.tls_cert, args.tls_key)
    elif args.host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("Pas de TLS : le contenu reste chiffré de bout en bout, mais les métadonnées "
                        "(identifiants, IP) transitent en clair. Utilisez --tls-cert/--tls-key en production.")
    if args.status_port and args.status_host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("La page de statut est exposée au-delà de la machine locale ; elle ne contient que des compteurs.")

    async def run() -> None:
        srv = RelayServer(args.host, args.port, args.db, ctx, access_token=args.token, allow_file=args.allow_file,
                          block_file=args.block_file, status_port=args.status_port, status_host=args.status_host)
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
