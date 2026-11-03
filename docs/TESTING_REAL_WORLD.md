# Validation sur le terrain (à faire sur de vrais PC)

Les tests automatiques couvrent le moteur, le réseau en loopback et l'interface hors écran. **Ce qui suit n'a pas pu être exécuté** et doit l'être avant de considérer la v2.0 comme fiable.

## Préparation
- Un VPS avec un nom de domaine et un certificat TLS ; lancer le relais (`deploy/`), ouvrir le port 9443.
- Deux PC Windows 10/11, **sur deux réseaux différents** (box A et partage de connexion 4G, par exemple).

## Checklist
- [ ] Installation / désinstallation (données conservées sauf demande) ; lancement depuis le menu Démarrer.
- [ ] Création d'identité, code de récupération noté ; restauration sur le 2ᵉ PC avec le code.
- [ ] Connexion TLS au relais (indicateur vert) ; diagnostic réseau lisible.
- [ ] Échange des codes d'invitation → demande → acceptation (les deux sens).
- [ ] Message A→B et B→A ; accusés ✓ ✓✓ lu ; « écrit… » ; présence (changer de statut).
- [ ] B éteint 10 min : A écrit ; B rallume → réception ; A voit « distribué ».
- [ ] Coupure Wi-Fi pendant l'envoi → « en attente » honnête, puis envoi au retour du réseau.
- [ ] Redémarrer l'app pendant un envoi en attente → le message part quand même.
- [ ] Fichier 50 Mo ; couper le réseau à 50 % ; reprise ; empreinte vérifiée.
- [ ] Groupe à 3 (3ᵉ PC/VM) ; retrait d'un membre ; message éphémère ; suppression pour tous.
- [ ] Notifications Windows (sans contenu par défaut), zone de notification, « ne pas déranger ».
- [ ] Même réseau local : lien direct établi (menu Diagnostic) ; relais coupé → la conversation continue.
- [ ] Mise à l'échelle écran 125 % / 150 %, thème sombre système, plusieurs moniteurs.
- [ ] Antivirus / SmartScreen : comportement de l'installateur non signé.
- [ ] (Optionnel) Flou Windows 11 : activer `backdrop` dans les réglages et vérifier le rendu.
