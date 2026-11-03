# Distribution

## Construire l'installateur (Windows)
```powershell
pwsh packaging/build_windows.ps1
```
Le script lance les tests, génère l'icône, compile avec PyInstaller, **refuse de continuer si la distribution contient une base, un `.pem` ou un coffre de clés**, puis compile `installer.iss` (Inno Setup 6) et écrit les empreintes SHA-256.
Installation par utilisateur (sans droits administrateur). À la désinstallation, les données utilisateur (`%APPDATA%\LDCP2P`) sont conservées sauf si l'utilisateur répond « Oui ».
Pour éviter les avertissements SmartScreen : signer `LDC-P2P.exe` et l'installateur avec un certificat de signature de code.

## Héberger le relais
```bash
docker compose -f deploy/docker-compose.yml up -d     # après avoir édité le domaine/les certificats
```
ou unité systemd `deploy/ldc-relay.service`. Ouvrir le port TCP 9443, utiliser un certificat Let's Encrypt. Sauvegarde inutile : la boîte aux lettres ne contient que des enveloppes chiffrées expirant sous 14 jours.

## Mises à jour signées
```bash
python tools/release_sign.py keygen release.key           # une fois ; garder release.key hors du dépôt
# placer la clé publique affichée dans src/ldc_p2p/release_key.py, puis compiler
python tools/release_sign.py sign release.key dist/LDC-P2P-Setup-2.1.0.exe 2.1.0 https://exemple.fr/ldc/LDC-P2P-Setup-2.1.0.exe "Notes"
# publier manifest.json, manifest.sig et l'installateur sur un hébergement HTTPS
```
L'application refuse tout manifeste mal signé et tout installateur dont l'empreinte diffère.
