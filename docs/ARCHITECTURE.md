# Architecture

```
┌──────────────────────── ui (PySide6, Liquid Glass) ────────────────────────┐
│ onboarding · main_window · pages(chats, contacts, settings) · dialogs      │
│ ChatView (modèle + délégué peint)   UiController (événements → signaux Qt) │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ appels async / événements
┌───────────────────────────────▼────────────────────────────────────────────┐
│ engine (asyncio pur, testable sans Qt)                                      │
│ ChatEngine : contacts, boîte d'envoi, réception, présence, maintenance      │
│ + GroupsMixin + FilesMixin      EventBus                                    │
└───────┬──────────────────┬───────────────────────────────┬─────────────────┘
        │                  │                               │
   storage/Database    network/RelayClient            network/DirectManager
   SQLite+migrations   (serveur, reconnexion)         (liens directs authentifiés)
   contenus chiffrés          │                               │
        │                     ▼                               ▼
   crypto/ (identité,    server/RelayServer  ◄── enveloppes chiffrées ──► pair
   enveloppes E2E,       rendez-vous, présence,
   chiffrement repos)    relais, boîte aux lettres
```

## Décisions clés (et pourquoi)

* **Le moteur ne dépend pas de Qt** : il est testé en intégration (serveur réel + deux moteurs) sans interface.
* **Une enveloppe = une unité de sécurité** : identique sur lien direct et relais ; le transport n'a pas à être de confiance.
* **Enregistrer avant d'envoyer** : un message est écrit en base et placé dans la boîte d'envoi (même transaction) avant tout accès réseau.
* **Traitement des messages entrants hors de la boucle de lecture** : le traitement envoie lui-même des accusés sur la même connexion ; le faire dans la boucle de lecture provoquait un interblocage (bug trouvé par les tests, corrigé).
* **« Vu » seulement après traitement réussi** : un échec de traitement n'acquitte pas et ne marque pas le message comme vu → nouvel essai, jamais de perte silencieuse.
* **Transports** : lien direct si disponible, sinon relais. Les candidats d'adresse sont échangés dans des signaux chiffrés (le serveur n'est jamais un annuaire d'IP).

## Protocole (v1)

Trames TCP : 4 octets de longueur (big-endian, max 3 Mio) + JSON UTF-8.

**Serveur** : `hello{challenge}` → `auth{id,sk,bk,sig,visible}` (signature du défi, ID = hash des clés) → `ready{observed_ip,queued}`. Puis `relay{to,env,ephemeral}` → `relay_ack{status: delivered|queued|offline|rejected}`, `deliver{env}` → `mailack{mid}`, `watch{ids}` ↔ `presence{states}`, `vis`, `ping/pong`. Une enveloppe stockée n'est supprimée qu'à `mailack`.

**Enveloppe** : `{v,id,from,to,ts,k:"m"|"r",n,ct,sig[,card]}`. `ct` = crypto_box(JSON interne rembourré à 256 octets). `sig` = Ed25519 sur le JSON canonique de tous les autres champs. `k:"r"` (requête de contact) embarque la carte auto-signée de l'expéditeur.

**Contenus internes** (`t`) : `msg` (texte, fichier, groupe), `ack`, `read`, `req`/`req_ok`, `typing`, `presence`, `cand`, `edit`, `del`, `ginv`/`gleave`, `fget`/`fack`/`fchunk`/`fdone`/`fno`.

**Lien direct** : poignée de main à deux défis, signatures croisées de (rôle, défis, deux identifiants) ; seuls les contacts acceptés non bloqués passent ; en cas de liens croisés, les deux côtés gardent celui ouvert par le plus petit identifiant.

## Base de données

Migrations versionnées (`PRAGMA user_version`) ; refus d'ouvrir une base plus récente que l'application. Tables : contacts, groups, messages, receipts (statut par destinataire), outbox, seen, conv_settings, settings, transfers.
