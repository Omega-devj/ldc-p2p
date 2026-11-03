"""Mode démonstration : profil jetable, hors ligne, avec des conversations fictives."""
from __future__ import annotations

import os
import tempfile
import time

from . import profile
from .crypto.e2e import new_mid
from .crypto.identity import Identity
from .storage.database import Database, now_ms

NAMES = ["Camille", "Noah", "Inès", "Lucas", "Sacha", "Maëlle"]


def seed(p: profile.Profile) -> dict[str, Identity]:
    db: Database = p.db
    me = p.identity.id
    ids = {n: Identity.generate(n) for n in NAMES}
    states = {"Camille": "accepted", "Noah": "accepted", "Inès": "accepted", "Lucas": "accepted", "Sacha": "pending_in", "Maëlle": "pending_out"}
    for n, ident in ids.items():
        db.put_contact(ident.id, n, ident.public.sign_pk, ident.public.box_pk, states[n])
    db.update_contact(ids["Camille"].id, verified=1)
    now = now_ms()
    mn = 60_000

    def msg(conv, sender, text, ago, direction=None, status=None, kind="text", extra=None, read=True, ttl=0):
        d = direction or ("out" if sender == me else "in")
        db.add_message(mid=new_mid(), conv=conv, sender=sender, direction=d, body=text, ts=now - ago * mn,
                       status=status or ("read" if d == "out" else "received"), kind=kind, extra=extra, ttl=ttl)
        if d == "in" and read:
            db.conn.execute("UPDATE messages SET read_at=ts WHERE read_at IS NULL AND conv=?", (conv,))

    cam = ids["Camille"].id
    msg(cam, cam, "Hello ! Tu as testé la nouvelle version de LDC P2P ?", 1500, read=True)
    msg(cam, me, "Oui, enfin une messagerie qui marche entre deux réseaux 🎉", 1496)
    msg(cam, cam, "Le numéro de sécurité, on se le compare quand on se voit samedi ?", 1490)
    msg(cam, me, "Parfait, je note. Je t'envoie le plan du week-end d'abord.", 60, status="read")
    msg(cam, me, "plan-weekend.pdf", 58, kind="file", extra={"name": "plan-weekend.pdf", "size": 2_480_000, "fid": "a" * 32, "sha256": "0" * 64, "n": 26, "chunk": 98304}, status="delivered")
    msg(cam, cam, "Reçu ! Regarde ce lien pour le gîte : https://exemple.org/gite-annecy", 12)
    msg(cam, cam, "On part à quelle heure vendredi ?", 11, read=False)
    msg(cam, cam, "Je peux conduire si tu veux", 10, read=False)

    noah = ids["Noah"].id
    msg(noah, noah, "Tu as relu le contrat ?", 300)
    msg(noah, me, "Je termine ça ce soir et je te renvoie mes remarques.", 290, status="delivered")
    ines = ids["Inès"].id
    msg(ines, ines, "Merci pour hier, c'était top !", 3000)
    msg(ines, me, "Avec plaisir 🙂", 2990, status="read")
    lucas = ids["Lucas"].id
    msg(lucas, lucas, "On se fait une partie ce soir ?", 6000, read=False)
    msg(lucas, lucas, "Je lance le salon à 21h", 5999, read=False)
    msg(lucas, lucas, "Dis-moi si tu viens", 5998, read=False)
    db.add_message(mid=new_mid(), conv=ids["Sacha"].id, sender=ids["Sacha"].id, direction="in", body="Salut, c'est Sacha de la coloc !", ts=now - 5 * mn,
                   status="received", kind="system")

    db.put_group("a1b2c3d4e5f60718", "Week-end Annecy", me, [me, cam, noah, ines], 1)
    for n in ("Noah", "Inès"):
        pass
    g = "grp:a1b2c3d4e5f60718"
    msg(g, noah, "J'ai réservé le gîte, il reste deux chambres !", 200)
    msg(g, ines, "Génial, je prends celle avec la vue 😄", 190)
    msg(g, me, "Je m'occupe des courses.", 180, status="delivered")
    db.set_conv_setting(g, pinned=1)
    db.set_conv_setting(lucas, muted=1)
    return ids


def start_demo(shell) -> None:
    home = tempfile.mkdtemp(prefix="ldc-demo-")
    os.environ["LDC_P2P_HOME"] = home
    p = profile.create_profile("Alex", "demo-demo-demo", scrypt_n=2 ** 10)
    ids = seed(p)
    shell.start_session(p, {})

    async def decorate():
        import asyncio
        while shell.window is None:
            await asyncio.sleep(0.05)
        e = shell.engine
        e.server_online.update({ids["Camille"].id: True, ids["Noah"].id: True, ids["Inès"].id: True})
        e.peer_status.update({ids["Noah"].id: "away", ids["Inès"].id: "dnd"})
        e.events.emit("presence", id=None)

    import asyncio
    asyncio.ensure_future(decorate())
