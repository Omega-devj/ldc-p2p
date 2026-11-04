# Conception : confidentialité persistante (Double Ratchet)

**Statut : non implémenté volontairement.** Un double ratchet maison, écrit sans relecture par un cryptographe, est plus dangereux qu'utile. Cette note fixe la conception à faire valider.

## Objectif
Si une clé privée d'identité fuit, les messages passés interceptés restent illisibles (FS) et les futurs se « soignent » (post-compromise security).

## Conception proposée
* **Établissement** : X3DH simplifié — pré-clés signées de moyen terme (SPK) et pré-clés à usage unique (OPK) publiées sur le relais (chiffrées/signées par l'identité), car les contacts peuvent être hors ligne.
* **Ratchet** : Double Ratchet (Signal) avec X25519, HKDF-SHA256, ChaCha20-Poly1305, en-têtes chiffrés.
* **Contraintes propres à LDC** : renvois idempotents (l'état n'avance qu'au premier déchiffrement réussi), messages hors ordre (clés sautées bornées), groupes (chiffrement pair à pair → un ratchet par paire), reprise après restauration (nouvelle session + alerte « code de sécurité modifié »).
* **Migration** : champ `v` d'enveloppe (v2 = ratchet) ; les v1 restent lisibles pendant la transition.
* **Bibliothèque** : privilégier une implémentation éprouvée (libsignal via binding, ou Noise) plutôt que du code maison.

## Prérequis avant de coder
Revue de cette conception par un cryptographe, vecteurs de test officiels, tests de désynchronisation.
