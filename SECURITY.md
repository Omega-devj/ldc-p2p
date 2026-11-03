# Sécurité de LDC P2P 2.0

> **Préversion non auditée.** N'y confiez pas de secrets critiques avant un audit indépendant.

## Ce qui est protégé

| Menace | Protection |
|---|---|
| Lecture des messages par le serveur ou le réseau | Chiffrement de bout en bout : NaCl `crypto_box` (X25519 + XSalsa20-Poly1305). Le relais ne voit que des octets chiffrés. |
| Usurpation d'identité | L'ID LDC est le hash des clés publiques (auto-certifié). Le destinataire vérifie la **signature Ed25519** avec la clé **épinglée** du contact : une clé fournie dans un paquet n'est jamais crue. Testé (usurpation d'ID, clé étrangère, carte falsifiée). |
| Falsification / redirection d'un message | La signature couvre version, id, expéditeur, destinataire, horodatage, type, nonce et chiffré. |
| Faux contact via un code d'invitation | Carte auto-signée + ID = hash des clés. Le numéro de sécurité (30 chiffres) permet la vérification de vive voix. |
| Vol du fichier de clés | Graine chiffrée par scrypt (n=2¹⁷) + ChaCha20-Poly1305. Mot de passe de 8 caractères minimum. |
| Lecture de la base locale | Corps des messages et métadonnées de fichiers chiffrés (ChaCha20-Poly1305, clé dérivée de l'identité). |
| Rejeu / doublons | Identifiant unique par message, table de déduplication, ré-acquittement idempotent. |
| Spam / abus du relais | Authentification par signature, limite de débit, quotas de boîte aux lettres, limites de connexions, taille de paquet bornée avant allocation. |
| Contenu malveillant à l'affichage | Texte distant échappé (seules des ancres http(s) sont créées) ; confirmation avant d'ouvrir un lien ; noms de fichiers assainis ; exécutables jamais lancés automatiquement. |
| Écriture par un inconnu/bloqué | Rejeté avant tout traitement ; aucune notification d'échec envoyée à l'expéditeur. |

## Ce qui n'est PAS protégé (limites assumées)

1. **Pas de confidentialité persistante** : si une clé privée fuit, les messages interceptés dans le passé peuvent être lus. Un double ratchet est prévu (`docs/ROADMAP.md`).
2. **Métadonnées visibles du relais** : identifiants LDC, adresses IP, qui écrit à qui, horaires, tailles (arrondies par blocs de 256 octets), liste de contacts surveillés pour la présence. Utilisez TLS et hébergez votre propre relais pour en limiter l'exposition.
3. **Base locale partiellement en clair** : identifiants, horodatages, statuts et noms de contacts ne sont pas chiffrés (SQLCipher prévu).
4. **Pas d'anti-malware, pas de protection si l'ordinateur est compromis** (enregistreur de frappe, etc.).
5. **Le propriétaire d'un groupe fait autorité** sur ses membres ; un groupe n'a pas d'administration partagée.
6. **Une seule session active par identité** (la seconde connexion remplace la première).
7. Les suppressions « pour tous » et les messages éphémères sont une demande : un correspondant peut copier ou capturer avant.
8. **Non audité** : aucune revue de sécurité externe n'a été réalisée.

## Données personnelles

L'archive de la v1 contenait des bases `*.db` et des fichiers `*.pem` : voir `docs/MIGRATION_FROM_V1.md`. `.gitignore` et `build_windows.ps1` bloquent désormais ces fichiers. Les journaux ne contiennent ni contenu de message ni clé.

## Signaler une vulnérabilité

Ouvrez un signalement privé auprès du mainteneur plutôt qu'un ticket public.
