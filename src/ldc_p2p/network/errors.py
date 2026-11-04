"""Messages d'erreur réseau compréhensibles (sans jargon) + sonde de connexion au serveur."""
from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import dataclass

from ..crypto.identity import Identity, b64, unb64
from .framing import FrameError, read_frame, write_frame


def explain_error(err: object, tls: bool = False) -> str:
    low = str(err).lower()
    if "wrong_version_number" in low or "record layer" in low or "http request" in low:
        return ("le serveur n'utilise pas TLS alors que l'option est activée. Désactivez « Chiffrer la connexion au serveur (TLS) », "
                "puis cliquez sur Appliquer.")
    if "certificate" in low or "ssl" in low and "verify" in low:
        return "le certificat TLS du serveur n'est pas reconnu (nom de domaine incorrect, certificat expiré ou auto-signé)."
    if (isinstance(err, ConnectionRefusedError) or "refused" in low or "refus" in low or "10061" in low
            or "errno 111" in low or "connect call failed" in low):
        return "aucun serveur n'écoute à cette adresse et ce port. Vérifiez que le serveur est lancé et que le pare-feu l'autorise."
    if "timeout" in low or "timed out" in low or isinstance(err, asyncio.TimeoutError):
        return ("le serveur ne répond pas (mauvaise adresse, pare-feu, serveur éteint)" +
                (" ou TLS désactivé alors que le serveur l'exige." if tls else "."))
    if "getaddrinfo" in low or "name or service" in low or "11001" in low or "nodename" in low:
        return "adresse introuvable : vérifiez l'orthographe du nom du serveur et votre connexion Internet."
    if "accès refusé" in low or "denied" in low:
        return "accès refusé : ce serveur est privé (jeton d'accès incorrect ou identifiant non invité)."
    if "connexion fermée" in low or "closed" in low or "reset" in low:
        return ("la connexion a été coupée immédiatement" + (" (le serveur attend peut-être du TLS)." if not tls else " (le serveur n'utilise peut-être pas TLS)."))
    return str(err) or "erreur inconnue"


@dataclass
class ProbeResult:
    ok: bool
    message: str
    ms: int = 0


async def probe_server(host: str, port: int, tls: bool = False, token: str = "", timeout: float = 6.0) -> ProbeResult:
    """Teste le serveur avec une identité jetable : connexion, protocole, authentification, jeton d'accès."""
    if not host.strip():
        return ProbeResult(False, "Renseignez l'adresse du serveur.")
    t0 = time.monotonic()
    writer = None
    try:
        ctx = ssl.create_default_context() if tls else None
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host.strip(), port, ssl=ctx), timeout)
        hello = await asyncio.wait_for(read_frame(reader, 8192), timeout)
        if hello.get("t") != "hello" or hello.get("v") != 1:
            return ProbeResult(False, "Ce serveur ne parle pas le protocole LDC P2P (mauvais port ?).")
        ident = Identity.generate("sonde")
        pub = ident.public
        await write_frame(writer, {"t": "auth", "id": ident.id, "sk": b64(pub.sign_pk), "bk": b64(pub.box_pk),
                                   "sig": b64(ident.sign(b"ldc-auth-v1\0" + unb64(hello["challenge"]) + ident.id.encode())),
                                   "visible": False, **({"tok": token} if token else {})})
        reply = await asyncio.wait_for(read_frame(reader, 8192), timeout)
        ms = int((time.monotonic() - t0) * 1000)
        if reply.get("t") == "ready":
            return ProbeResult(True, f"Connexion réussie ({ms} ms){' — chiffrée par TLS' if tls else ' — sans TLS'}.", ms)
        return ProbeResult(False, explain_error("denied" if reply.get("reason") == "denied" else "authentification refusée", tls))
    except (OSError, ssl.SSLError, ConnectionError, FrameError, asyncio.TimeoutError, UnicodeError) as e:
        return ProbeResult(False, explain_error(e, tls))
    finally:
        if writer:
            writer.close()
