# Mises à jour automatiques (GitHub)

Dépôt : https://github.com/Omega-devj/ldc-p2p

## Principe
1. Chaque **push sur `main`** déclenche `.github/workflows/release.yml` : tests → installateur Windows → archive des sources → **manifeste signé** → publication d'une release `v2.0.<n°>`.
2. L'application interroge `releases/latest/download/manifest.json` au démarrage puis toutes les 6 h.
3. Si la version est plus récente : bandeau « Nouvelle version disponible » (ou installation automatique selon *Paramètres › Mises à jour*).
4. **Installation depuis les sources** (`LANCER_LDC_P2P.bat`) : l'archive est vérifiée (signature Ed25519 du manifeste + SHA-256), `src/` est remplacé (sauvegarde `.update-backup-*`, 2 conservées), les dépendances sont mises à jour, l'application redémarre.
5. **Installateur Windows** : l'installateur signé/vérifié est téléchargé puis lancé.

## Mise en place (une seule fois)
1. Dépôt GitHub → *Settings › Secrets and variables › Actions › New repository secret*
   * Nom : `RELEASE_SIGNING_KEY` ; valeur : le contenu du fichier `RELEASE_SIGNING_KEY.txt` fourni (clé privée de publication). **Supprimez ensuite ce fichier de votre ordinateur** ou conservez-le dans un gestionnaire de mots de passe.
2. *Settings › Actions › General › Workflow permissions* → « Read and write permissions ».
3. La clé publique correspondante est déjà dans `src/ldc_p2p/release_key.py`. Pour **changer de clé** : `python tools/release_sign.py keygen nouvelle.key`, remplacer la clé publique dans `release_key.py`, mettre la nouvelle clé privée dans le secret.
4. Le dépôt (ou au moins ses *Releases*) doit être **public** : l'application télécharge sans authentification.

## Sécurité de ce canal (à lire)
* Sans la clé privée, personne ne peut publier une mise à jour acceptée par les applications.
* **Toute personne ayant le droit d'écrire sur `main`** peut cependant modifier le workflow et faire signer du code malveillant. Protégez le dépôt : authentification à deux facteurs, protection de branche (revue obligatoire), « environment » avec approbation pour la publication si vous collaborez.
* Chaque push de documentation seule (`docs/`, `*.md`) ne publie pas de version.
* Pour désactiver : *Paramètres › Mises à jour › Jamais*.
