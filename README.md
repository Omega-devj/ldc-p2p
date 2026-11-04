# LDC P2P 2.0 (préversion)

Messagerie **pair à pair hybride**, **chiffrée de bout en bout**, pour Windows 10/11 — reconstruite de zéro à partir de l'audit de la version 1.1.

> **État honnête** : le cœur (identité, chiffrement, messagerie fiable, groupes, fichiers, présence, interface) est écrit et testé automatiquement (tests unitaires, intégration réseau, test de fumée de l'interface). **Les appels vocaux/vidéo et la synchronisation multi-appareils ne sont pas encore développés.** Le build Windows (installateur) et l'usage sur deux vrais réseaux Internet n'ont pas pu être testés dans l'environnement de développement : voir `docs/TESTING_REAL_WORLD.md` (liste de vérifications à faire).

## Ce que fait la v2.0

| Fonction | État |
|---|---|
| Identité cryptographique (ID LDC auto-certifié, récupération, coffre protégé par mot de passe) | ✅ testé |
| Messages privés chiffrés de bout en bout (NaCl + signatures Ed25519) | ✅ testé |
| Contacts : demande / acceptation / blocage / vérification par numéro de sécurité | ✅ testé |
| Fiabilité : message enregistré avant envoi, file d'attente persistante, reprise, anti-doublon, statuts honnêtes (en attente → envoyé → distribué → lu → échec/réessayer) | ✅ testé |
| Hors ligne : boîte aux lettres chiffrée sur le relais (TTL 14 j), remise à la reconnexion | ✅ testé |
| Connexion directe pair à pair (LAN, IPv6, port redirigé) + relais de secours | ✅ testé en local (loopback) |
| Groupes privés (chiffrés individuellement par membre, propriétaire = autorité) | ✅ testé |
| Fichiers jusqu'à 100 Mo : consentement, blocs, reprise, SHA-256 | ✅ testé |
| Présence (en ligne / absent / ne pas déranger / invisible), « écrit… », accusés de lecture désactivables | ✅ testé |
| Messages éphémères, modification, suppression pour tous, recherche | ✅ testé |
| Notifications Windows (sans contenu par défaut), zone de notification | ⚠️ écrit, non testé sous Windows |
| Sauvegarde chiffrée des contacts/groupes, mises à jour signées | ✅ testé (mises à jour : désactivées tant qu'aucune clé de publication n'est intégrée) |
| TLS vers le relais | ✅ testé |
| Interface « Liquid Glass » (fond aurore, panneaux translucides, icônes SVG) | ✅ rendu vérifié hors écran ; flou Windows 11 optionnel non testé |
| Réactions, @mentions dans les groupes, infos de remise par membre, séparateur « Nouveaux messages », texte sélectionnable, miniatures d'images, glisser-déposer et collage d'images | ✅ |
| Verrouillage automatique, « Absent » automatique, protection contre les captures d'écran (Windows), sons, démarrage automatique, liens `ldc://`, QR code d'invitation | ✅ écrit (parties Windows non testées) |
| Thème clair/sombre, 6 couleurs d'accent, raccourcis clavier (Ctrl+K, Ctrl+N, Ctrl+L, Alt+↑/↓, Échap) | ✅ |
| Serveur privé (jeton, liste d'invités, liste d'adresses bloquées), page de statut sans donnée personnelle, image Docker | ✅ testé |
| **Mises à jour automatiques à chaque modification du dépôt GitHub** (releases signées) | ✅ écrit — voir `docs/UPDATES.md` |
| **Appels vocaux / vidéo** | ❌ non développé (voir `docs/ROADMAP.md`) |
| **Synchronisation multi-appareils** | ❌ non développé (une seule session active par identité) |
| Confidentialité persistante (forward secrecy / double ratchet) | ❌ conception écrite : `docs/FORWARD_SECRECY_DESIGN.md` |
| Audit de sécurité indépendant | ❌ impossible à automatiser : périmètre prêt dans `docs/AUDIT_SCOPE.md` |

## Démarrage rapide (développement)

```bash
pip install -r requirements.txt pytest pytest-asyncio
python -m pytest -q                       # 100+ tests
QT_QPA_PLATFORM=offscreen python tests/ui_smoke.py   # parcours complet de l'interface

# Lancer l'application avec un profil de démonstration (hors ligne, aucune donnée réelle)
PYTHONPATH=src python -m ldc_p2p --demo

# Lancer un serveur de rendez-vous/relais local pour tester à deux
PYTHONPATH=src python -m ldc_p2p.server --host 127.0.0.1 --port 9443
```

**Windows, le plus simple** : double-cliquez `LANCER_LDC_P2P.bat` (Python 3.11+ requis). Pour tester à deux sur un même réseau : `LANCER_SERVEUR.bat` sur un PC, puis `Paramètres › Réseau` → adresse du PC, port 9443, TLS désactivé.

Pour discuter entre deux ordinateurs : héberger le serveur (`deploy/`, TLS recommandé), le renseigner dans *Paramètres › Réseau*, puis échanger vos **codes d'invitation**.

## Architecture en une phrase

`ui/` (PySide6) → `engine/` (asyncio, sans Qt) → `network/` + `storage/` + `crypto/`, plus `server/` (relais). Détails : `docs/ARCHITECTURE.md`. Sécurité et limites : `SECURITY.md`.

## Structure

```
src/ldc_p2p/
  crypto/    identité, coffre de clés, enveloppes E2E, chiffrement au repos
  storage/   SQLite : migrations, contenus chiffrés, boîte d'envoi
  network/   cadrage TCP, client relais, liens directs authentifiés
  server/    serveur de rendez-vous + présence + relais + boîte aux lettres
  engine/    contacts, messagerie fiable, groupes, fichiers, présence
  ui/        thème Liquid Glass, composants, pages, dialogues
  profile.py profils locaux, sauvegarde ; updater.py mises à jour signées
tests/       tests unitaires/intégration + ui_smoke.py
packaging/   PyInstaller, Inno Setup, build_windows.ps1
deploy/      Dockerfile, docker-compose, unité systemd du relais
```
