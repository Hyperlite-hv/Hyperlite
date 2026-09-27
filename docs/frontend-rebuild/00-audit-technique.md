# 00 — Audit technique du frontend existant

> Phase 0 — documentation seulement. Aucun fichier applicatif, dépendance, route, style, backend, test ou script n'a été modifié.
> Base auditée : branche `test`, commit `a33dae5` (refonte shadcn/ui de la PR #168 + support Windows #169/#171 déjà fusionnés).
> Le dépôt de travail est le clone de développement `hl-devhub:/root/hyperlite-review`, branche `docs/frontend-rebuild`.

## 1. Méthode et niveau de preuve

| Source | Ce qui a été fait | Fiabilité |
|---|---|---|
| Lecture intégrale du code | 6 agents d'audit indépendants (coquille + Inventory, Datacenter, VM + nœud, assistants + primitives + tests, client API, backend). Chaque fichier de son périmètre a été lu en entier. Résultat : ~1 100 lignes de faits (`audit/*.jsonl`) | Élevée (citations `fichier:ligne`) |
| Captures réelles | 30 captures de la base `test` sur une instance privée (ports isolés, pas la preview d'un autre contributeur) : desktop 1440×900 sombre + clair, laptop 1280×720, tablette 820×1180, mobile 390×844, onglets Datacenter, onglets VM, nœud, assistant, tiroir mobile, sonde clavier de l'arbre | Élevée pour ce qui est visible ; les états d'erreur rares n'ont pas été forcés |
| Détecteur mécanique (`impeccable detect`) | 0 constat sur les sources du dashboard | Élevée mais limitée (motifs visuels génériques seulement) |
| Cadres de conception | `impeccable` (mode Operate, audit en 5 dimensions, critique heuristique) et `ui-ux-pro-max` (règles UX, graphiques, typographie ; ses palettes noir/néon ont été écartées car contraires au brief) | Recommandations, jamais des ordres |
| Non fait (volontairement) | Aucun test utilisateur, aucun profilage de performance chiffré, aucune modification du code | À faire en phase 2 (mesures avant/après) |

Les points marqués *(non vérifié)* n'ont pas été exécutés et sont listés en section 14.

## 2. Stack technique

| Couche | Technologie (versions de `dashboard/package.json`) |
|---|---|
| Backend | FastAPI (Python), libvirt / QEMU-KVM, LXC, SQLite en mode WAL ; 38 fichiers de routeurs dans `app/routers/` (dont le paquet `app/routers/vms/`), 168 décorateurs de routes ; boucles de fond : collecteur de métriques, planificateur de sauvegardes, poller de nœuds, auto-nettoyage des VM, vérification de mise à jour, écrivain d'audit |
| Frontend | React 19.3, Vite 8.3, Tailwind CSS 4.3 (mode CSS-first, `components.json`), shadcn/ui (Radix `radix-ui` 1.6, `cmdk`, `sonner`, `lucide-react` 1.47), Zustand 5.0, Recharts 3.8, `react-router-dom` 7.18, `class-variance-authority`, `tw-animate-css`. Langage : JavaScript/JSX (pas de TypeScript côté application ; TypeScript 7 sert aux tests Playwright) |
| Tests | Vitest 5 (4 fichiers, 87 lignes, environnement node, aucun test de composant), Playwright 1.63 (13 spécifications e2e sur un vrai backend, Chromium par défaut, Firefox/WebKit en option), `@axe-core/playwright` (6 scans axe) |
| Qualité | ESLint 9 (react, react-hooks), pas de vérification de types du code applicatif |
| Distribution | Paquet APT + ISO appliance ; workflow GitHub « Publish » ; `test` = développement, `master` = production ; PR obligatoires avec contrôles requis (backend, frontend, shell, e2e) |
| Polices | Archivo (sans/titres) + IBM Plex Mono, chargées depuis le CDN Google Fonts (`index.html:8-10`) — incompatible avec une installation hors-ligne |
| Internationalisation | Aucune : chaînes anglaises inline ; `lib/labels.js` traduit quelques valeurs de fil ; quelques fuites françaises (« Migrer VM », « Tester », « Conteneur », « Processeur », « Virtualisation », unités « o/Ko ») |

Identifiants de fil français (`nom`, `etat`, `en_cours`, `observateur`, `actif`, `arrete`…) : **contrats** de l'API et de la base, jamais renommés. Seul le texte affiché est traduit.

## 3. Routes et écrans

| Route | Composant | Garde | Remarques |
|---|---|---|---|
| `/` | redirection vers `/datacenter` (replace) | session | la query string est perdue |
| `/datacenter?tab=` | AppShell → panneau Datacenter | session | 16 onglets : summary, activity, storage, templates, backups, exports, permissions, reseau, automation, containers, nodes, ha, compat, notifications, sso, journal |
| `/node/:id?tab=` | AppShell → panneau Nœud | session | `local` = hôte qui exécute Hyperlite ; 7 onglets : summary, system, network, disk, tasks, compat, shell |
| `/vm/:id?tab=` | AppShell → panneau VM | session | id = nom de la VM seul (ambigu entre nœuds) ; 6 onglets : summary, console, hardware, options, backup, snapshots |
| `*` | redirection vers `/datacenter` | session | pas de page 404 |
| `/console/:name?mode=terminal` | ConsoleWindow (fenêtre autonome VNC ou terminal) | sa propre garde de session, aucun contrôle de rôle | sonde `GET /vms/{name}` toutes les 5 s |
| `/host-shell` | HostShellWindow | session + rôle admin | message « Host shell reserved for the admin role. » |
| `/container-terminal/:name` | ContainerTerminalWindow | session seulement | pas de contrôle de rôle côté client |
| (non authentifié) | LoginScreen à l'URL demandée | — | lien profond conservé après connexion ; SSO via `/?sso_token=` ou `?sso_error=` |

Aucune page conteneur : pas de type de sélection « conteneur », pas d'onglets de détail. Les conteneurs ne vivent que dans l'onglet Datacenter > Containers. La sélection d'un pool de stockage affiche un espace réservé.

## 4. Structure de l'interface actuelle

- **Cadre** : `SidebarProvider` shadcn > `Sidebar` (marque, rail de 15 entrées Datacenter en 3 blocs, arbre de ressources) + `SidebarInset` (`Header`, zone centrale, `TaskLogPanel`) + `Toaster` + `ConfirmHost`. Le cadre appelle `loadAll()` une fois puis `refreshAll()` toutes les 6 s.
- **Sélection** : `useInfraStore.selection` (type + nom), synchronisée à l'URL par `useUrlSync` en `replace` (l'historique du navigateur ne restitue donc ni sélection ni onglet).
- **Panneau central** : `CentralPanel` choisit le jeu d'onglets selon le type de sélection et reflète l'onglet actif dans `?tab=`.
- **Deux surfaces de navigation Datacenter** redondantes : la barre d'onglets et le rail latéral (libellés différents ; « SSO » absent du rail ; « Templates / ISO » ouvre Templates alors que les ISO se gèrent dans Storage).
- **Stores Zustand** : `useInfraStore` (nœuds, VM, pools, réseaux, sélection, recherche, mode d'arbre, onglet, tâches, thème, actions VM), `useAuthStore` (jeton, session), `useConfirmStore` (dialogue à promesse, un seul emplacement).
- **Persistance navigateur** : `localStorage` `hyperlite_token`, `hyperlite_username` et `hyperlite_role` (les deux derniers jamais relus), `hyperlite-theme` ; cookie `sidebar_state` (jamais relu).

## 5. Fonctionnalités et couverture

Décompte de l'audit (détails dans la matrice `01`) :

| Domaine | Lignes de faits |
|---|---|
| Coquille, navigation, Inventory Explorer, en-tête, session | 223 |
| Datacenter (16 onglets + composants partagés) | 282 |
| VM (6 onglets), nœud (7 onglets), fenêtres console | 125 |
| Assistants VM/conteneur, utilitaires, confirmations, primitives | 128 |
| Client API (153 fonctions exportées + 4 assistants internes + 12 appels directs) | 169 |
| Backend (168 endpoints/WebSocket, modèle de permissions, boucles ; hors 32 tables) | 188 |

Écrans/onglets à reconstruire : 1 connexion, 3 fenêtres autonomes, 16 onglets Datacenter, 7 onglets nœud, 6 onglets VM, 2 assistants (5 étapes VM + conteneur), 2 modales d'en-tête (mise à jour, sécurité du compte), tiroir de détail VM, dock des tâches.

## 6. Modèle de permissions

- **Backend (source de vérité)** : rôles globaux `admin` et `observateur` ; rôles ACL prédéfinis `lecteur`, `operateur`, `gestionnaire` ; rôles personnalisés `custom:<id>` (sous-ensemble libre des privilèges) ; portée par utilisateur, groupe ou pool de ressources ; privilèges par VM/conteneur (`vm.view`, `vm.power`, `vm.console`, `vm.snapshot`, `vm.resize`, `vm.hardware`, `vm.clone`…). Suppression, migration, modèle, HA, restauration, suppression de sauvegarde, création de volume : rôle `admin`.
- **Frontend actuel** : uniquement `role === 'admin'` (`selectIsAdmin`). Conséquences : un utilisateur qui a des droits ACL n'obtient aucun bouton ; aucun onglet Datacenter n'est masqué pour un observateur (Permissions, Journal, Exports, SSO répondent 403 → spinners éternels ou toasts d'erreur répétés toutes les 8 s) ; à l'inverse, Start/Stop des cartes/tableaux/tiroir ne sont pas soumis au rôle (le backend refuse).
- **Exigence de la reconstruction** : masquer ce que l'utilisateur ne peut jamais faire, **désactiver avec une raison** ce qu'il pourrait faire dans un autre état ; ne jamais élargir un droit. Une table de capacités calculée côté client à partir de `/auth/me` reste à définir (voir amélioration A-05 de `06`).

### 6 bis. Constats backend qui conditionnent la reconstruction (audit de 38 routeurs, lecture intégrale)

| # | Constat | Preuve | Effet sur le front |
|---|---|---|---|
| B1 | **Sécurité — un observateur peut ouvrir un shell dans une VM** : le ticket de terminal SSH d'une VM ne demande que `vm.console`, que tout observateur global reçoit automatiquement ; le commentaire du code dit « admin seulement » | `app/routers/vms/console.py:142`, `app/core/permissions.py:304-305` | **À signaler à part (hors périmètre front)** ; le bouton Terminal doit rester soumis à une règle explicite et visible, pas seulement au backend |
| B2 | Les listes ne sont **pas filtrées par ACL** : tout utilisateur connecté voit toutes les VM, conteneurs, tâches, sauvegardes et l'historique de métriques | `inventory.py:16-46`, `containers.py:106`, `tasks.py:14`, `backups.py:26`, `metrics.py:45` | L'Inventory ne peut pas se fier au backend pour masquer ; les boutons se calculent avec `/auth/me` + ACL (voir A-05) |
| B3 | Seuls ~10 endpoints acceptent `node` (liste VM, power, delete, migrate + check, pools de stockage, HA) ; toutes les autres sous-routes VM agissent sur l'hôte local et peuvent atteindre une VM homonyme locale | `inventory.py:34-46`, `lifecycle.py` | Interface d'un nœud distant : n'afficher que ce qui est réellement adressable, avertir sinon |
| B4 | Aucun push (ni WebSocket ni SSE) pour tâches, états, métriques, audit, notifications : tout est de l'interrogation | `backend.md` §2 | Couche d'interrogation centralisée (cadence adaptative, pause en onglet caché) |
| B5 | Sauvegarde, restauration, export, exécution de job, création de VM, clone, envois : ne renvoient pas d'identifiant de tâche ; il faut retrouver la tâche par `GET /tasks?type=&cible=`. Seuls snapshots, migration, sauvegarde de conteneur et mise à jour renvoient `task_id` | `backend.md` §3 | Corrélation tâche/ressource à construire côté front ; échecs asynchrones visibles seulement dans la tâche |
| B6 | Progression réelle seulement pour sauvegarde, export, migration, job, mise à jour, restauration, sauvegarde de conteneur ; jamais pour snapshot, création VM/conteneur, clones, modèles, envois | idem | États « indéterminé » explicites |
| B7 | Les GET en lecture écrivent une ligne d'audit à chaque appel (`/vms`, `/networks`, `/storage`, `/host/capabilities`) : l'interrogation à 6 s inonde le journal ; `GET /vms/{name}/provisioning` **modifie l'état serveur** quand on l'interroge | `inventory.py:28`, `runtime.py:259,282-383` | Réduire le nombre d'appels, ne pas multiplier l'interrogation de provisioning |
| B8 | `detail` d'erreur est tantôt une chaîne, tantôt une liste de chaînes, tantôt une liste d'objets (422) | `create.py`, `containers.py`, pydantic | Un seul normaliseur d'erreurs (corrige `[object Object]`) |
| B9 | JWT de 4 h sans rafraîchissement ; `SECRET_KEY` aléatoire par processus si non fixée (sessions perdues à chaque redémarrage) ; verrouillage par IP source (derrière un proxy, tous les utilisateurs partagent l'IP) ; SSO contourne le TOTP et renvoie le jeton dans l'URL | `security.py`, `auth.py:65`, `sso.py:126` | Gestion d'expiration de session explicite (avertissement + reconnexion sans perte de saisie) |
| B10 | Toute route GET inconnue renvoie `index.html` avec HTTP 200 ; `/health` public expose nom d'hôte, noyau, versions | `main.py:96,139` | Le client doit valider le type de contenu (déjà partiellement) ; `/health` reste sondé pour la mise à jour |
| B11 | Suppression d'utilisateur : ses lignes ACL et jetons API restent ; suppression de VM : sauvegardes, planning, HA et métriques restent ; conversion en modèle irréversible sans confirmation serveur | `auth.py:339`, `lifecycle.py:228`, `templates.py:28` | Confirmations fortes et texte d'impact exact dans l'interface |
| B12 | Tâches `en_cours` jamais nettoyées après un crash ; jobs sans édition ; canaux de notification sans édition ; URL de webhook renvoyée en clair aux admins | `main.py:161`, `jobs.py`, `notifications.py:50` | Afficher l'ancienneté d'une tâche « en cours » ; marquer les secrets comme tels |

## 7. API et flux de données

- 153 fonctions dans `dashboard/src/api/client.js` (732 lignes), toutes de minces enveloppes de `realFetch` (jeton Bearer, normalisation d'erreur, gestionnaire 401). Chemins sans préfixe `/api` (même origine).
- 168 routes backend. **3 routes sans appelant frontend** : `GET /metrics` (Prometheus, scrapeurs externes), `DELETE /storage/{pool}/volumes/{vol}`, `PUT /vms/{name}/network`. **3 fonctions client sans appelant d'interface** : `deleteVmDisk`, `fetchJob`, `fetchContainer`. Paramètres backend jamais envoyés : `jusqu_a` (`/audit`), `type`, `username`, `depuis` (`/tasks`). Ce sont des capacités **cachées ou inachevées** : la matrice les conserve (statut « à clarifier ») et `06` propose de les exposer.
- Appels hors client : `useAuthStore` (`/auth/me`, `/auth/login`, `/auth/login/2fa`), `GET /health` (NodeSystemTab, UpdateModal), 2 envois XHR (`POST /isos`, `POST /vm-disks`), lien `/auth/sso/login`, `window.open` sur `/vm-exports/download`.
- Pas de délai d'attente, pas d'annulation, pas de nouvelle tentative, pas de cache ni de déduplication.
- **Le nœud n'est pas propagé** : seuls start/stop/restart/delete/migrate/migration-check/HA-enable passent le paramètre `node`. Clone, modèle, disques, interfaces, snapshots, sauvegardes, pare-feu, métriques, console, provisioning, export, auto-nettoyage et **création de VM** visent toujours l'hôte local, même pour une VM listée sous un nœud distant. Le résumé, le système et le shell d'un nœud distant affichent les données du **local**.
- Correspondances (`mapVm`, `mapPool`, `fetchNodes`) : liste blanche de champs ; l'`id` libvirt, `uuid`/`autostart` des pools, `baux_dhcp`, `vms.total`, `etat_infrastructure` et les métadonnées des nœuds (`ssh_user`, `ssh_port`, `derniere_verification`, `added_at`) sont abandonnés au passage ; `memoire_utilisee_mo`, `disque_go`, `disque_utilise_go` sont forcés à `null` (colonne « DISK » toujours `--`).

## 8. Temps réel (interrogation et WebSocket)

| Boucle | Cadence | Endpoints | Nettoyage |
|---|---|---|---|
| `refreshAll` (AppShell) | 6 s (`setInterval`) | `/dashboard`, `/nodes` (×3), `/nodes/{n}/summary`, `/vms`, `/storage`, `/networks` (+ variantes `?node=`) | oui ; continue en onglet caché |
| Métriques VM live | 4 s | `/vms/{name}/metrics` (fenêtre de 120 points) | oui |
| Provisioning | 6 s tant qu'en cours | `/vms/{name}/provisioning` | oui |
| Datacenter Summary | 15 s métriques, 10 s tâches | `/host/metrics/history`, `/tasks` | oui |
| Activity / Exports / NodeTasks | 8 s | `/tasks`, `/vm-exports`, `/tasks?node=` | oui |
| HA / métriques nœud | 15 s | `/ha`, `/host/metrics/history` | oui |
| CompatibilityTab | ~6 s (effet dépendant du tableau `nodes` remplacé à chaque tick) | capacités de chaque nœud | tableau qui clignote |
| Conteneurs (formulaire ouvert) | 5 s | `/containers` | — |
| ConsoleWindow | 5 s | `/vms/{name}` | oui |
| Mise à jour | 1 s puis 2 s, jusqu'à 120 s | `/tasks/{id}`, `/health` | non |
| Attente de snapshot | 1 s | `/tasks/{id}` | sans délai maximal |

WebSocket (tous avec ticket à usage unique, TTL 30 s, `?ticket=`) : `/vms/{name}/console` (VNC via noVNC), `/vms/{name}/terminal`, `/host/terminal`, `/containers/{name}/terminal` (xterm ; message de redimensionnement `"\x00" + JSON({cols,rows})`). Pas de reconnexion automatique. Aucun ticket n'accepte un nœud.

## 9. Données simulées ou codées en dur

Aucune donnée n'est simulée. Valeurs codées en dur repérées : `utilisateur: "admin"` sur toutes les tâches créées côté client ; sentinelle nœud `local` ; `restart` toujours `force=true` (arrêt brutal + démarrage) ; `confirm=true` ajouté par le client pour 8 suppressions ; pool `default` pour l'attache de disque ; vCPU max 16 dans le formulaire conteneur (indépendant de la politique d'allocation) ; VM `disque_go` etc. `null` ; chaîne de version synthétisée ; interface réseau et ports par défaut dans certains formulaires.

## 10. Fonctionnalités cachées, incomplètes ou fragiles (extraits)

| Constat | Preuve | Conséquence pour la reconstruction |
|---|---|---|
| Le choix du nœud dans l'assistant VM est décoratif : la charge utile n'a pas de champ nœud, le backend `VMCreate` non plus | `VMWizard.jsx:104-114` | À clarifier : garder le pas « Node » et le documenter comme « hôte local » tant que le backend ne suit pas |
| « Clone » de la carte/menu ouvre l'onglet Options qui n'a pas de clone (il est dans Summary seulement) | `VMActionMenu.jsx:44,54`, `VMCard.jsx:64` | Corriger la destination |
| Raccourcis annoncés (`C`, `S`, `⌘K`, « Esc to cancel ») inexistants | `VMActionMenu.jsx:41-61`, `VMTable.jsx:65` | Implémenter réellement (palette de commandes) |
| Champs sans contrôle : SSO `scope`, notification `use_tls`, nœud `ssh_port`, réseau `subnet_netmask`, description de pool, déploiement de modèle > réseau | `datacenter.md` | Exposer dans « Advanced » ; conserver les valeurs par défaut |
| Actions sans confirmation : Automation « Run » (exécute des commandes shell), HA « Recover », Restart VM, Start/Stop dans cartes/tableaux | `AutomationTab.jsx:89`, `HaTab.jsx:50`, `VMSummaryTab.jsx:230` | Ajouter une confirmation nommant l'impact |
| Message trompeur : supprimer un job d'automatisation « supprime l'historique » alors que le backend laisse les exécutions orphelines | `AutomationTab.jsx` vs `jobs.py:87-104` | Corriger le texte ou le backend |
| `RangeToggle`, `CompatChecks`, `GaugeRing`, `MetricsHistoryCard`, `OverallocationNote` non utilisés dans Datacenter ; `addVM`, `node.badge`, `resource.alerte`, primitives `chart/command/progress/scroll-area/slider/input-group/textarea/skeleton` non utilisés | `shell.md`, `datacenter.md` | À inventorier ; `command` (cmdk) servira à la palette |
| Classes CSS `.input` et `.btn-secondary` référencées et jamais définies | `StepTemplate.jsx:139,149`, `DriversIsoControl.jsx:31` | Champs sans style dans l'assistant |
| `window.prompt` natif pour cloner, restaurer, convertir en modèle, sauvegarder | `VMSummaryTab.jsx`, `VMBackupTab.jsx`, `ContainersTab.jsx` | Remplacer par de vrais formulaires (mêmes champs) |
| Sentinelle `__pending__` peut atteindre `POST` comme `import_disk` | `StepTemplate.jsx:31,53` | Bogue à corriger |
| Erreur 422 affichée `[object Object]` ; aucune protection contre le double envoi de « Create the VM » | `client.js:43`, `VMWizard.jsx:101-125` | À corriger avec le formulaire |
| Le filtre « tâches du nœud local » envoie `node=local` alors que le backend stocke le nom d'hôte libvirt : liste probablement vide *(non vérifié)* | `NodeTasksTab.jsx:60` | À vérifier avant de reconstruire l'onglet |

## 11. Actions sensibles

Toutes exigent une confirmation nommant la ressource dans la reconstruction (celles qui n'en ont pas aujourd'hui sont **en gras**).

- **Cycle de vie VM** : arrêt forcé, **redémarrage (toujours brutal)**, suppression (disque inclus), migration à chaud, restauration de snapshot, **Start/Stop dans les cartes**.
- **Données** : suppression de sauvegarde, restauration de sauvegarde (VM et conteneur), suppression d'ISO/modèle/volume/disque importable, suppression de pool de stockage (chemin « ZFS destroy »), détachement de disque, retrait d'interface.
- **Cluster** : retrait de nœud, **HA Recover**, désactivation HA, création de pool de stockage ZFS (local à un nœud).
- **Sécurité** : suppression d'utilisateur/groupe/rôle/pool, assignation ACL, réinitialisation de mot de passe, révocation de jeton API, désactivation 2FA, secret SSO.
- **Exécution de code** : shell hôte (admin), terminaux VM/conteneur, **Automation « Run »** (commandes shell planifiées), mise à jour du système (avec retour arrière automatique).
- **Hors-bande** : notifications (webhook, e-mail), export de disque (jeton de téléchargement).

## 12. Risques techniques et UX (tous avec preuve dans `audit/*.md`)

**Élevés** : nœud non propagé (données du local affichées pour un nœud distant) ; e2e sans couverture des assistants conteneur/ISO/import/Windows ; jeton dans `localStorage` et déconnexion locale seulement ; `dangerouslySetInnerHTML` du QR SVG ; Automation « Run » et HA « Recover » sans confirmation ; polling qui continue en onglet caché et n'a aucun délai d'attente ; sélection par nom seul (deux VM homonymes sur deux nœuds).
**Moyens** : `loadAll()` déclenché par des panneaux démonte tout le panneau central ; l'erreur de chargement initial ne s'efface jamais ; un seul emplacement de confirmation (la seconde promesse est orpheline) ; l'historique du navigateur ne restitue pas la navigation ; proxy de développement Vite incomplet (`/host`, `/jobs`, `/nodes`, `/tasks`, `/containers`… renvoient la page `index.html` traitée comme `null`) ; polices depuis un CDN ; couleurs d'état dupliquées entre CSS (#15803d) et JS (#16A34A) ; sidebar violette indépendante du thème ; défaut clair sans lecture de `prefers-color-scheme`.
**Faibles** : libellés français, faute « Virtualisation/Virtualization » qui scinde deux cartes, unités « o/Ko », identifiant `aria-label` indéfini (`p.nom`), ids de dégradé Recharts en collision, infobulle sombre en thème clair.

## 13. Inventory Explorer actuel

- **Modes** : *Server* (Datacenter > nœuds local + distants > pools de stockage puis VM) et *Pool* (Datacenter > un groupe par pool de stockage nommé « nom (id-nœud) » > **toutes** les VM de ce nœud : ce n'est pas une vraie appartenance ; les VM sont dupliquées sous chaque pool, des clés React peuvent entrer en collision et l'id de nœud interne fuit dans le libellé).
- **Types de lignes sélectionnables** : Datacenter, nœud, VM, pool de stockage. Pas de conteneurs, réseaux, ni états intermédiaires.
- **Recherche d'en-tête** : filtre par sous-chaîne du libellé, élague les branches non concordantes, sans surbrillance ni message « aucun résultat » ; masquée sous 640 px ; la phrase « Find a node or VM » n'existe pas dans le code.
- **Comportement** : chaque clic sur une ligne qui a des enfants bascule l'expansion ; ouverte par défaut pour la profondeur < 2 ; état d'expansion local par ligne (non persisté).
- **Clavier** : Tab (chaque ligne), Entrée/Espace, Gauche/Droite ; **pas de flèches haut/bas, Home/End, ni de saisie prédictive ; pas de tabindex itinérant** (confirmé par la sonde Playwright : `ArrowDown` ne déplace pas le focus). Rôles `tree` / `treeitem` / `group` présents.
- **Accessibilité** : pastille d'état sans alternative textuelle ; ni menu contextuel, ni glisser-déposer, ni info-bulle, ni compteurs.
- **Ce qui existe déjà côté backend et n'est pas exploité** : pools de ressources réels avec membres (`/pools`), groupes, ACL par VM, tags absents, état HA (`/ha`), tâches actives par ressource (`/tasks`).

## 14. Lacunes de données pour l'Inventory Explorer cible

| Besoin | Disponible ? | Effort |
|---|---|---|
| Vraie appartenance aux pools (mode Pool) | Oui : `GET /pools` (+ membres) | Front seul |
| Clé de sélection sans ambiguïté (nœud + nom) | Oui côté front (déjà `node` dans `mapVm`) ; URL à faire évoluer avec alias | Front seul |
| Conteneurs et réseaux dans l'arbre | Oui : `GET /containers`, `GET /networks` | Front seul |
| Tâche active / alerte par ressource | Partiel : `GET /tasks?statut=en_cours` filtrable par ressource ; pas d'événements d'alerte unifiés | Front (dérivé) ; **backend pour une vraie vue d'alertes** |
| Compteurs d'enfants et état agrégé (VM en erreur sous un nœud) | Dérivable côté client | Front seul |
| Métriques par VM dans l'arbre | Oui (`/vms/{name}/metrics`) mais coûteux à 1 000 VM | **Backend** : endpoint groupé d'états/CPU (voir A-12) |
| Recherche serveur (>1 000 VM) | Non ; le filtre client suffit jusqu'à ~1 000 | Backend optionnel |
| Propagation `node` sur tous les endpoints VM | Non pour ~15 routes | **Backend** (voir A-13) |

## 15. Audit en 5 dimensions (méthode `impeccable audit`)

Note de 0 (critique) à 4 (excellent) ; jugements portés d'après le code et les captures, pas d'après des mesures instrumentées.

| Dimension | Note | Constats principaux |
|---|---|---|
| Accessibilité | 2 | Rôles ARIA présents (172 `aria-*`, 24 `role=` hors primitives), 6 scans axe verts en e2e ; mais arbre sans navigation flèches, pastilles d'état sans texte, `role="button"` qui écrase la sémantique de menu, erreur de chargement sans `role="alert"`, thème ignorant `prefers-color-scheme`, écran mobile sans recherche, indications clavier fictives |
| Performance | 2 | Rafraîchissement global toutes les 6 s (~8 requêtes dont 3 fois `/nodes`), aucune virtualisation de liste, aucun délai d'attente, historique de métriques rechargé à chaque rendu (fonction fléchée en ligne), interrogation en onglet caché, polices d'un CDN, `loadAll()` qui démonte l'écran |
| Thèmes | 2 | Rampe « anthracite » exploitée mais la sidebar violette est indépendante du thème ; couleurs d'état dupliquées entre CSS et JS ; jetons inutilisés (`panel`, `surface`, `accent-pink`, `chrome-*`) ; classes `.input` non définies ; clair par défaut sans préférence système |
| Responsive | 2 | Tiroir mobile fonctionnel à 390 px ; à 820 px la sidebar reste et il n'y a pas de bouton de menu ; recherche et « Create container » masquées sous 640 px même pour un admin ; graphes à un point avec étiquettes coupées ; aucun débordement horizontal relevé sur les captures |
| Intégrité de l'implémentation | 2 | 0 constat du détecteur ; en revanche code mort, doublons (libellés de tâches, couleurs), valeurs codées en dur, aucun test de composant, écarts client/backend (nœud, rôle ACL) |
| **Total** | **10 / 20** | « Acceptable, travail important requis » : la base fonctionnelle est riche, l'ergonomie d'exploitation et l'accessibilité sont à reconstruire |

### Critique heuristique (Nielsen, échelle 0–4)

| Heuristique | Note | Preuve |
|---|---|---|
| 1 Visibilité de l'état | 2 | Pastilles sans texte ; tâches locales non persistées ; erreur initiale figée |
| 2 Correspondance avec le monde réel | 3 | Vocabulaire d'admin (VM, pool, snapshot) correct ; fuites françaises |
| 3 Contrôle et liberté | 2 | Fermeture d'assistant en plein envoi ; `replace` sans historique |
| 4 Cohérence | 2 | Deux navigations Datacenter ; `window.prompt` mêlé à des dialogues |
| 5 Prévention des erreurs | 2 | Restart brutal sans confirmation ; double envoi possible |
| 6 Reconnaissance plutôt que mémoire | 2 | Pas de palette de commandes ni de recherche visible sur mobile |
| 7 Flexibilité | 1 | Aucun raccourci réel, aucune vue enregistrée |
| 8 Design minimaliste | 3 | Densité raisonnable, mais gros doublons de KPI |
| 9 Récupération d'erreur | 2 | `[object Object]`, 403 en boucle |
| 10 Aide | 1 | Aucune aide contextuelle |

## 16. Tests existants et lacunes

Vitest : `capabilities` (2), `format` (4), `labels` (2), `windowsProfile` (5 blocs). Playwright : auth (8), automation (5), coverage (5), degraded (10), destructive (3), keyboard-responsive (7), nav (4), network (4), pages (23), settings (10), users (6), vm-advanced (7), vm (11). **Non couvert** : assistant conteneur, chemins ISO / import / Windows / système invité / auto-nettoyage / pool de l'assistant VM, limites hôte, terminaux et consoles, migration, HA, notifications e-mail réelles.

## 17. Documentation et cohérence

`docs/features.md` affirme « Windows unattended installation is not supported » alors que le support Windows a été fusionné (#169/#171) : à réconcilier. Les documents de cette phase sont rédigés en français (noms de fichiers et statuts imposés par le brief) alors que la règle du dépôt est « tout en anglais » ; une version anglaise sera produite avant toute fusion si demandé.

## 18. Ce qui reste à vérifier avant l'implémentation

1. Le filtre `node=local` de l'onglet Tâches d'un nœud (liste vide ?).
2. Les valeurs réelles de `pool.etat` renvoyées par `/storage` (couleurs de l'arbre).
3. Si l'endpoint HA « recover » accepte le nœud sentinelle `local`.
4. Si `GET /backups` filtre par ACL pour un non-admin.
5. Si l'attache de disque fonctionne pour un pool ZFS par défaut.
6. Le rendu réel des largeurs d'assistant selon le paquet npm `cn`.
