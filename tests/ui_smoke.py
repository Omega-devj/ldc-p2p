"""Test de fumée bout en bout (serveur + deux profils + deux fenêtres), exécuté hors écran.

    QT_QPA_PLATFORM=offscreen python tests/ui_smoke.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LDC_P2P_HOME"] = tempfile.mkdtemp(prefix="ldc-smoke-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import qasync

from ldc_p2p import paths, profile
from ldc_p2p.app import build_app
from ldc_p2p.engine import Engine, EngineSettings
from ldc_p2p.server.relay_server import RelayServer
from ldc_p2p.ui.controller import UiController
from ldc_p2p.ui.main_window import MainWindow

app = build_app(["smoke"])
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)
OUT = Path(os.environ.get("SMOKE_OUT", "/tmp/shots"))
OUT.mkdir(parents=True, exist_ok=True)
failures: list[str] = []


def check(cond, msg):
    print(("  ok  " if cond else "  FAIL"), msg)
    if not cond:
        failures.append(msg)


async def until(cond, timeout=10.0):
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def make_user(name, port):
    p = profile.create_profile(name, "motdepasse-test", scrypt_n=2 ** 10)
    s = EngineSettings.load(p.db)
    s.server_host, s.server_port, s.direct_enabled = "127.0.0.1", port, False
    s.notifications = False
    e = Engine(p.identity, p.db, s, download_dir=Path(tempfile.mkdtemp(prefix=f"dl-{name}-")))
    await e.start()
    ctrl = UiController(e)
    w = MainWindow(p, ctrl)
    w.resize(1300, 820)
    w.show()
    return p, e, ctrl, w


async def main():
    srv = RelayServer("127.0.0.1", 0)
    await srv.start()
    pa, ea, ca, wa = await make_user("Alice", srv.port)
    pb, eb, cb, wb = await make_user("Bruno", srv.port)
    check(await until(lambda: ea.relay.connected and eb.relay.connected), "les deux clients se connectent au serveur")

    # contact + acceptation
    await ea.add_contact_from_code(eb.my_card_code(), "Salut Bruno")
    check(await until(lambda: eb.db.get_contact(pa.identity.id) and eb.db.get_contact(pa.identity.id)["state"] == "pending_in"), "Bruno reçoit la demande")
    check(wb.nav[1].badge == 1, "pastille « demandes » sur l'onglet Contacts")
    await eb.accept_request(pa.identity.id)
    check(await until(lambda: ea.db.get_contact(pb.identity.id)["state"] == "accepted"), "Alice voit la demande acceptée")

    # envoi depuis le composer d'Alice
    wa.chats.open_conv(pb.identity.id)
    await asyncio.sleep(0.2)
    check(wa.chats.composer.btn_send.isEnabled(), "composer actif pour un contact accepté")
    wa.chats.composer.edit.setPlainText("Bonjour Bruno, ceci est un test chiffré 🔐")
    wa.chats.composer.btn_send.click()
    check(await until(lambda: wa.chats.composer.edit.toPlainText() == ""), "la zone de saisie est vidée après envoi")
    check(await until(lambda: any(i["type"] == "msg" and i["msg"]["body"].startswith("Bonjour Bruno") for i in wa.chats.view.model_.items)), "le message apparaît dans la vue d'Alice")
    mid = next(i["mid"] for i in wa.chats.view.model_.items if i["type"] == "msg")
    check(await until(lambda: wa.chats.view.model_.items[wa.chats.view.model_.row_of(mid)]["msg"]["status"] == "delivered"), "statut « distribué » reflété dans la vue")

    # réception côté Bruno : non lu, notification d'onglet, puis lecture
    check(await until(lambda: wb.nav[0].badge == 1), "pastille « non lus » sur l'onglet Messages de Bruno")
    wb.chats.refresh_list()
    check(any(c["unread"] == 1 for c in wb.chats.list.model_.items), "la liste de Bruno affiche 1 non lu")
    wb.activateWindow()
    wb.chats.open_conv(pa.identity.id)
    await eb.mark_read(pa.identity.id)
    check(await until(lambda: wa.chats.view.model_.items[wa.chats.view.model_.row_of(mid)]["msg"]["status"] == "read"), "accusé de lecture reçu par Alice")

    # réponse de Bruno, en direct dans la vue d'Alice (sans rechargement)
    wb.chats.composer.edit.setPlainText("Bien reçu !")
    wb.chats.composer.btn_send.click()
    check(await until(lambda: any(i["type"] == "msg" and i["msg"]["body"] == "Bien reçu !" for i in wa.chats.view.model_.items)), "la réponse arrive en direct chez Alice")

    # modification + suppression
    own = next(i["mid"] for i in wa.chats.view.model_.items if i["type"] == "msg" and i["out"])
    await ea.edit_message(own, "Bonjour Bruno (corrigé)")
    check(await until(lambda: any(i["type"] == "msg" and "corrigé" in i["msg"]["body"] for i in wb.chats.view.model_.items)), "la modification est visible chez Bruno")
    await ea.delete_message(own, for_everyone=True)
    check(await until(lambda: all(i.get("mid") != own for i in wb.chats.view.model_.items)), "la suppression pour tous disparaît chez Bruno")

    # fichier
    src = Path(tempfile.mkdtemp()) / "notes.txt"
    src.write_bytes(os.urandom(220_000))
    fmid = await ea.send_file(pb.identity.id, src)
    check(await until(lambda: eb.transfer(fmid) is not None and any(i.get("mid") == fmid for i in wb.chats.view.model_.items)), "l'offre de fichier apparaît chez Bruno")
    await eb.accept_file(fmid)
    check(await until(lambda: eb.transfer(fmid)["state"] == "done", 20), "le fichier est reçu et vérifié")
    check(Path(eb.transfer(fmid)["path"]).read_bytes() == src.read_bytes(), "contenu identique octet pour octet")

    # arrêt du serveur : message conservé puis remis
    await srv.stop()
    check(await until(lambda: not ea.relay.connected), "Alice détecte la coupure")
    wa.chats._update_banner()
    check(wa.chats.banner.isVisible() or "injoignable" in wa.chats.banner.text(), "bandeau « serveur injoignable » affiché")
    m2 = await ea.send_text(pb.identity.id, "écrit pendant la panne")
    check(ea.db.get_message(m2)["status"] == "pending", "statut « en attente » honnête pendant la panne")
    srv2 = RelayServer("127.0.0.1", srv.port)
    await srv2.start()
    check(await until(lambda: eb.db.get_message(m2) is not None, 25), "le message est remis après le retour du serveur")

    wa.grab().save(str(OUT / "smoke_alice.png"))
    wb.grab().save(str(OUT / "smoke_bruno.png"))
    for e in (ea, eb):
        await e.stop()
    await srv2.stop()


try:
    loop.run_until_complete(main())
except Exception:
    import traceback
    traceback.print_exc()
    failures.append("exception")
for w in app.topLevelWidgets():
    w.close()
print("\nRÉSULTAT :", "ÉCHEC " + str(failures) if failures else "TOUT EST OK")
sys.stdout.flush()
os._exit(1 if failures else 0)  # évite l'abandon de Qt à la destruction des threads de qasync à la sortie
