# Appels vocaux et vidéo

Appels **1 à 1** avec les contacts acceptés, chiffrés de bout en bout (WebRTC : DTLS-SRTP, Opus, VP8).
La signalisation (offre/réponse SDP) voyage dans des **enveloppes signées et chiffrées** : l'empreinte DTLS est donc authentifiée par l'identité du contact — pas d'attaque de l'homme du milieu sur les médias. Le serveur LDC ne voit jamais le SDP ni les adresses.

## Trois façons de connecter un appel
| Mode | Serveurs utilisés | Fonctionne quand… |
|---|---|---|
| **Sans serveur** (*Paramètres › Appels › Sans serveur*) | **Aucun** (ni LDC, ni STUN, ni TURN) | Les deux personnes se joignent directement : même réseau local (découverte automatique), VPN (Tailscale, ZeroTier), IPv6, port redirigé. Au besoin, *Détails › Connexion directe manuelle* pour saisir l'adresse. |
| **Automatique + STUN** | Relais LDC (signalisation chiffrée) + STUN (adresse publique) | La plupart des box domestiques (NAT « simple »). |
| **Automatique + TURN** | + votre serveur TURN | Réseaux stricts (entreprises, CGNAT, 4G) : les médias (toujours chiffrés) passent par TURN. Voir `deploy/coturn/`. |

Option **Masquer mon adresse IP** : seuls les candidats « relais » sont annoncés (TURN obligatoire).

## Utilisation
Boutons téléphone/caméra dans l'en-tête d'une conversation (actifs si le contact est en ligne). Appel entrant : pop-up toujours visible + sonnerie. Pendant l'appel : micro, caméra, raccrocher, minuteur, qualité de connexion. L'historique (« Appel terminé · 05:12 », « Appel manqué ») apparaît dans la conversation. Mode « Ne pas déranger » : appels refusés automatiquement (et signalés comme manqués). Limite : 3 sonneries par minute et par contact.

## Composants
Les appels demandent `aiortc`, `sounddevice` et `opencv-python-headless` (~150 Mo) : `requirements-calls.txt`. L'application propose de les installer à la demande (*Paramètres › Appels › Installer*). Sans eux, tout le reste fonctionne.

## Limites connues (v2.1)
* **Pas d'annulation d'écho** : utilisez un casque. (Un module AEC est prévu.)
* Appels 1 à 1 uniquement (pas d'appels de groupe).
* Débit vidéo fixe (640×480, ~30 i/s) : pas d'adaptation fine ni de partage d'écran.
* Testé ici avec des médias synthétiques en boucle locale (audio dans les deux sens, vidéo reçue conforme, mode sans serveur). **Non testé avec un vrai micro/caméra ni sous Windows, ni via Internet/TURN** : à valider (`docs/TESTING_REAL_WORLD.md`).
* Le SDP contient les adresses des candidats : elles sont visibles **de votre contact** (pas du serveur LDC) ; utilisez « Masquer mon adresse IP » si c'est un problème.
