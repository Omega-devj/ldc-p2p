"""Paramètres du moteur, persistés dans la base du profil."""
from __future__ import annotations

from dataclasses import dataclass, fields

from ..storage.database import Database

STATUSES = ("online", "away", "dnd", "invisible")


@dataclass
class EngineSettings:
    server_host: str = ""
    server_port: int = 9443
    server_tls: bool = False
    direct_enabled: bool = True
    listen_port: int = 0
    read_receipts: bool = True
    typing_indicators: bool = True
    notifications: bool = True
    notification_previews: bool = False   # par défaut : aucun contenu dans les notifications
    status: str = "online"
    download_dir: str = ""
    auto_accept_mb: int = 0               # 0 = toujours demander
    update_url: str = ""                 # base HTTPS des manifestes de mise à jour (vide = désactivé)
    close_to_tray: bool = False
    backdrop: bool = False                # flou Mica/Acrylique Windows 11 (expérimental)

    @classmethod
    def load(cls, db: Database) -> "EngineSettings":
        raw = db.all_settings()
        s = cls()
        for f in fields(cls):
            if f.name in raw:
                cur = getattr(s, f.name)
                v = raw[f.name]
                try:
                    setattr(s, f.name, (v == "1") if isinstance(cur, bool) else int(v) if isinstance(cur, int) else v)
                except ValueError:
                    pass
        if s.status not in STATUSES:
            s.status = "online"
        return s

    def save(self, db: Database) -> None:
        for f in fields(self):
            v = getattr(self, f.name)
            db.set_setting(f.name, ("1" if v else "0") if isinstance(v, bool) else str(v))
