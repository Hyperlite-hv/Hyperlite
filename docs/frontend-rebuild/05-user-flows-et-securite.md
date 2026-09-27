# 05 — Parcours utilisateurs et sécurité

> Phase 1 — documentation seulement.

## 1. Modèle de sécurité de l'interface

| Sujet | Constat | Règle pour la reconstruction |
|---|---|---|
| Authentification | Jeton JWT (4 h, pas de rafraîchissement) ou jeton API `hlt_…` ; 2FA TOTP en deux étapes ; SSO OIDC (contourne le TOTP, jeton renvoyé dans l'URL) ; verrouillage par IP source | Même flux ; gestion d'expiration avec avertissement 5 min avant et reconnexion **sans perte de saisie** ; jeton SSO retiré de l'URL immédiatement (déjà fait) |
| Stockage du jeton | `localStorage` (`hyperlite_token`) lisible par tout script de la page | Conservé (contrat existant) ; CSP stricte proposée ; `hyperlite_username`/`role` jamais relus : supprimés |
| Déconnexion | Locale seulement (le jeton reste valide 4 h côté serveur) | Documenté ; « Sign out everywhere » nécessite le backend (A-15) |
| Rôles | Front : `admin` seulement ; backend : global (`admin`/`observateur`) + ACL (`lecteur`, `operateur`, `gestionnaire`, personnalisés) par utilisateur/groupe/pool | Table de capacités unique côté front (fonction pure, testée) ; le backend reste l'autorité |
| Listes non filtrées | Tout utilisateur voit toutes les VM/tâches/sauvegardes (B2) | L'interface ne suggère jamais que la visibilité est restreinte |
| Terminal VM | Le ticket SSH n'exige que `vm.console` (B1) | **À corriger côté backend** ; en attendant, bouton visible avec libellé « Root shell in the guest » |
| Secrets | Mot de passe de VM, secret SSO, jeton API (affiché une fois), URL de webhook (renvoyée en clair aux admins) | Jamais dans les logs ni les toasts ; champs de type `password` ; « Copier » avec avertissement ; masque par défaut |
| XSS | `dangerouslySetInnerHTML` du QR SVG de 2FA (`AccountSecurityModal.jsx:180`) | Remplacer par un rendu sûr (image `data:` ou composant QR) ; aucune autre injection HTML |
| Tickets | WebSocket : ticket à usage unique, 30 s, en mémoire | Ne jamais mettre le ticket dans l'historique ni les logs ; nouveau ticket à chaque connexion |
| Actions destructrices | Voir `00` §11 | Gabarit unique de confirmation (impact + saisie du nom pour l'irréversible) |
| Confirmations serveur | Le client ajoute `confirm=true` pour 8 suppressions | Conservé, mais **la confirmation utilisateur est réelle** et précède toujours l'appel |

## 2. Les 18 parcours


### Parcours 1 — Rechercher une VM ou un nœud depuis l'Inventory Explorer

| Élément | Description |
|---|---|
| Point de départ | N'importe quel écran : focus sur `/` ou `Ctrl/Cmd+K`, ou clic dans le champ « Find a node or VM ». |
| Étapes | 1 Taper 2–3 lettres · 2 l'arbre se filtre en gardant le chemin des parents, la correspondance est surlignée, un compteur annonce « 3 results » · 3 `↑↓` pour parcourir, `Entrée` pour ouvrir la sélection (ou le premier résultat) · 4 `Échap` vide la recherche et restaure l'arbre. |
| Informations présentées | Nom, IP, OS, état, nœud et chemin hiérarchique de chaque résultat ; état de fraîcheur si des données sont périmées. |
| Actions | Filtrer, ouvrir, effacer ; depuis la palette : exécuter une action autorisée. |
| Validations | Aucun appel réseau à la frappe ; recherche limitée à ce qui est chargé (indication « 2 nodes still loading »). |
| Permissions | Tout utilisateur connecté (les listes ne sont pas filtrées par ACL côté backend : l'interface ne prétend pas masquer). |
| Risques | Faible. Résultat ambigu si deux VM portent le même nom sur deux nœuds : le nœud est toujours affiché. |
| Confirmations | Aucune. |
| Résultat attendu | La ressource s'ouvre dans le workspace, l'URL devient `/vms/<nœud>/<nom>` (lien copiable). |
| Erreurs possibles | Aucun résultat (message + « Clear search ») ; données pas encore chargées. |
| Liens vers logs / tâches | Aucun (lecture). |
| Fonctions existantes préservées | Recherche d'en-tête existante (filtre de l'arbre) conservée et étendue ; matrice SHL « Inventory Explorer ». |

### Parcours 2 — Naviguer dans Server et Pool

| Élément | Description |
|---|---|
| Point de départ | Explorer, commutateur `[Server \| Pool]`. |
| Étapes | 1 Mode Server : Datacenter › nœuds › catégories (VM, conteneurs, stockage, réseaux) · 2 basculer en Pool : Datacenter › pools de ressources › membres, groupe « Unassigned » · 3 le dépliage, la sélection et le filtre sont conservés au changement de mode quand la ressource existe dans les deux. |
| Informations présentées | Compteurs d'enfants, état agrégé, nœud de chaque VM (mode Pool), nombre de membres. |
| Actions | Déplier/replier, sélectionner, changer de mode, options d'affichage. |
| Validations | Décision D-01 (sens du mode Pool) à valider avant implémentation. |
| Permissions | Lecture : tous ; création/édition des pools : admin (Security). |
| Risques | Faible ; le mode Pool actuel affiche de fausses appartenances (doublons) : corrigé. |
| Confirmations | Aucune. |
| Résultat attendu | L'utilisateur retrouve n'importe quelle ressource par l'une ou l'autre hiérarchie, sans perdre sa sélection. |
| Erreurs possibles | Pool vide, pool supprimé pendant la navigation (la sélection se replie sur le parent), API `/pools` indisponible (mode Pool désactivé avec explication). |
| Liens vers logs / tâches | Aucun. |
| Fonctions existantes préservées | Modes Server/Pool, sélection Datacenter/nœud/VM/pool, dépliage par défaut profondeur < 2, `Ctrl/Cmd+B`. |

### Parcours 3 — Identifier une VM en erreur

| Élément | Description |
|---|---|
| Point de départ | Overview (« Needs attention »), pastille agrégée d'un parent dans l'Explorer, ou liste des VM triée « problèmes d'abord ». |
| Étapes | 1 Repérer ◆ ou ▲ (forme + texte) · 2 ouvrir la VM · 3 lire le bandeau de cause (dernière tâche en échec, état `plante`/`bloque`) · 4 choisir : consulter les logs, démarrer, arrêt forcé. |
| Informations présentées | État, dernière tâche et son erreur, uptime, nœud, changements récents. |
| Actions | Ouvrir la tâche, ouvrir les logs, démarrer, arrêter (forcé après confirmation). |
| Validations | États dérivés des valeurs de fil (`plante`, `bloque`, `suspendu`, `inconnu`) ; jamais d'état inventé. |
| Permissions | Lecture : tous ; actions : `vm.power` (ACL) ou admin. |
| Risques | Diagnostic erroné si les données sont périmées : la fraîcheur est affichée. |
| Confirmations | Force stop : confirmation renforcée. |
| Résultat attendu | La cause probable est visible en ≤ 2 clics. |
| Erreurs possibles | Tâche introuvable (`en_cours` orpheline), 403 sur le journal. |
| Liens vers logs / tâches | Lien direct vers la tâche et vers le journal filtré sur la VM. |
| Fonctions existantes préservées | États VM existants, tâches liées. |

### Parcours 4 — Lire les logs et tâches associés

| Élément | Description |
|---|---|
| Point de départ | Onglet « Tasks & Logs » de la ressource, ou dock, ou Activity. |
| Étapes | 1 Filtrer par statut/type/période · 2 ouvrir une tâche · 3 lire l'erreur complète, copier · 4 basculer sur le journal filtré sur la ressource · 5 suivre en direct (option). |
| Informations présentées | Type, cible, nœud, utilisateur, début, durée, progression (déterminée ou « indéterminée »), erreur, 20 dernières lignes d'audit. |
| Actions | Filtrer, trier, copier, exporter CSV, ouvrir la ressource liée. |
| Validations | Plage de dates cohérente ; limite ≤ 1 000 lignes. |
| Permissions | Tâches : tous ; journal d'audit : admin (message explicite sinon). |
| Risques | Les GET écrivent des lignes d'audit : cadence d'interrogation raisonnable (≥ 8 s), pause en onglet caché. |
| Confirmations | Aucune. |
| Résultat attendu | L'utilisateur comprend quoi, quand, par qui et pourquoi. |
| Erreurs possibles | 403 (journal), tâche « en cours » très ancienne, erreur non JSON. |
| Liens vers logs / tâches | C'est le flux lui-même. |
| Fonctions existantes préservées | Onglet Recent activity, Journal, cloche et task log de session. |

### Parcours 5 — Créer une VM

| Élément | Description |
|---|---|
| Point de départ | Menu **Create ▾ › Virtual machine** (ou palette, ou bouton de la liste). Admin. |
| Étapes | 1 Source (image cloud, ISO avec/sans installation automatique, import de disque) · 2 Identity · 3 Placement · 4 Compute · 5 Storage · 6 Network · 7 Advanced · 8 Review · 9 Create · 10 suivre la tâche. |
| Informations présentées | Bornes hôte (`/host/limits`), politique d'allocation, avertissements (Windows), résumé fidèle de tout ce qui sera envoyé. |
| Actions | Choisir, saisir, envoyer un ISO, revenir, créer. |
| Validations | Nom `^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$` ; utilisateur `^[a-z_][a-z0-9_-]{0,31}$` ; mot de passe ≥ 4 ; auto-nettoyage 1–365 ; ISO ⊕ import ; pilotes ⇒ ISO ; unicité du nom ; erreurs 422/409 lisibles. |
| Permissions | Admin (`POST /vms`). |
| Risques | Double envoi (aujourd'hui non protégé) ; fermeture accidentelle ; mot de passe en clair dans l'état du formulaire (jamais journalisé ni réaffiché). |
| Confirmations | Annuler avec saisie non vide : confirmation « Discard draft? ». |
| Résultat attendu | VM créée, tâche `create_vm` (et `auto_install` si applicable) visible, provisioning suivi. |
| Erreurs possibles | Nom déjà pris (409), hors limites (422/400), pool inaccessible, ISO absent, échec du démarrage. |
| Liens vers logs / tâches | « View task » et « Open VM » à la fin ; échec : lien vers la tâche et le journal. |
| Fonctions existantes préservées | Tous les champs des 5 étapes actuelles ; assistant conteneur conservé (`Create ▾ › Container`). |

### Parcours 6 — Modifier une VM

| Élément | Description |
|---|---|
| Point de départ | VM › Hardware / Storage / Network / Settings. |
| Étapes | 1 Choisir l'onglet · 2 modifier (vCPU/mémoire, disques, interfaces, CD-ROM, limites, options, pare-feu) · 3 « Apply changes » · 4 lire le résultat (à chaud ou après redémarrage). |
| Informations présentées | Valeurs actuelles, bornes, mode d'application (hot-plug ou à froid), impact. |
| Actions | Appliquer, annuler (« Revert »). |
| Validations | Bornes de `/host/limits` et de la politique ; SATA non hot-plug ; nom de disque unique (bogue actuel : nom généré une fois). |
| Permissions | `vm.resize`, `vm.hardware` (ACL) ou admin. |
| Risques | Réduire la RAM à chaud ; détacher un disque système ; retirer l'interface active. |
| Confirmations | Détacher/retirer : confirmation ; le reste : « Apply ». |
| Résultat attendu | Changement appliqué, tâche/toast de succès, valeurs relues depuis l'API. |
| Erreurs possibles | 409/422 (limites), échec de hot-plug, VM verrouillée par une tâche. |
| Liens vers logs / tâches | Lien vers la tâche si le backend en crée une ; sinon journal. |
| Fonctions existantes préservées | Édition mémoire/vCPU, disques, interfaces, CD-ROM, limites, pare-feu. |

### Parcours 7 — Démarrer une VM

| Élément | Description |
|---|---|
| Point de départ | En-tête de la VM, menu contextuel de l'Explorer, ligne de tableau, palette. |
| Étapes | 1 Cliquer ▶ Start · 2 le bouton passe en chargement, la ligne « Starting… » apparaît · 3 l'état passe à Running (relecture de `etat` et `ip`). |
| Informations présentées | État, tâche `start_vm`, IP quand disponible. |
| Actions | Démarrer. |
| Validations | VM non démarrée (`etat ≠ actif`) ; nœud en ligne. |
| Permissions | `vm.power` ou admin ; `node` transmis pour un nœud distant. |
| Risques | Faible. |
| Confirmations | Aucune. |
| Résultat attendu | VM en marche, tâche `start_vm` ✓. |
| Erreurs possibles | Ressources insuffisantes, disque manquant, nœud hors ligne. |
| Liens vers logs / tâches | Tâche `start_vm` dans le dock. |
| Fonctions existantes préservées | Start (avec `node` distant), rafraîchissement de l'état. |

### Parcours 8 — Arrêter proprement une VM

| Élément | Description |
|---|---|
| Point de départ | En-tête › Stop ▾ › Shutdown (ACPI). |
| Étapes | 1 Choisir « Shutdown » · 2 confirmation courte · 3 attente jusqu'à `etat = arrete` ; relecture après 4 s (comportement actuel conservé) · 4 si la VM ne s'arrête pas : proposition « Force stop ». |
| Informations présentées | État, durée d'attente, alternative. |
| Actions | Shutdown, attendre, forcer. |
| Validations | VM en marche. |
| Permissions | `vm.power` ou admin. |
| Risques | Invité sans ACPI : n'obéit pas. |
| Confirmations | Confirmation courte (aujourd'hui aucune sur les cartes). |
| Résultat attendu | VM arrêtée. |
| Erreurs possibles | Délai dépassé, invité bloqué. |
| Liens vers logs / tâches | Tâche `stop_vm`. |
| Fonctions existantes préservées | Stop, relecture différée. |

### Parcours 9 — Redémarrer une VM

| Élément | Description |
|---|---|
| Point de départ | En-tête › Restart. |
| Étapes | 1 Cliquer Restart · 2 la boîte de dialogue explique le mode (**redémarrage brutal** aujourd'hui) · 3 confirmer · 4 suivre l'état. |
| Informations présentées | Mode, impact, état. |
| Actions | Confirmer/annuler ; choisir « graceful » **si D-04 validé**. |
| Validations | VM en marche. |
| Permissions | `vm.power` ou admin. |
| Risques | Le client envoie toujours `force=true` : équivalent d'un arrêt d'urgence suivi d'un démarrage. |
| Confirmations | Confirmation obligatoire nommant le mode. |
| Résultat attendu | VM redémarrée. |
| Erreurs possibles | Échec du redémarrage (VM restée arrêtée). |
| Liens vers logs / tâches | Tâche `restart_vm`. |
| Fonctions existantes préservées | Restart (comportement inchangé tant que D-04 n'est pas décidé). |

### Parcours 10 — Force stop avec confirmation renforcée

| Élément | Description |
|---|---|
| Point de départ | En-tête › Stop ▾ › Force stop ; menu contextuel. |
| Étapes | 1 Choisir « Force stop » · 2 la boîte propose d'abord l'arrêt propre · 3 texte d'impact (perte de données, contrôle de disque) · 4 **saisir le nom de la VM** · 5 bouton ambre actif · 6 exécuter. |
| Informations présentées | Nom, impact, alternative. |
| Actions | Annuler (focus par défaut), arrêt propre, forcer. |
| Validations | Nom saisi = nom de la VM. |
| Permissions | `vm.power` ou admin. |
| Risques | Corruption du système de fichiers invité. |
| Confirmations | Saisie du nom + bouton dédié. |
| Résultat attendu | VM arrêtée, tâche `force_stop_vm`. |
| Erreurs possibles | VM déjà arrêtée (409). |
| Liens vers logs / tâches | Tâche `force_stop_vm`. |
| Fonctions existantes préservées | Force stop et son `ConfirmDialog` actuel. |

### Parcours 11 — Ouvrir la console si disponible

| Élément | Description |
|---|---|
| Point de départ | VM › Console, bouton `>_` de l'en-tête, menu contextuel. |
| Étapes | 1 Choisir VNC ou terminal SSH · 2 le client demande un ticket à usage unique (30 s) · 3 la fenêtre `/console/<nom>` s'ouvre (repli « Open here » si pop-up bloqué) · 4 barre d'état : connecté / déconnecté ; reconnexion = nouveau ticket. |
| Informations présentées | Disponibilité (VM en marche, utilisateur SSH connu, privilège `vm.console`), état de connexion. |
| Actions | Ouvrir, plein écran, coller, déconnecter, reconnecter. |
| Validations | VM en marche ; ticket valide ; pour SSH : utilisateur défini. |
| Permissions | `vm.console` (ACL) — **attention constat B1** : tout observateur global l'a, y compris pour le terminal SSH. |
| Risques | Terminal racine dans l'invité ; ticket réutilisable impossible mais capturable pendant 30 s. |
| Confirmations | Aucune pour VNC ; avertissement pour SSH. |
| Résultat attendu | Console ouverte. |
| Erreurs possibles | Ticket expiré (4401), VM arrêtée, WebSocket coupé, 1011 (échec de connexion VNC). |
| Liens vers logs / tâches | Tâche `host_shell` pour le shell hôte ; sinon journal. |
| Fonctions existantes préservées | Consoles autonomes, tickets, protocole WebSocket inchangé. |

### Parcours 12 — Consulter ou créer un snapshot, backup, clone ou migration

| Élément | Description |
|---|---|
| Point de départ | VM › Snapshots (Snapshots \| Backups \| Clone & migrate). |
| Étapes | **Snapshot** : nom → créer (tâche indéterminée) → restaurer/supprimer avec confirmation · **Backup** : « Back up now » (chaud si en marche, froid sinon), planification UTC, rétention · **Clone** : formulaire (nom) · **Migration** : diagnostic de compatibilité → cible → confirmer → progression réelle. |
| Informations présentées | Mode chaud/froid, taille, somme SHA-256, fuseau UTC, progression, raisons d'incompatibilité. |
| Actions | Créer, restaurer, supprimer, planifier, cloner, migrer. |
| Validations | ZFS : disque seulement, restauration VM arrêtée ; migration : ≥ 1 autre nœud en ligne, pas de pool ZFS, cible ≠ source. |
| Permissions | `vm.snapshot` (snapshots, backups, export), `vm.clone`, admin (migration, restauration de sauvegarde). |
| Risques | Restauration écrase l'état ; migration à chaud ; rétention supprime d'anciennes sauvegardes. |
| Confirmations | Restauration, suppression, migration : confirmation avec impact. |
| Résultat attendu | Opération terminée, tâche ✓. |
| Erreurs possibles | Échecs asynchrones invisibles à l'appel (HTTP 202) : seul le journal les montre ; remote→remote non supporté. |
| Liens vers logs / tâches | `task_id` pour snapshots et migration ; `GET /tasks?type=&cible=` pour backup/restore/export/clone. |
| Fonctions existantes préservées | Tous les snapshots, backups, clone, migration, planification actuels. |

### Parcours 13 — Diagnostiquer un nœud saturé

| Élément | Description |
|---|---|
| Point de départ | Overview (KPI capacité) ou nœud › Summary. |
| Étapes | 1 Ouvrir le nœud · 2 lire CPU/RAM/disque et tendance 24 h (local ; distant : « not reported ») · 3 trier les VM du nœud par consommation · 4 ouvrir la plus gourmande · 5 décider : migrer, arrêter, redimensionner. |
| Informations présentées | Charge, seuils (≥ 90 % → alerte du backend), VM par consommation, tâches en cours, compatibilité. |
| Actions | Trier, ouvrir, migrer (avec diagnostic). |
| Validations | Seuil fixe du backend ; aucune mesure inventée. |
| Permissions | Lecture : tous. |
| Risques | Décision sur des données de nœud distant absentes. |
| Confirmations | Migration : confirmation. |
| Résultat attendu | Cause identifiée en ≤ 3 clics. |
| Erreurs possibles | Nœud distant sans métriques (message). |
| Liens vers logs / tâches | Journal `alert_seuil_depasse`. |
| Fonctions existantes préservées | Vues nœud et métriques hôte existantes. |

### Parcours 14 — Diagnostiquer un stockage presque plein

| Élément | Description |
|---|---|
| Point de départ | Alerte ▲ « pool tank 91 % », Overview, ou Infrastructure › Storage. |
| Étapes | 1 Ouvrir le pool · 2 lire capacité/alloué/disponible, volumes triés par taille, ISO, sauvegardes · 3 identifier ce qui consomme · 4 libérer (supprimer ISO/disque importable/sauvegarde, avec confirmation) ou étendre (hors interface). |
| Informations présentées | Capacité, allocation, disponible, volumes et usage par VM, type (ZFS local). |
| Actions | Trier, supprimer, envoyer. |
| Validations | Aucune suppression sans confirmation nommant l'impact. |
| Permissions | Écriture : admin. |
| Risques | Suppression irréversible. |
| Confirmations | Confirmation avec saisie (n°45). |
| Résultat attendu | Espace libéré, tendance visible. |
| Erreurs possibles | Pool en `degrade`/`inaccessible`. |
| Liens vers logs / tâches | Tâches de sauvegarde/export récentes. |
| Fonctions existantes préservées | Storage, ISO, volumes, disques importables. |

### Parcours 15 — Diagnostiquer un réseau dégradé

| Élément | Description |
|---|---|
| Point de départ | Networks › réseau ▲/■, ou VM › Network. |
| Étapes | 1 Ouvrir le réseau · 2 vérifier actif/autostart, pont, sous-réseau, baux DHCP · 3 vérifier le pare-feu · 4 vérifier les interfaces hôte (VLAN, état) · 5 corriger. |
| Informations présentées | État, pont, baux, règles, interfaces de l'hôte. |
| Actions | Éditer le pare-feu, supprimer/créer un réseau. |
| Validations | Pont valide, doublon refusé. |
| Permissions | Écriture : admin. |
| Risques | Une règle erronée coupe l'accès aux VM. |
| Confirmations | Enregistrement des règles avec aperçu ; suppression : confirmation. |
| Résultat attendu | Réseau rétabli. |
| Erreurs possibles | Absence de métriques réseau (documentée). |
| Liens vers logs / tâches | Journal d'audit. |
| Fonctions existantes préservées | Réseaux, baux, pare-feu. |

### Parcours 16 — Gérer utilisateurs, rôles et permissions

| Élément | Description |
|---|---|
| Point de départ | Security. |
| Étapes | 1 Users : créer, changer de rôle, réinitialiser · 2 Groups/Pools : créer, ajouter des membres · 3 Roles : créer un rôle personnalisé (sous-ensemble des privilèges) · 4 Assignments : sujet + rôle + portée · 5 « Effective rights » pour vérifier. |
| Informations présentées | Rôles, privilèges, portées, source d'authentification, 2FA. |
| Actions | Créer, éditer, retirer. |
| Validations | Unicité, mot de passe, existence (non vérifiée par le backend : l'interface vérifie). |
| Permissions | Admin. |
| Risques | Suppression d'un utilisateur : ses ACL et jetons subsistent (B11) ; ACL additives sans refus explicite. |
| Confirmations | Chaque suppression confirmée (l'existant l'est déjà). |
| Résultat attendu | Droits appliqués, aperçu correct. |
| Erreurs possibles | 403, doublon, sujet inexistant. |
| Liens vers logs / tâches | Journal d'audit. |
| Fonctions existantes préservées | Toutes les sections de Permissions, SSO, modale de sécurité du compte. |

### Parcours 17 — Réagir à une API indisponible, une perte de WebSocket ou un état offline

| Élément | Description |
|---|---|
| Point de départ | Bandeau global, console/terminal déconnecté. |
| Étapes | 1 Bandeau « Cannot reach the server » avec dernière mise à jour · 2 données grisées « stale », actions désactivées · 3 reconnexion automatique (2→30 s) · 4 console : « Disconnected — Reconnect » (nouveau ticket) · 5 rétablissement : toast, rafraîchissement, état des tâches relu. |
| Informations présentées | Dernière mise à jour, prochaine tentative, ressources concernées. |
| Actions | Réessayer maintenant, copier le diagnostic. |
| Validations | Détection : échec réseau ≠ 401 ≠ 5xx. |
| Permissions | Tous. |
| Risques | Agir sur des données périmées : actions désactivées. |
| Confirmations | Aucune. |
| Résultat attendu | L'utilisateur sait ce qui est fiable ou non. |
| Erreurs possibles | Session expirée pendant l'absence (401 → connexion, URL conservée), mise à jour de Hyperlite en cours (redémarrage du service). |
| Liens vers logs / tâches | Journal local du client (« Copy details »). |
| Fonctions existantes préservées | Gestion 401, bandeau d'erreur, `LoadingState` à 10 s. |

### Parcours 18 — Supprimer une VM, un disque ou une ressource critique

| Élément | Description |
|---|---|
| Point de départ | Zone dangereuse de l'onglet Settings/Summary, menu `⋯`, Storage, Data protection. |
| Étapes | 1 Choisir « Delete… » · 2 boîte : impact exact (disques, snapshots, sauvegardes non supprimées, HA) · 3 saisir le nom · 4 bouton rouge · 5 tâche `delete_vm` suivie. |
| Informations présentées | Nom, impact chiffré, dépendances (VM utilisant le volume). |
| Actions | Annuler (défaut) ou supprimer. |
| Validations | VM arrêtée ; nom saisi ; `confirm=true` ajouté par le client. |
| Permissions | Admin (suppression VM, pool, ISO, réseau…), `vm.hardware` pour détacher un disque. |
| Risques | Irréversible ; sauvegardes, planification, HA, métriques subsistent après suppression de VM. |
| Confirmations | Saisie du nom obligatoire. |
| Résultat attendu | Ressource supprimée, tâche ✓, entrée d'audit. |
| Erreurs possibles | Encore en marche (409), en cours d'utilisation, erreur du backend. |
| Liens vers logs / tâches | Tâche `delete_vm`, journal d'audit. |
| Fonctions existantes préservées | Toutes les suppressions actuelles et leurs confirmations (`destructive.spec`). |
