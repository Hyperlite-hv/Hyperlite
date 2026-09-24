# 07 — Plan d'implémentation

> Phase 1 — documentation seulement. **Rien ne démarre sans votre validation explicite.**

## Principes de migration sûre

1. **Deux UI en parallèle** : la nouvelle interface vit dans `dashboard/src/next/`, montée derrière un aiguillage (`?ui=next` puis préférence) ; **l'ancienne reste par défaut** jusqu'à l'étape 16.
2. **Même client, mêmes stores, mêmes routes** : `api/client.js`, `useAuthStore`, `useInfraStore` et les fenêtres autonomes sont réutilisés ; aucun contrat API ne change.
3. **Domaine par domaine**, chaque étape se termine par : lint, tests, build, comparaison avec la matrice, revue.
4. **Tests d'abord** pour les chemins non couverts (conteneur, ISO, import, Windows, pool, migration, HA).
5. **Aucune dépendance lourde** ; ajouts éventuels : `@tanstack/react-virtual` (D-05) et Testing Library en développement.
6. **Backend** : aucune modification sauf besoin documenté et validé (A-05, A-11, A-12, A-13, A-14, A-15, A-30, A-33) ; chaque item est un chantier séparé.
7. **Livraison** : une branche par étape, PR vers `test`, contrôles requis verts ; publication vers `master` selon le flux habituel une fois l'ensemble validé.
8. **Travail sur hl-devhub d'abord** (clone de développement), comme demandé.

## Vue d'ensemble

| # | Étape | Dépend de | Effort relatif |
|---|---|---|---|
| 1 | Sécurisation des contrats et inventaire fonctionnel | — | S |
| 2 | Création des tokens (design system) | 1 | S |
| 3 | Shell global : navigation, Inventory Explorer (coquille) et routage | 2 | L |
| 4 | Composants partagés | 2, 3 | M |
| 5 | Migration et validation de l'Inventory Explorer | 2, 3 | L |
| 6 | Overview | 3, 4 | M |
| 7 | Liste des VM et détail VM (9 onglets) | 3, 4 | XL |
| 8 | Création et édition de VM (wizard 8 étapes) | 3, 4 | L |
| 9 | Nœuds | 3, 4 | M |
| 10 | Stockage | 3, 4 | M |
| 11 | Réseau | 3, 4 | M |
| 12 | Tâches, logs, événements, alertes | 3, 4 | M |
| 13 | Sécurité et paramètres | 3, 4 | M |
| 14 | Responsive et accessibilité | 3, 4 | M |
| 15 | Tests et non-régression | 14 | L |
| 16 | Retrait contrôlé de l'ancienne UI | 15 | S |


