# LDC P2P 2.0

LDC P2P est une application de messagerie privée pour Windows 10 et 11. Elle permet d'échanger des messages et des fichiers directement entre utilisateurs, avec du chiffrement de bout en bout et un système de relais lorsque la connexion directe n'est pas possible.

Cette version a été reconstruite à partir de zéro après l'audit de la version 1.1.

> **État du projet :** les principales fonctionnalités sont développées et passent les tests automatisés. Les appels vocaux, les appels vidéo et la synchronisation entre plusieurs appareils ne sont pas encore disponibles.
>
> Le programme d'installation Windows et les connexions entre deux réseaux Internet différents restent à tester. Les vérifications restantes sont détaillées dans [`docs/TESTING_REAL_WORLD.md`](docs/TESTING_REAL_WORLD.md).

## Fonctionnalités

| Fonctionnalité | État |
|---|---|
| Création d'identité et récupération de compte | ✅ |
| Coffre de clés protégé par mot de passe | ✅ |
| Messages privés chiffrés de bout en bout | ✅ |
| Signatures Ed25519 | ✅ |
| Ajout, acceptation et blocage de contacts | ✅ |
| Vérification des contacts par numéro de sécurité | ✅ |
| File d'attente et reprise automatique des messages | ✅ |
| Statuts des messages (envoyé, distribué, lu, échec) | ✅ |
| Réception des messages hors ligne via un relais | ✅ |
| Connexion directe entre utilisateurs et relais de secours | ✅ |
| Groupes privés chiffrés | ✅ |
| Envoi de fichiers jusqu'à 100 Mo | ✅ |
| Reprise des transferts et vérification SHA-256 | ✅ |
| Statut en ligne, absent, invisible et ne pas déranger | ✅ |
| Indicateur de saisie et accusés de lecture désactivables | ✅ |
| Modification et suppression des messages | ✅ |
| Messages éphémères et recherche | ✅ |
| Notifications Windows et icône dans la zone de notification | ⚠️ À tester sous Windows |
| Sauvegarde chiffrée des contacts et groupes | ✅ |
| Vérification des mises à jour signées | ✅ Code présent, mises à jour désactivées |
| Connexion TLS au relais | ✅ |
| Interface PySide6 avec thème Liquid Glass | ✅ |
| Appels vocaux et vidéo | ❌ Pas encore développés |
| Synchronisation entre plusieurs appareils | ❌ Pas encore développée |
| Forward secrecy / Double Ratchet | ❌ Prévu |

Les connexions directes ont été testées en local (loopback). Cela ne garantit pas encore leur bon fonctionnement sur tous les réseaux Internet.

## Installation et lancement

### Depuis les sources

Python 3.11 ou une version plus récente est nécessaire.

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio