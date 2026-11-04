# Feuille de route

Ordre recommandé : on ne passe à l'étape suivante que lorsque la précédente est validée **sur de vrais réseaux** (`TESTING_REAL_WORLD.md`).

1. **Validation terrain v2.0** : deux PC Windows sur deux réseaux, relais TLS hébergé, batterie de tests manuels.
2. **Traversée NAT** : STUN/ICE (ou UDP hole punching), UPnP/NAT-PMP, pour de vraies connexions directes derrière box/CGNAT. Aujourd'hui : direct = LAN/IPv6/port redirigé ; sinon relais.
3. **Confidentialité persistante** (conception : `FORWARD_SECRECY_DESIGN.md`) : Double Ratchet (ou Noise + ratchet) par conversation, avec migration des conversations existantes.
4. **Appels vocaux** : WebRTC (aiortc ou libwebrtc) avec Opus, annulation d'écho, signalisation via les enveloppes chiffrées existantes (`signal`), TURN en secours. États d'appel (sonnerie, refus, occupé, raccroché), tests de gigue/perte.
5. **Appels vidéo** puis **appels de groupe** (SFU ou maillage limité).
6. **Synchronisation multi-appareils** : liaison d'appareils (clé d'appareil signée par l'identité), journal d'événements chiffré, résolution de conflits, historique transférable.
7. **Base chiffrée intégralement** (SQLCipher), recherche plein texte chiffrée.
8. **Groupes** : administrateurs multiples, fichiers de groupe, réactions.
9. **Installateur signé** (certificat de signature de code), mise à jour automatique en arrière-plan.
10. **Audit de sécurité indépendant** avant toute diffusion large.
