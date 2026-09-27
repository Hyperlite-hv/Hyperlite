# 02 — Architecture de l'information

> Phase 1 — documentation seulement. Cette architecture est une **proposition** à valider ; aucune route n'est modifiée tant que vous n'avez pas répondu.
> Principe directeur (PRODUCT.md) : *diagnostiquer là où l'on regarde* — l'état, les tâches, les journaux et la compatibilité d'une ressource sont à un pas de la ressource ; *calme en temps normal, indubitable en cas de panne*.

## 1. Vue d'ensemble : trois zones, une seule source de vérité

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Top bar : Hyperlite · breadcrumb · [ Find a node or VM  ⌘K ] · Create ▾ · ⟳ · 🔔 · 🌐 · ◐ · user │
├────┬─────────────────────┬───────────────────────────────────────────────────┤
│Rail│ Inventory Explorer  │ Workspace (resource header + tabs + content)      │
│ 6  │  Server | Pool      │                                                   │
│sec.│  search  tree       │                                                   │
├────┴─────────────────────┴───────────────────────────────────────────────────┤
│ Dock : Tasks · Logs · Alerts   (repliable, hauteur mémorisée)                │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Rail de sections** (56 px, icônes + libellé court) : Overview · Infrastructure · Virtual Machines · Activity · Security · Settings. Il change la *zone de travail*, pas l'arbre.
- **Inventory Explorer** (panneau redimensionnable 240–420 px, repliable ; **Ctrl/Cmd+B** conservé) : navigation par ressource, toujours disponible.
- **Workspace** : en-tête de ressource (nom, type, état en texte + forme + couleur, actions principales, raccourci « copier le lien ») puis onglets.
- **Dock** (bas, repliable) : *Tasks* (persistées + de session), *Logs* (journal d'audit filtré sur la ressource sélectionnée), *Alerts* (seuils dépassés, nœuds hors ligne, échecs de tâche, HA).
- **Top bar** : recherche globale `Find a node or VM` (aussi `Ctrl/Cmd+K`), menu **Create** (VM, conteneur, réseau, pool, sauvegarde… selon le contexte), sélecteur de langue EN/FR, bascule de thème (sombre par défaut, clair conservé, option « système »), menu utilisateur (rôle, sécurité du compte, mises à jour pour admin, déconnexion).

## 2. Sections et contenu (relogement de tout l'existant)

| Section | Contenu (nouvel emplacement) | Anciennes routes / onglets relogés |
|---|---|---|
| **Overview** | Santé de l'infrastructure, KPI capacité (CPU, RAM, stockage), VM par état, nœuds, tâches en cours, alertes récentes, sauvegardes en retard, journal récent | `/datacenter?tab=summary`, `activity` |
| **Infrastructure** > Nodes | Liste des nœuds, ajout/retrait, clé du cluster, diagnostic de compatibilité avant ajout | `datacenter?tab=nodes` |
| Infrastructure > Cluster (HA & compatibilité) | HA (activation, récupération manuelle confirmée), matrice de compatibilité des nœuds | `tab=ha`, `tab=compat` |
| Infrastructure > Storage | Pools (répertoire, NFS, ZFS), volumes, bibliothèque ISO, disques importables, envois | `tab=storage` (et ISO de « Templates / ISO ») |
| Infrastructure > Networks | Réseaux libvirt, détails DHCP, pare-feu réseau | `tab=reseau` |
| Infrastructure > Templates | Modèles, conversion, déploiement | `tab=templates` |
| Infrastructure > Data protection | Sauvegardes (toutes VM), planifications, exports de disque | `tab=backups`, `tab=exports` |
| **Virtual Machines** | Tableau dense de toutes les VM + conteneurs (filtres, tri, actions groupées) ; vue VM détaillée ; vue conteneur détaillée (**nouvelle**, les données existent) | `/vm/:id`, cartes/tableau de la vue nœud, `tab=containers` |
| **Activity** | Tasks (historique persistant, filtres complets), Audit journal (filtres, dont `jusqu_a` jamais exposé), Alerts | `tab=activity`, `tab=journal`, dock des tâches |
| **Security** | Users, Groups, Roles (personnalisés), Resource pools, ACL, SSO, My account (2FA, jetons API) | `tab=permissions`, `tab=sso`, modale « Account security » |
| **Settings** | Automation (jobs), Notifications (canaux), Updates, Profile & allocation policy, About/health | `tab=automation`, `tab=notifications`, modale de mise à jour, profil de déploiement (Compatibility) |

## 3. Routes (compatibilité totale + ajouts)

**Règle** : chaque route historique reste valide pendant au moins une version, avec le même comportement et les mêmes paramètres ; les nouvelles routes sont des alias qui pointent sur les mêmes composants. Une route ne disparaît qu'après une décision explicite.

| Route historique | Devient | Statut |
|---|---|---|
| `/` | `/overview` (redirection) | alias |
| `/datacenter?tab=summary` | `/overview` | alias |
| `/datacenter?tab=activity` | `/activity/tasks` | alias |
| `/datacenter?tab=journal` | `/activity/journal` | alias |
| `/datacenter?tab=storage` | `/infrastructure/storage` | alias |
| `/datacenter?tab=templates` | `/infrastructure/templates` | alias |
| `/datacenter?tab=backups` / `exports` | `/infrastructure/protection` (`?view=backups|exports`) | alias |
| `/datacenter?tab=reseau` | `/infrastructure/networks` | alias |
| `/datacenter?tab=nodes` / `ha` / `compat` | `/infrastructure/nodes` / `/infrastructure/cluster?view=ha|compat` | alias |
| `/datacenter?tab=containers` | `/vms?kind=container` | alias |
| `/datacenter?tab=permissions` / `sso` | `/security/users…` / `/security/sso` | alias |
| `/datacenter?tab=automation` / `notifications` | `/settings/automation` / `/settings/notifications` | alias |
| `/node/:id?tab=summary|system|network|disk|tasks|compat|shell` | `/infrastructure/nodes/:id/<tab>` | alias |
| `/vm/:id?tab=summary|console|hardware|options|backup|snapshots` | `/vms/:node/:name/<tab>` avec les 9 onglets cibles `summary, metrics, console, hardware, storage, network, snapshots, tasks, settings` ; anciens ids conservés comme alias (`options`→`settings`, `backup`→`snapshots?view=backups`) ; le nom seul reste accepté, l'ambiguïté inter-nœuds est levée par le nœud | alias |
| `/console/:name?mode=terminal`, `/host-shell`, `/container-terminal/:name` | **inchangées** (fenêtres autonomes, mêmes gardes) | conservées |
| *(nouveau)* `/vms/:node/:name` `?tab=`, `/containers/:name`, `/networks/:name`, `/storage/:pool` | pages de détail (les conteneurs, réseaux et pools n'en ont pas aujourd'hui) | ajout |
| *(nouveau)* `*` | page « introuvable » avec recherche | ajout |

L'état d'interface est **dans l'URL** : `?tab=`, `?q=` (recherche), `?mode=server|pool`, filtres de tableaux. Le bouton Précédent du navigateur restitue la sélection et l'onglet (aujourd'hui en `replace` : voir A-07).

## 4. Inventory Explorer — spécification

### 4.1 Modes

| Mode | Hiérarchie | Source |
|---|---|---|
| **Server** (défaut) | Datacenter › Nodes (local + distants) › {Virtual machines, Containers, Storage pools, Networks} › ressources | `/nodes`, `/vms`, `/containers`, `/storage`, `/networks` (déjà chargés) |
| **Pool** | Datacenter › *Resource pools* réels › VM / conteneurs membres ; groupe « Unassigned » pour le reste ; les pools de stockage restent visibles en mode Server | `/pools` + membres (existe déjà côté backend) |

⚠ **Décision D-01 à valider** : aujourd'hui le mode Pool regroupe par *pool de stockage* et affiche **toutes** les VM du nœud sous chacun (fausse appartenance, doublons, fuite de l'identifiant de nœud). Je propose de le remplacer par l'appartenance réelle aux **pools de ressources** (déjà gérés dans Security). Si vous préférez garder le sens « pool de stockage », le mode Pool devient « VM par pool de stockage utilisé » (nécessite les volumes) et le mode Server garde les pools de stockage par nœud. La matrice conserve la fonction actuelle jusqu'à cette décision (statut « À conserver et améliorer »).

### 4.2 Nœuds de l'arbre

| Type | Icône (forme distincte) | Libellé | Sous-libellé / compteurs | État |
|---|---|---|---|---|
| Datacenter | globe | nom | `4 nodes · 37 VMs · 2 alerts` | pire état enfant |
| Node | serveur | nom ; badge `local` | `12/16 VMs running` | En ligne / Hors ligne / Inconnu / Erreur |
| VM | écran | nom | IP ou `stopped` ; icône OS | Running / Stopped / Paused / Blocked / Crashed / Suspended / Shutting down / Unknown |
| Container | boîte | nom | image | Running / Stopped |
| Storage pool | cylindre | nom | `72 % used` | Active / Degraded / Inactive / Unreachable |
| Network | nœud réseau | nom | `bridge br0` | Active / Inactive |
| Resource pool (mode Pool) | dossier | nom | `5 members` | — |
| Groupe vide / chargement | — | libellé + `Loading…`/`No VMs` | — | — |

État = **forme + texte accessible + couleur** (jamais la couleur seule) ; une tâche en cours ajoute un anneau animé (désactivé si mouvement réduit) et une info-bulle « Backup 42 % ».

### 4.3 Comportements

- **Sélection** ouvre la ressource dans le workspace ; **un clic sur le chevron** (ou `→`/`←`) déplie/replie ; un clic sur la ligne ne referme plus la branche (aujourd'hui il bascule toujours).
- **Recherche** `Find a node or VM` : filtre à mesure de la frappe (sous-chaîne, insensible à la casse et aux accents) sur nom, IP, OS, état, nœud, pool ; **met en surbrillance** la correspondance, **garde le chemin des parents** visible, affiche le nombre de résultats (`3 results`) et un état vide clair avec action « Clear search » ; `Entrée` ouvre le premier résultat ; `Échap` vide. Même moteur que la palette `Ctrl/Cmd+K` (qui ajoute les actions : « Start vm-01 », « Open Storage », « Create VM »).
- **Menu contextuel** (clic droit, `Shift+F10`, touche Menu) : les mêmes actions que l'en-tête de ressource (Start/Stop/Restart/Console/Snapshot/Migrate/Delete…), mêmes gardes de rôle ; actions destructrices toujours séparées et confirmées.
- **Persistance** (préférence par utilisateur, `localStorage`) : mode, largeur, dépliage, dernier filtre ; **jamais** de secret.
- **Volume** : virtualisation de la liste au-delà de ~200 lignes visibles ; chargement paresseux des enfants ; indicateurs agrégés (un nœud « rouge » quand une VM enfant est en erreur, sans tout déplier).
- **Rafraîchissement** : conserve dépliage, sélection et focus lors du sondage (aujourd'hui `loadAll()` démonte tout).
- **Petits écrans** : l'Explorer devient un tiroir (bouton « Inventory » toujours visible, y compris à 820 px) et la recherche reste accessible (icône loupe → champ plein largeur).

### 4.4 Clavier (patron WAI-ARIA « tree view »)

| Touche | Effet |
|---|---|
| `↑` / `↓` | ligne précédente / suivante visible (tabindex itinérant : un seul arrêt Tab pour l'arbre) |
| `→` | déplie ; si déjà dépliée, va au premier enfant |
| `←` | replie ; si déjà repliée, va au parent |
| `Home` / `End` | première / dernière ligne visible |
| `Entrée` / `Espace` | sélectionne / ouvre |
| `*` | déplie tous les frères |
| Lettres | saisie prédictive (saute au libellé qui commence par la frappe) |
| `/` ou `Ctrl/Cmd+K` | focus sur la recherche |
| `Shift+F10` / Menu | menu contextuel |
| `Échap` | ferme menu, sinon vide la recherche |

Rôles ARIA : `tree`, `treeitem` (`aria-level`, `aria-expanded`, `aria-selected`, `aria-setsize/posinset` en liste virtualisée), `group`. Chaque ligne a un nom accessible complet : « vm-01, virtual machine, running, on node hl-devhub ».

### 4.5 Ce que l'Explorer ne fait pas (v1)

Pas de glisser-déposer entre pools, pas de tags, pas de vues enregistrées partagées (proposées en `06` : vues locales d'abord). Aucune de ces limites ne retire une fonction existante.

## 5. Écrans principaux (contenu, données, états, droits)

| Écran | Objectif (question à laquelle il répond) | Données / endpoints (inchangés) | Actions principales | États à traiter | Droits |
|---|---|---|---|---|---|
| Login | Qui êtes-vous ? | `/auth/login`, `/auth/login/2fa`, `/auth/sso/*` | Connexion, 2FA, SSO | chargement, erreur, 429 verrouillage, session expirée | public |
| Overview | L'infrastructure va-t-elle bien ? | `/dashboard`, `/nodes`, `/vms`, `/tasks`, `/host/metrics/history`, `/audit` (admin) | ouvrir un problème | skeleton, vide, hors ligne, périmé (« mis à jour il y a 45 s ») | tous |
| Nodes | Quels nœuds, dans quel état ? | `/nodes`, `/nodes/{n}/summary`, `/capabilities` | ajouter, retirer, diagnostiquer | hors ligne, inconnu | lecture : tous ; écriture : admin |
| Node detail (7 onglets) | Que fait ce nœud ? | idem + `/host/*` (local) | shell (local, admin), compat | distant : données locales interdites (voir §7) | selon onglet |
| VM list | Quelles VM, dans quel état ? | `/vms`, `/containers` | filtrer, trier, actions groupées | 0 VM, 1 000 VM, erreur partielle | lecture : tous |
| VM detail (6 onglets) | Que fait cette VM et que puis-je en faire ? | `/vms/{n}`… | power, console, matériel, options, sauvegardes, snapshots | arrêtée, en provisioning, en migration, verrouillée par tâche | ACL par privilège |
| Container detail (**nouveau**) | idem pour un conteneur | `/containers/{n}`, `/containers/{n}/…` | start/stop, terminal, sauvegardes, clone | idem | ACL conteneur |
| Storage | Où sont mes disques, combien de place ? | `/storage`, `/storage/{p}/volumes`, `/isos`, `/vm-disks` | créer pool, envoyer ISO/disque, supprimer | pool dégradé/inactif, envoi en cours, échec d'envoi | admin pour écrire |
| Networks | Quels réseaux, quelles règles ? | `/networks`, pare-feu | créer, supprimer, règles | doublon, pont invalide | admin pour écrire |
| Templates | Mes modèles | `/templates` | déployer, supprimer | vide | admin pour écrire |
| Data protection | Mes données sont-elles protégées ? | `/backups`, `/vm-exports` | restaurer, supprimer, exporter | échec de sauvegarde (suppression possible : aujourd'hui non) | mixte |
| Activity › Tasks | Que s'est-il passé, que se passe-t-il ? | `/tasks` (tous filtres) | filtrer, ouvrir le détail | tâche « en cours » très ancienne signalée | tous |
| Activity › Journal | Qui a fait quoi ? | `/audit` | filtrer, exporter | 403 pour non-admin : écran explicatif, pas de spinner | admin |
| Security | Qui peut faire quoi ? | `/auth/users`, `/groups`, `/pools`, `/acl`, rôles | créer, affecter, réinitialiser | 403 explicite | admin |
| My account | Mon accès | `/auth/me`, 2FA, jetons | activer 2FA, créer/révoquer jetons | jeton affiché une seule fois | tous |
| Settings | Comment l'installation se comporte-t-elle ? | `/jobs`, `/notifications`, `/update`, `/host/profile` | créer job, tester canal, mettre à jour | mise à jour en cours (retour arrière automatique) | admin |
| Create VM / Create container | Créer sans erreur | `POST /vms`, `POST /containers`, `/host/limits`… | assistant (voir `05`) | validation, double envoi bloqué, erreur normalisée | admin |
| Consoles autonomes | Accès graphique/terminal | tickets + WebSocket | plein écran, coller, redimensionner | ticket expiré (30 s), déconnexion visible, reconnexion manuelle | selon privilège |

## 5 bis. Détail VM : de 6 à 9 onglets sans rien perdre

Onglets cibles (imposés par le brief) : `Summary | Metrics | Console | Hardware | Storage | Network | Snapshots | Tasks & Logs | Settings`.

| Onglet cible | Contenu (fonctions existantes relogées) | Ancien onglet / source | Nouveau ? |
|---|---|---|---|
| Summary | État, identité (nom, UUID, OS, IP, utilisateur SSH), ressources allouées vs utilisées, actions de cycle de vie (Start, Stop, Force stop, Restart, Clone, To template, Delete, Migrate, HA), provisioning en cours, dernières tâches | Summary | non |
| Metrics | Courbes CPU/RAM/disque/réseau ; plages `1h · 24h · 7j · 30j` (`/vms/{n}/metrics/history`) ; mesures instantanées 4 s (`/metrics`) ; tableau de données de secours (accessibilité) ; pause/reprise | carte « MetricsHistoryCard » du Summary | promu en onglet |
| Console | Lanceur VNC / terminal SSH (ticket + fenêtre autonome), aide sur les modes, état de disponibilité | Console | non |
| Hardware | vCPU/RAM (édition à chaud), CD-ROM (insertion/éjection, ISO pilotes), contrôleur de disque, limites | Hardware + Options | partiel |
| Storage | Disques attachés, attache/détachement (volume neuf ou existant, pool, hot-plug sauf SATA), export de disque, disques importables | Hardware (disques) + menu d'actions | promu en onglet |
| Network | Interfaces (ajout/retrait), réseau, pare-feu de la VM (règles), IP/MAC/bail DHCP | Hardware (interfaces) + Options (pare-feu) | promu en onglet |
| Snapshots | Snapshots (libvirt interne ou ZFS), restauration, suppression ; **Backups** (à chaud/à froid, planification UTC, rétention, restauration) ; **Clone** et **Migration** (avec vérification de compatibilité) comme actions liées | Snapshots + Backup + Summary | regroupé |
| Tasks & Logs | Tâches de cette VM (`/tasks?cible=`), journal d'audit filtré, progression, liens ; **remplit le manque « diagnostiquer là où l'on regarde »** | dock de session + Activity | nouveau (données existantes) |
| Settings | Options de démarrage/auto-démarrage, auto-nettoyage (jours d'inactivité), HA, conversion en modèle, suppression (zone dangereuse en bas) | Options + boutons du Summary | regroupé |

Aucune fonction n'est supprimée : le tableau de correspondance ligne à ligne est dans la matrice `01` (colonnes « Nouvel emplacement UX »).

## 5 ter. Assistant « Create VM » : de 5 à 8 étapes

| Étape cible | Champs (existants) | Ancienne étape |
|---|---|---|
| 1 Source | mode : image cloud / ISO (avec installation automatique ou manuelle) / import de disque ; modèle ou ISO ; envoi d'ISO ; ISO de pilotes (Windows) | Template |
| 2 Identity | nom (`^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$`), système invité, utilisateur (`^[a-z_][a-z0-9_-]{0,31}$`), mot de passe (≥ 4) | Template/Resources |
| 3 Placement | nœud (**aujourd'hui décoratif** : indiqué « local host » tant que le backend ne suit pas), pool de stockage | Node + Resources |
| 4 Compute | vCPU, mémoire (bornes = `/host/limits`), ajustement Windows automatique | Resources |
| 5 Storage | disques (taille, contrôleur), auto-nettoyage (1–365 jours) | Resources |
| 6 Network | réseau (aucun VLAN aujourd'hui) | Network |
| 7 Advanced | contrôleur de disque, ISO de pilotes, options d'import, règles d'exclusivité (ISO ⊕ import ; pilotes ⇒ ISO) | Resources/Template |
| 8 Review & create | résumé fidèle (y compris auto-nettoyage, pilotes, disques supplémentaires : aujourd'hui absents du résumé), bouton unique protégé contre le double envoi, progression + lien vers la tâche | Review |

Garanties : aucune perte de saisie entre étapes (état unique conservé à la fermeture accidentelle), validation progressive, avertissements de limites hôte, erreur 422 lisible, création = une seule requête `POST /vms` avec la même charge utile qu'aujourd'hui (+ `node` seulement le jour où le backend l'accepte).

## 6. Composants de page réutilisables

`ResourceHeader`, `StatusIndicator` (forme + texte + couleur), `DataTable` (colonnes configurables, tri, filtres, sélection, virtualisation, densité, export, état vide/erreur/chargement), `KpiTile`, `UsageBar`, `LogViewer` (lignes monospaces, niveaux, recherche, suivre/pause, copier, filtre de gravité), `TaskRow` (progression déterminée ou indéterminée), `ConfirmDialog` (impact + saisie du nom pour les actions irréversibles), `FormSection` (Basic / Advanced repliable), `EmptyState`, `ErrorState` (message normalisé + réessayer + copier le détail), `PermissionNotice` (« Requires vm.snapshot — ask an administrator »).

## 7. Nœuds distants : honnêteté d'interface

Tant que le backend ne route pas `node` sur toutes les sous-routes (B3), la vue d'une VM sous un nœud distant :
- affiche un bandeau « Remote node — some operations run on the local host only » ;
- **désactive avec explication** les onglets/actions qui ne sont pas routés (snapshots, disques, sauvegardes, console, métriques…) plutôt que d'agir silencieusement sur une VM homonyme locale ;
- garde actifs start/stop/restart/delete/migrate/HA (déjà routés).
Cela documente et n'invente aucune fonction ; la levée de cette limite est l'amélioration backend A-13 (`06`).

## 8. Internationalisation (EN + FR dès le départ)

- Catalogues par domaine (`en/vm.json`, `fr/vm.json`…) chargés à la demande ; clés stables (`vm.action.start`) ; pluriels et nombres via `Intl` ; dates via `Intl.DateTimeFormat` avec le fuseau affiché (les horaires de sauvegarde sont en **UTC** : libellé « UTC » ajouté).
- Les **valeurs de fil restent françaises** (`actif`, `arrete`, `en_cours`, `observateur`) ; un dictionnaire `enum.<champ>.<valeur>` produit les libellés (extension de l'actuel `lib/labels.js`). Les messages d'erreur du backend (déjà en anglais ou français mélangés) sont affichés tels quels, encadrés d'un libellé traduit.
- Détection : préférence enregistrée › langue du navigateur › anglais. Sélecteur dans la barre supérieure. Un test vérifie qu'aucune clé n'est manquante dans une langue et qu'aucun texte français ne fuite en mode anglais (le e2e `pages.spec` a déjà un scan de texte français).
- Exception documentée à la règle « tout en anglais » du dépôt : les catalogues de traduction.

## 9. Densité, responsive, mouvement

| Point d'arrêt | Comportement |
|---|---|
| ≥ 1440 px (desktop) | Rail + Explorer + Workspace + Dock ; tableaux à toutes les colonnes |
| 1024–1439 px (laptop) | Explorer 240 px, colonnes secondaires masquables ; dock replié par défaut |
| 768–1023 px (tablette) | Explorer en tiroir (bouton « Inventory »), rail réduit, tableaux avec colonnes prioritaires |
| < 768 px (mobile) | Navigation basse compacte (Overview, VMs, Activity, More) ; tableaux → listes de cartes ; actions sûres uniquement en avant (start/stop/console) ; actions destructrices et assistants restent disponibles mais en pleine page |

Mouvement : 150–250 ms, uniquement pour transmettre un changement d'état (dépliage, ouverture de tiroir, progression) ; aucun mouvement décoratif ; `prefers-reduced-motion` respecté (les anneaux de tâche deviennent des libellés « 42 % »).
