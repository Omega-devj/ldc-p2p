# Périmètre pour un audit de sécurité indépendant

Un audit ne peut être réalisé que par des tiers humains : voici le dossier à leur remettre.

**Cible** : LDC P2P 2.x (client Windows + serveur relais). **Modèle de menace** : `SECURITY.md`.

**Priorités** : (1) `crypto/` (identité, coffre, enveloppes, cartes) ; (2) `network/` + `server/` (authentification, quotas, isolation, mailbox) ; (3) `engine/` (autorisations : qui peut écrire/éditer/supprimer/réagir ; groupes ; fichiers) ; (4) `storage/` (chiffrement au repos, migrations) ; (5) `updater.py` et workflow de release (chaîne d'approvisionnement) ; (6) UI : rendu de contenu distant (HTML, liens, images, noms de fichiers).

**Hors périmètre v2.0** : confidentialité persistante, appels, multi-appareils.

**Livrables attendus** : rapport avec gravité, preuves de concept, correctifs recommandés ; re-test après correction.
