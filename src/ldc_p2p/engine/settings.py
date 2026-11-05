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
    update_url: str = "https://github.com/Omega-devj/ldc-p2p/releases/latest/download"   # manifestes signés
    auto_update: int = 1                  # 0 = jamais, 1 = prévenir, 2 = installer automatiquement
    server_token: str = ""               # jeton d'accès si le serveur est privé
    lock_minutes: int = 0                 # verrouillage automatique (0 = jamais)
    auto_away_minutes: int = 5            # « Absent » automatique (0 = jamais)
    sound: bool = True
    sound_name: str = "doux"
    capture_protection: bool = False      # exclut la fenêtre des captures d'écran (Windows)
    calls_enabled: bool = True
    call_mode: str = "auto"               # "auto" : STUN/TURN si configurés ; "local" : AUCUN serveur (réseau local uniquement)
    stun_servers: str = "stun:stun.l.google.com:19302"
    turn_url: str = ""
    turn_user: str = ""
    turn_pass: str = ""
    hide_ip: bool = False                 # n'annonce que des candidats relais (TURN obligatoire)
    audio_in: str = ""
    audio_out: str = ""
    camera: str = ""
    lan_discovery: bool = True            # trouve les contacts du réseau local sans aucun serveur
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
