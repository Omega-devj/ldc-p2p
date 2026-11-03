# Migration depuis la v1.1

* **Rien n'est repris automatiquement** : le format de la v1 (invitations contenant pseudo+IP+port, messages en clair dans SQLite, identité `.pem`) est incompatible et ne mérite pas d'être conservé.
* **Données exposées** : l'archive v1 contenait `Alastor_data.db` (messages **non chiffrés**) et `Alastor_identity.pem` (clé privée protégée par mot de passe). Si cette archive a été partagée publiquement, considérez ces contenus comme exposés : ne réutilisez pas la clé, changez les mots de passe réutilisés, prévenez vos correspondants.
* Si vous voulez conserver l'historique de la v1, exportez-le depuis l'ancienne base avant de la supprimer ; la v2 repart d'une base propre.
* Le nom de la v1 (« Alastor », dans les noms de fichiers) n'est plus utilisé.