## Étape 1 — Sécurisation des contrats et inventaire fonctionnel

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/contracts/` (types JSDoc des réponses de l'API), `dashboard/src/__tests__/api-contract.test.js` (tests du client avec `fetch` simulé, un par fonction exportée), `dashboard/e2e/parity/` (squelette de parité par ligne de la matrice `01`) |
| Fichiers à modifier | aucun fichier applicatif ; `docs/frontend-rebuild/01-…` (statuts) |
| Routes concernées | aucune |
| APIs / services impactés | les 153 fonctions de `client.js` et 168 routes (lecture seule) |
| Fonctionnalités à protéger | Tout : cette étape fige le comportement actuel (« golden master ») |
| Améliorations intégrées | Résolution des points « À clarifier » de la matrice avant de construire ; captures de référence (30 → 90) desktop/laptop/tablette/mobile, sombre + clair |
| Risques | Tests de contrat fragiles si le backend évolue en parallèle |
| Tests | Vitest (contrat), Playwright (parité squelette), lint |
| Critères de validation | Chaque fonction de `client.js` a un test ; chaque ligne « À clarifier » a une décision écrite ; base de captures archivée |
| Plan de rollback | Sans objet (aucun changement de comportement) |

## Étape 2 — Création des tokens (design system)

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`tokens/tokens.css (couche primitive → sémantique → composant), ``dashboard/src/next/tokens/theme.js` (dark/light/system), `dashboard/public/fonts/*.woff2` (Plex Sans/Mono), `dashboard/src/__tests__/tokens-contrast.test.js` (contrastes WCAG des paires de `03` §2.4, chroma ≤ 0,135) |
| Fichiers à modifier | `dashboard/index.html` (retrait de Google Fonts au moment du basculement, pas avant) |
| Routes concernées | aucune |
| APIs / services impactés | aucun |
| Fonctionnalités à protéger | Thème clair existant et bascule manuelle ; couche de compatibilité pour les variables shadcn (`--background`, `--primary`…) |
| Améliorations intégrées | A-21 polices auto-hébergées, A-23 thème système, A-29 mouvement/contraste réduits |
| Risques | Régression visuelle de l'ancienne UI si les tokens fuient : ils sont **scopés** sous `[data-ui="next"]` |
| Tests | Test de contraste automatisé (reprend `contrast.py`), capture des primitives |
| Critères de validation | Toutes les paires ≥ cibles ; ancienne UI inchangée pixel à pixel (comparaison de captures) |
| Plan de rollback | Supprimer le dossier `next/tokens` ; l'ancienne UI ne le référence pas |

## Étape 3 — Shell global : navigation, Inventory Explorer (coquille) et routage

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`app/NextApp.jsx, `dashboard/src/next/layout/{TopBar,Rail,Workspace,Dock,SkipLink}.jsx, `dashboard/src/next/routes/aliases.js (table d'alias de `02` §3), ``dashboard/src/next/lib/pollingManager.js` |
| Fichiers à modifier | `dashboard/src/App.jsx` (un aiguillage `?ui=next` / préférence, **ancienne UI par défaut**), `dashboard/src/api/client.js` (aucune modification de contrat ; ajout d'un normaliseur d'erreurs en module séparé) |
| Routes concernées | toutes (alias) ; `/console/:name`, `/host-shell`, `/container-terminal/:name` inchangées |
| APIs / services impactés | réutilisation de `useAuthStore`, `useInfraStore`, `api/client.js` tels quels |
| Fonctionnalités à protéger | Session, 401, SSO, liens profonds après connexion, fenêtres autonomes, `Ctrl/Cmd+B` |
| Améliorations intégrées | A-07 historique réel, A-08 fraîcheur, A-16 erreurs, A-28 gestionnaire d'interrogation, A-32 bandeau nœud distant |
| Risques | Double interrogation (ancienne + nouvelle UI) : une seule est montée à la fois |
| Tests | e2e `auth`, `nav`, `degraded`, `keyboard-responsive` exécutés contre les deux UI ; test des alias (chaque route historique atterrit sur la même ressource) |
| Critères de validation | Toutes les routes historiques répondent ; aucun appel API en plus de l'ancien ; bandeau hors ligne fonctionnel |
| Plan de rollback | Retirer l'aiguillage dans `App.jsx` (un commit) |

## Étape 4 — Composants partagés

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`components/{StatusIndicator,DataTable,LogViewer,KpiTile,UsageBar,EmptyState,ErrorState,PermissionNotice,ConfirmDialog,FormSection,Toast,Skeleton}.jsx, ``dashboard/src/next/lib/{capabilities,format,enums}.js` |
| Fichiers à modifier | aucun fichier de l'ancienne UI |
| Routes concernées | aucune |
| APIs / services impactés | aucun (composants purs) |
| Fonctionnalités à protéger | Tous les états standard, `labels.js` (repris dans `enums`), formats de nombre/durée |
| Améliorations intégrées | A-18 formulaires à la place de `prompt`, A-20 droits avec raison, A-25 densité/colonnes, A-31 export CSV |
| Risques | Composants trop génériques : on ne les extrait qu'après deux usages réels |
| Tests | Vitest + Testing Library (nouveau, dev seulement, justifié : aucun test de composant aujourd'hui), scans axe par composant |
| Critères de validation | Chaque composant a ses 7 états rendus et testés (`chargement, vide, erreur, avertissement, hors ligne, refus, succès`) |
| Plan de rollback | Supprimer `next/components` |

## Étape 5 — Migration et validation de l'Inventory Explorer

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`explorer/{Explorer,Tree,TreeRow,SearchBox,ContextMenu,ModeSwitch,useInventoryIndex}.jsx, tests dédiés |
| Fichiers à modifier | aucun ; l'ancienne `ResourceTree` reste montée dans l'ancienne UI |
| Routes concernées | sélection dans toutes les routes de ressources |
| APIs / services impactés | `/nodes`, `/vms`, `/containers`, `/storage`, `/networks`, `/pools`, `/tasks` (lecture) |
| Fonctionnalités à protéger | **Toutes les lignes SHL « Inventory Explorer »** : Server, Pool, recherche, sélection, dépliage par défaut, clavier existant, thème, `Ctrl/Cmd+B` |
| Améliorations intégrées | A-02 clavier complet, A-03 recherche, A-04 mode Pool réel (si D-01 validée), A-06 favoris/récents, A-09 tâches actives, A-01 palette |
| Risques | Deux implémentations à maintenir jusqu'à l'étape 16 ; virtualisation à 1 000 VM (D-05) |
| Tests | e2e `keyboard-responsive` (arbre) + nouvelle spec `explorer.spec.ts` : Server, Pool, recherche (résultats, vide), sélection, dépliage, nœud hors ligne, VM en erreur, tâche active, 1 000 VM (jeu de données simulé côté test seulement), clavier complet, tiroir tablette/mobile, erreur API |
| Critères de validation | **Équivalence prouvée ligne par ligne avec la matrice** ; rendu < 100 ms pour 1 000 VM ; axe = 0 violation ; parcours clavier complet |
| Plan de rollback | Remettre l'ancienne `ResourceTree` (elle n'a jamais été retirée) |

## Étape 6 — Overview

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/overview/*.jsx |
| Fichiers à modifier | table d'alias uniquement |
| Routes concernées | `/overview` (alias de `/datacenter?tab=summary` et `activity`) |
| APIs / services impactés | `/dashboard`, `/nodes`, `/vms`, `/containers`, `/storage`, `/tasks`, `/audit`, `/host/metrics/history` |
| Fonctionnalités à protéger | Lignes DC « Summary » et « Recent activity » (tuiles, jauges, tâches 10 s, métriques 15 s) |
| Améliorations intégrées | A-10 alertes dérivées, A-08 fraîcheur, lien du journal corrigé |
| Risques | Capacité des nœuds distants absente : affichée « not reported » |
| Tests | e2e `pages` (Summary/Activity) + capture ; test de dérivation « Needs attention » |
| Critères de validation | Mêmes chiffres que l'ancienne vue pour le même jeu de données |
| Plan de rollback | Alias `/overview` retiré |

## Étape 7 — Liste des VM et détail VM (9 onglets)

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/vms/{VmList,VmDetail,tabs/*}.jsx |
| Fichiers à modifier | table d'alias |
| Routes concernées | `/vms`, `/vms/:node/:name?tab=`, anciennes `/vm/:id?tab=` |
| APIs / services impactés | `/vms*` (lecture et actions déjà existantes), métriques, provisioning, tâches |
| Fonctionnalités à protéger | Lignes VMN « VM > * » et composants VMCard/VMTable/VMActionMenu/VMDetailPanel, conditions d'activation des actions (`etat`, nœud), `node` sur start/stop/restart/delete/migrate/HA |
| Améliorations intégrées | A-12 (si backend), A-18, A-19 (si validé), A-20, A-32, onglet Tasks & Logs, Metrics en onglet |
| Risques | Actions destructrices : c'est la zone la plus risquée ; aucune modification de payload |
| Tests | e2e `vm`, `vm-advanced`, `destructive` exécutés contre la nouvelle UI ; nouveaux tests par onglet |
| Critères de validation | Chaque bouton envoie les mêmes requêtes que l'ancien (comparaison de traces réseau) |
| Plan de rollback | Alias `/vms` retiré ; ancienne vue intacte |

## Étape 8 — Création et édition de VM (wizard 8 étapes)

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/vms/wizard/{Wizard,steps/*}.jsx, ``dashboard/src/next/pages/containers/ContainerWizard.jsx` |
| Fichiers à modifier | aucun |
| Routes concernées | ouverture par menu Create (pas de route nouvelle obligatoire) |
| APIs / services impactés | `POST /vms`, `POST /containers`, `/host/limits`, `/templates`, `/isos`, `/vm-disks`, `/networks`, `/storage` |
| Fonctionnalités à protéger | Tous les champs des 5 étapes (payload identique), assistant conteneur, règles ISO ⊕ import, pilotes ⇒ ISO, ajustement Windows |
| Améliorations intégrées | A-16, A-17 (double envoi, brouillon), A-37 (Placement honnête) |
| Risques | Chemins non couverts aujourd'hui par les e2e (conteneur, ISO, import, Windows, pool) : **on écrit les tests d'abord** |
| Tests | Nouveaux e2e : création par image cloud, ISO auto, ISO manuelle, Windows + pilotes, import, conteneur ; test de charge utile comparée à l'ancien wizard |
| Critères de validation | Charges utiles identiques octet pour octet à l'ancien wizard pour les mêmes saisies |
| Plan de rollback | Ancien wizard conservé jusqu'à l'étape 16 |

## Étape 9 — Nœuds

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/nodes/*.jsx |
| Fichiers à modifier | aucun |
| Routes concernées | `/infrastructure/nodes`, `/infrastructure/cluster`, anciennes `/node/:id` |
| APIs / services impactés | `/nodes*`, `/host/*`, `/ha*`, `/health` |
| Fonctionnalités à protéger | Lignes DC « Nodes », « HA », « Compatibility » ; VMN « Node > * » ; shell hôte (fenêtre autonome) |
| Améliorations intégrées | A-27 (`ssh_port`), A-32, filtre de tâches corrigé (après vérification en étape 1), HA Recover confirmé |
| Risques | Onglets d'un nœud distant affichant le local : masqués/expliqués |
| Tests | e2e `pages` (nœud), `settings` ; nouveaux tests d'ajout/retrait de nœud simulés |
| Critères de validation | Parité des données pour le nœud local ; aucun affichage local pour un distant |
| Plan de rollback | Alias retirés |

## Étape 10 — Stockage

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/storage/*.jsx |
| Fichiers à modifier | aucun |
| Routes concernées | `/infrastructure/storage`, `/storage/:pool` |
| APIs / services impactés | `/storage*`, `/isos*`, `/vm-disks*` |
| Fonctionnalités à protéger | Lignes DC « Storage » (pools répertoire/NFS/ZFS, volumes, ISO, envois avec progression) |
| Améliorations intégrées | A-27 (suppression de volume/disque si validé), seuils d'usage |
| Risques | Suppression ZFS destructrice : double garde |
| Tests | e2e `settings` (pool), `destructive` (ISO) + nouveaux tests d'envoi d'ISO échoué |
| Critères de validation | Envoi en échec correctement signalé ; suppressions confirmées |
| Plan de rollback | Alias retirés |

## Étape 11 — Réseau

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/networks/*.jsx |
| Fichiers à modifier | aucun |
| Routes concernées | `/infrastructure/networks`, `/networks/:name` |
| APIs / services impactés | `/networks*`, pare-feu réseau/VM |
| Fonctionnalités à protéger | Lignes DC « Network », FirewallRulesEditor, détail réseau |
| Améliorations intégrées | A-27 (`subnet_netmask`), baux DHCP |
| Risques | État de détail partagé qui affichait des données périmées : corrigé |
| Tests | e2e `network` |
| Critères de validation | Parité avec `network.spec` |
| Plan de rollback | Alias retirés |

## Étape 12 — Tâches, logs, événements, alertes

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/activity/{Tasks,TaskDetail,Journal,Events,Alerts}.jsx, ``dashboard/src/next/layout/Dock.jsx` (branchement réel) |
| Fichiers à modifier | aucun |
| Routes concernées | `/activity/tasks`, `/activity/journal`, `/activity/events`, `/activity/alerts` |
| APIs / services impactés | `/tasks*`, `/audit` |
| Fonctionnalités à protéger | Lignes DC « Recent activity », « Journal », task log de session, cloche |
| Améliorations intégrées | A-10, A-27 (filtres jamais envoyés), A-33 (si backend), A-14 (si backend) |
| Risques | Les GET d'audit écrivent des lignes : cadence prudente |
| Tests | e2e `settings` (journal), `vm-advanced` (activité filtrée) + tests de dérivation d'alertes |
| Critères de validation | Filtres identiques ; alertes dérivées justes sur jeux de tests |
| Plan de rollback | Alias retirés |

## Étape 13 — Sécurité et paramètres

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`pages/security/*.jsx, `dashboard/src/next/pages/settings/*.jsx, `dashboard/src/next/pages/account/*.jsx |
| Fichiers à modifier | aucun |
| Routes concernées | `/security/*`, `/settings/*` |
| APIs / services impactés | `/auth/*`, `/acl`, `/groups`, `/pools`, `/jobs`, `/notifications`, `/update`, `/host/profile` |
| Fonctionnalités à protéger | Lignes DC « Permissions », « SSO », « Automation », « Notifications », modales Update et Account security |
| Améliorations intégrées | A-05 (si backend), A-19 (Run, Recover), A-27 (`use_tls`, `scope`), QR sûr (sans `dangerouslySetInnerHTML`) |
| Risques | Secrets (SSO, jetons, mot de passe) : jamais dans les logs |
| Tests | e2e `users`, `settings`, `automation`, `auth`, `coverage` |
| Critères de validation | Observateur : aucun spinner infini, aucun sondage après 403 |
| Plan de rollback | Alias retirés |

## Étape 14 — Responsive et accessibilité

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/src/next/`a11y/*.spec.ts (parcours clavier, axe par écran), captures 4 tailles × 2 thèmes |
| Fichiers à modifier | ajustements dans `next/` |
| Routes concernées | toutes |
| APIs / services impactés | aucun |
| Fonctionnalités à protéger | Toutes les fonctions sur tablette (820 px, bouton Inventory) et mobile (recherche, création de conteneur) |
| Améliorations intégrées | A-29, contraste renforcé, mouvement réduit |
| Risques | Régressions de mise en page sur chaînes longues (IPv6, UUID, chemins) |
| Tests | axe sur chaque écran, jeu de chaînes longues, parcours clavier complet |
| Critères de validation | 0 violation axe sérieuse ; aucun défilement horizontal de page ; cibles ≥ 44 px mobile |
| Plan de rollback | Correctifs isolés par écran |

## Étape 15 — Tests et non-régression

| Élément | Détail |
|---|---|
| Fichiers à créer | `dashboard/e2e/parity/*.spec.ts` complétés (une assertion par ligne de la matrice `01`), rapport de couverture de la matrice |
| Fichiers à modifier | `.github/workflows/*` seulement si une étape de contrôle est ajoutée (après accord) |
| Routes concernées | toutes |
| APIs / services impactés | aucun |
| Fonctionnalités à protéger | **Chaque ligne de la matrice** passe de « à conserver » à « vérifié » |
| Améliorations intégrées | Rapport « matrice ↔ tests » généré |
| Risques | Temps d'exécution des e2e : exécution en parallèle par domaine |
| Tests | lint, vérification de types (JSDoc + `tsc --checkJs` si adopté), Vitest, Playwright Chromium (Firefox/WebKit en option), build |
| Critères de validation | Tout vert ; matrice 100 % vérifiée ; captures validées |
| Plan de rollback | Sans objet |

## Étape 16 — Retrait contrôlé de l'ancienne UI

| Élément | Détail |
|---|---|
| Fichiers à créer | aucun |
| Fichiers à modifier | `dashboard/src/App.jsx` (nouvelle UI par défaut), suppression des anciens dossiers `layout/`, `panels/`, `wizard/` et composants morts **uniquement après** équivalence validée ; `docs/features.md`, `docs/webui-test-matrix.md` |
| Routes concernées | toutes (les alias restent une version) |
| APIs / services impactés | aucun |
| Fonctionnalités à protéger | Rien ne disparaît sans décision explicite : les lignes restées « À clarifier » gardent leur ancien accès |
| Améliorations intégrées | Nettoyage des jetons/classes morts, retrait de Google Fonts |
| Risques | Retrait prématuré : conditionné à la validation utilisateur écrite |
| Tests | Suite complète + essais manuels sur l'instance de dev |
| Critères de validation | Utilisateur valide l'équivalence ; publication d'abord sur `test`, puis `master` selon le flux habituel |
| Plan de rollback | `git revert` du commit de retrait (l'ancienne UI est dans l'historique) ; version précédente disponible par le mécanisme de mise à jour avec retour arrière |

## Ordre de publication proposé

Étapes 1–5 (fondations + Explorer) → revue et **validation intermédiaire** → étapes 6–13 par lots de domaine → 14–15 (qualité) → 16 (retrait). Les chantiers backend (A-30 d'abord : correctif de sécurité du terminal) se planifient en parallèle et indépendamment.

