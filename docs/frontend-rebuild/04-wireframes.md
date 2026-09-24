# 04 — Wireframes ASCII

> Phase 1 — documentation seulement. Ce sont des **maquettes de structure** (hiérarchie, données, états), pas des maquettes graphiques finales : les couleurs et espacements sont définis en `03-design-system.md`.
> **45 maquettes numérotées, 48 vues** (les n°9 et n°11 se déclinent en variantes). Les n°1–11 concernent l'**Inventory Explorer** (14 vues : layout, Server, Pool, recherche, sélection, nœud hors ligne, VM en erreur, tâche active, chargement/vide/hors ligne, laptop, tablette, mobile).

## Légende

| Symbole | Sens | | Symbole | Sens |
|---|---|---|---|---|
| ● | Running / en ligne / actif | | ■ | Stopped / inactif |
| ▲ | Warning / bloqué / dégradé | | ◆ | Error / plantée / critique |
| ○ | Offline / inconnu | | ⇄ | Migrating |
| ◌ | Tâche ou provisioning en cours | | ‖ | Paused |
| ▾ ▸ | Déplié / replié | | ▌ | Sélection (barre 2 px) |
| `[ … ]` | Bouton | | `[⧉]` | Copier |
| `☐ ☑` | Case | | `(●) ( )` | Radio |

Chaque état est porté par **forme + texte + couleur** (voir `03` §4). Les écrans hors de l'Explorer montrent la **zone de travail** ; le cadre (barre supérieure, rail, Explorer, dock) est celui du n°1.

## Capacités absentes du code (documentées, non inventées)

| Demande de la maquette | Constat | Traitement |
|---|---|---|
| Tags de VM | aucun champ ni endpoint | colonne et recherche par tag **prévues mais désactivées** ; amélioration A-11 (backend) |
| Événements (flux), Alertes (API) | pas d'endpoint ; seulement le journal d'audit (`alert_seuil_depasse`, `node_statut_change`, `ha_alert`) | vues **dérivées côté client** (n°30, n°31), marquées comme telles |
| Performance stockage, IOPS, latence | absents | non affichés ; message explicite |
| Débit/erreurs par interface, historique réseau | absents (débit instantané par interface seulement pour une VM locale) | non affiché pour les réseaux |
| Services d'un nœud, mode maintenance, redémarrage/arrêt de nœud | absents | non proposés (n°24, n°43) |
| Disque « utilisé » d'une VM, mémoire utilisée dans la liste | forcés à `null` par le client | « — » jusqu'à extension backend (A-12) |
| Capacité (CPU/RAM) des nœuds distants | non rapportée par l'API | « not reported » |
| Journaux système de l'hôte / de l'invité | absents | message dans le n°29 |
| Annulation d'une tâche | absente | non proposée |



---

## W01 — Layout desktop complet : navigation globale + Inventory Explorer + contenu

**Objectif utilisateur** : Situer l'utilisateur dans l'infrastructure et lui donner accès à toute ressource en un clic ou une frappe.

```text
┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ◈ Hyperlite   Datacenter › hl-devhub › db-01      [🔍 Find a node or VM      ⌘K]  ＋Create▾  ⟳12s 🔔2 EN◐ ▾admin│
├──┬───────────────────────────┬────────────────────────────────────────────────────────────────────────┤
│▣ │ INVENTORY   [Server|Pool] │ ● db-01                          Running · hl-devhub · 10.0.0.21       │
│  │ 🔍 Find a node or VM      │ [▶ Start][■ Stop ▾][↻ Restart][>_ Console][⋯]      ⓘ 1 task running      │
│▤ │ ▾ ◍ Datacenter  4n·37vm ▲2│────────────────────────────────────────────────────────────────────────│
│  │  ▾ ▭ hl-devhub  local  ● │ Summary  Metrics  Console  Hardware  Storage  Network  Snapshots  Tasks  Settings│
│▥ │   ▾ ▫ Virtual machines 12│────────────────────────────────────────────────────────────────────────│
│  │    ● db-01               │ ┌ CPU 34 % ─────┐ ┌ Memory 6.1/8 GiB ┐ ┌ Disk 41/80 GiB ┐ ┌ Net 2.1 MB/s ┐│
│▦ │    ● web-01              │ │ ▁▂▃▅▃▂▃▅▆▅    │ │ ▃▃▄▄▅▅▅▆▆▆       │ │ ▂▂▂▂▂▃▃▃▃▃     │ │ ▁▁▂▅▂▁▁▂▂    ││
│  │    ■ backup-01            │ └───────────────┘ └──────────────────┘ └────────────────┘ └──────────────┘│
│▧ │    ◆ win-2022             │ Identity  UUID 4f0c…9a1e  OS Debian 12  SSH user ops   IP 10.0.0.21 [⧉]    │
│  │   ▸ ▫ Containers      3   │ Recent tasks   ◌ backup_vm 42 %   ✓ start_vm 14:02   ✕ snapshot 13:51 [→]  │
│⚙ │   ▸ ▫ Storage pools   2   │                                                                        │
│  │   ▸ ▫ Networks        3   │                                                                        │
│  │  ▸ ▭ node-b          ○    │                                                                        │
├──┴───────────────────────────┴────────────────────────────────────────────────────────────────────────┤
│ ▴ Tasks (1)  Logs  Alerts (2)   ◌ backup_vm db-01 42 % · 00:01:12          filter ▾   ⌄ collapse      │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | 1 sélection (arbre) → 2 titre + état + actions (workspace) → 3 onglets → 4 contenu ; le calme visuel est la norme, seuls ▲ ◆ ○ ressortent. |
| Données (API réelles) | Toutes les données déjà chargées par `refreshAll` (`/dashboard`, `/nodes`, `/vms`, `/storage`, `/networks`) + `/containers`, `/tasks`, `/pools`. |
| Actions | Sélectionner une ressource ; chercher (`/` ou `⌘K`) ; créer ; changer langue/thème ; ouvrir tâches/logs/alertes ; replier l'Explorer (`Ctrl/Cmd+B`). |
| Fonctions existantes préservées | Arbre Datacenter/nœuds/VM/pools/réseaux, recherche, création VM/conteneur, cloche des tâches, thème, menu utilisateur, mise à jour, sécurité du compte, task log, `Ctrl/Cmd+B`. |
| Améliorations proposées | Breadcrumb cliquable, rail des 6 sections, dock persistant, indicateur de fraîcheur `⟳12s`, compteur d'alertes, langue EN/FR. |
| États possibles | Chargement, vide, erreur API, hors ligne, périmé, permission refusée (voir n°36–40). |
| Responsive | Voir `02` §9 : rail réduit et Explorer en tiroir sous 1024 px ; dock replié par défaut sur laptop. |
| Accessibilité | Landmarks `banner`, `navigation` (rail), `complementary` (Explorer), `main`, `region` (dock) ; lien « Skip to content » ; ordre de tabulation : barre → rail → Explorer → contenu → dock. |
| Confirmations et risques | Aucun (vue de navigation). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W02 — Inventory Explorer — mode Server

**Objectif utilisateur** : Parcourir la hiérarchie physique : Datacenter › nœuds › {VM, conteneurs, stockage, réseaux}.

```text
┌─ INVENTORY ────────────────────────┐
│ [■ Server] [  Pool ]      ⋯  ⇤     │   ⋯ = affichage : compteurs, IP, ID
│ 🔍 Find a node or VM            /  │
├────────────────────────────────────┤
│ ▾ ◍ Datacenter        4 nodes  ▲ 2 │
│   ▾ ▭ hl-devhub  [local]      ●    │
│     ▾ ▫ Virtual machines      12   │
│       ● db-01          10.0.0.21   │
│       ● web-01         10.0.0.22   │
│       ■ backup-01      stopped     │
│       ◆ win-2022       crashed     │
│       ⋮  (8 more)                  │
│     ▸ ▫ Containers             3   │
│     ▾ ▫ Storage pools          2   │
│         ● default        72 % used │
│         ▲ tank (zfs)     91 % used │
│     ▸ ▫ Networks               3   │
│   ▾ ▭ node-b                  ○    │
│       Offline since 14:02          │
│   ▸ ▭ node-c                  ●    │
├────────────────────────────────────┤
│ Updated 12 s ago · 37 VMs · 3 cont.│
└────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Datacenter (racine) → nœuds → catégories → ressources ; l'état le pire remonte en compteur sur chaque parent (▲ 2). |
| Données (API réelles) | `/nodes` (local + distants), `/vms` (avec `node`), `/containers`, `/storage`, `/networks`, `/host/…` pour le local. |
| Actions | Déplier/replier (chevron, `←→`), sélectionner (clic, Entrée), menu contextuel, basculer Server/Pool, options d'affichage. |
| Fonctions existantes préservées | Mode Server actuel : Datacenter › nœuds › pools de stockage puis VM ; sélection Datacenter/nœud/VM/pool ; ouverture par défaut de la profondeur < 2. |
| Améliorations proposées | Catégories explicites, conteneurs et réseaux ajoutés, compteurs, agrégat d'état, fraîcheur, IP en sous-libellé (option), un clic sur la ligne ne referme plus la branche. |
| États possibles | Chargement de branche, catégorie vide (« No containers »), nœud hors ligne, pool dégradé. |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. |
| Confirmations et risques | Aucun (lecture seule) ; les actions du menu contextuel reprennent les gardes de l'en-tête de ressource. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W03 — Inventory Explorer — mode Pool

**Objectif utilisateur** : Regrouper par appartenance réelle (pools de ressources) pour raisonner par projet ou équipe.

```text
┌─ INVENTORY ────────────────────────┐
│ [  Server] [■ Pool ]      ⋯  ⇤     │
│ 🔍 Find a node or VM            /  │
├────────────────────────────────────┤
│ ▾ ◍ Datacenter      3 pools   ▲ 1  │
│   ▾ ▤ production        5 members  │
│       ● db-01     hl-devhub        │
│       ● web-01    hl-devhub        │
│       ◆ win-2022  hl-devhub        │
│       ● api-02    node-c           │
│       ⇄ api-03    node-c → hl-devh…│
│   ▾ ▤ staging           2 members  │
│       ● stg-01    hl-devhub        │
│       ■ stg-02    hl-devhub        │
│   ▸ ▤ Unassigned       26 members  │
│                                    │
│ ⓘ Pool = resource pool (Security). │
│   Storage pools: switch to Server. │
└────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Datacenter → pools de ressources → membres (avec leur nœud en sous-libellé) ; « Unassigned » regroupe le reste. |
| Données (API réelles) | `/pools` + `/pools/{id}` membres (VM et conteneurs), `/vms`, `/containers`. **Décision D-01** (voir `02` §4.1). |
| Actions | Mêmes gestes que le mode Server ; un pool a un menu contextuel (ouvrir dans Security › Resource pools). |
| Fonctions existantes préservées | Mode Pool actuel (pools de stockage avec toutes les VM du nœud) : fonction conservée jusqu'à validation de D-01 ; recherche et sélection identiques. |
| Améliorations proposées | Vraie appartenance (plus de doublons ni de clés en collision), nœud de la VM visible, groupe « Unassigned », plus de fuite de l'id interne « local ». |
| États possibles | Aucun pool défini → vide avec « Create a resource pool » (admin) ; pool sans membre ; membre supprimé. |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. |
| Confirmations et risques | Aucun ; l'appartenance à un pool conditionne les ACL : le libellé rappelle « ACL apply to pool members ». |
| Confirmation de capacité | Appartenance réelle confirmée (`/pools`) ; ancien sens « pool de stockage » documenté en `02` §4.1. |

---

## W04 — Inventory Explorer — recherche active

**Objectif utilisateur** : Trouver un nœud ou une VM par nom, IP, OS, état ou nœud, sans perdre le contexte hiérarchique.

```text
┌─ INVENTORY ────────────────────────┐
│ [■ Server] [  Pool ]      ⋯  ⇤     │
│ 🔍 db▌                        ✕ /  │
│ 3 results                          │  ← aria-live
├────────────────────────────────────┤
│ ▾ ◍ Datacenter                     │
│   ▾ ▭ hl-devhub                    │
│     ▾ ▫ Virtual machines           │
│       ● [db]-01        10.0.0.21   │  ← sélectionné ▌
│       ● [db]-replica   10.0.0.23   │
│   ▾ ▭ node-c                       │
│     ▾ ▫ Virtual machines           │
│       ■ old-[db]       stopped     │
│                                    │
│ ↵ open first · ↑↓ move · Esc clear │
└────────────────────────────────────┘
       cas sans résultat :
│ 🔍 zzz▌                       ✕    │
│ 0 results                          │
│  No node or VM matches “zzz”.      │
│  Search covers name, IP, OS, state,│
│  node.        [ Clear search ]     │
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Champ → compteur → arbre élagué qui garde le chemin des parents et surligne la correspondance. |
| Données (API réelles) | Index construit côté client à partir des données déjà chargées (nom, IP, OS, état, nœud, pool) ; **aucun appel réseau à la frappe**. |
| Actions | Taper (debounce 120 ms), `↑↓`, Entrée = ouvrir le premier, Échap = vider, `⌘K` = palette avec actions. |
| Fonctions existantes préservées | Recherche d'en-tête actuelle : sous-chaîne sur le libellé, élagage des branches non concordantes. |
| Améliorations proposées | Surbrillance, compteur, message « aucun résultat », recherche par IP/OS/état/nœud, insensible aux accents, chemin hiérarchique conservé, champ toujours accessible (aujourd'hui masqué sous 640 px), placeholder non tronqué. |
| États possibles | Actif avec résultats ; sans résultat ; données en cours de chargement (« Searching loaded items — 2 nodes still loading »). |
| Responsive | Mobile : la loupe ouvre un champ plein écran ; résultats en liste plate avec chemin. |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. Le champ a `role=searchbox` et `aria-controls` vers l'arbre. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W05 — Inventory Explorer — VM sélectionnée

**Objectif utilisateur** : Montrer où l'on est et offrir les actions rapides sur la ressource sélectionnée.

```text
┌─ INVENTORY ────────────────────────┐
│ 🔍 Find a node or VM            /  │
├────────────────────────────────────┤
│ ▾ ◍ Datacenter                     │
│   ▾ ▭ hl-devhub  [local]      ●    │
│     ▾ ▫ Virtual machines      12   │
│       ● web-01                     │
│ ▌     ● db-01         ◌ 42 %    ⋯  │  ← ligne sélectionnée + tâche + menu
│       ■ backup-01                  │
│                                    │
│  clic droit / ⋯ :                  │
│  ┌──────────────────────────┐      │
│  │ ▶ Start          (disabled: running)│
│  │ ■ Stop                     │      │
│  │ ↻ Restart                  │      │
│  │ >_ Console                 │      │
│  │ ⧉ Copy link / IP           │      │
│  │ ⇄ Migrate…                 │      │
│  │ ─────────────────────────  │      │
│  │ ✕ Delete…        (danger)  │      │
│  └──────────────────────────┘      │
└────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Ligne sélectionnée (barre 2 px + fond + semi-gras) → menu contextuel aligné sur l'en-tête de ressource. |
| Données (API réelles) | Même liste d'actions et mêmes règles d'activation que l'onglet Summary (`etat`, rôle/ACL). |
| Actions | Clic droit, `Shift+F10`, bouton `⋯` ; navigation `↑↓` dans le menu ; Entrée exécute ; les actions destructrices ouvrent une confirmation. |
| Fonctions existantes préservées | Actions VM du Summary/menu d'actions (start, stop, restart, migrate, delete, console) ; sélection par nom. |
| Améliorations proposées | Menu contextuel (n'existe pas aujourd'hui), raccourcis affichés **réellement implémentés**, action désactivée avec raison (« Running — stop it first »). |
| États possibles | Action indisponible (désactivée + raison), action en cours (spinner), refus 403 (toast explicite). |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. Le menu est un `role=menu` Radix ; `aria-haspopup=menu` sur la ligne. |
| Confirmations et risques | Delete/force stop : confirmation renforcée (voir n°41–42) ; Restart aujourd'hui **brutal** : la confirmation nomme l'impact « hard power cycle ». |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W06 — Inventory Explorer — nœud hors ligne

**Objectif utilisateur** : Signaler un nœud injoignable sans faire croire que ses VM sont arrêtées.

```text
┌─ INVENTORY ────────────────────────┐
│ ▾ ◍ Datacenter      4 nodes  ▲ 1   │
│   ▾ ▭ hl-devhub  [local]      ●    │
│   ▾ ▭ node-b   ○ Offline  14:02    │  ← forme ○ barrée + texte + gris
│       ○ 6 VMs — state unknown      │
│       ○ web-b1     last: running   │
│       ○ web-b2     last: running   │
│       ⓘ Data from 14:02 (stale)     │
│   ▸ ▭ node-c                  ●    │
├────────────────────────────────────┤
│ workspace : bandeau ambre ▲        │
│ ┌────────────────────────────────┐ │
│ │ ▲ node-b is offline since 14:02│ │
│ │   Node poller: 3 failed checks │ │
│ │   [ Diagnose ] [ View tasks ]  │ │
│ └────────────────────────────────┘ │
└────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Nœud gris hors ligne (pas rouge : l'absence de donnée n'est pas une panne confirmée) → VM en « état inconnu » avec dernier état connu → bandeau d'action. |
| Données (API réelles) | `/nodes` (`statut: hors_ligne`, `derniere_verification`), dernier `/vms?node=` mis en cache ; événement `node_statut_change` du journal. |
| Actions | Diagnostiquer (ouvre le nœud › Compatibility), voir les tâches, ouvrir la HA si applicable. |
| Fonctions existantes préservées | État `hors_ligne` du nœud (aujourd'hui mappé sur `erreur`) ; les VM du nœud restent listées. |
| Améliorations proposées | Distinction hors ligne / inconnu / erreur, dernière vérification, VM marquées « périmé », lien vers le diagnostic ; HA « Recover » proposé (confirmation) si activé. |
| États possibles | Hors ligne, inconnu, reconnexion en cours, retour en ligne (toast de succès). |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. Le bandeau a `role=status` (pas `alert` : non bloquant). |
| Confirmations et risques | La récupération HA reste **manuelle et confirmée** (pas de fencing : le backend le dit). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W07 — Inventory Explorer — VM en erreur

**Objectif utilisateur** : Repérer immédiatement une VM en défaut (plantée, bloquée) et remonter à sa cause.

```text
┌─ INVENTORY ────────────────────────┐
│ ▾ ◍ Datacenter        ▲ 1  ◆ 1     │
│   ▾ ▭ hl-devhub                ◆   │  ← parent porte le pire état enfant
│     ▾ ▫ Virtual machines       ◆   │
│       ● web-01                     │
│ ▌     ◆ win-2022    crashed        │  ← forme ◆ + texte ; pas de fond rouge
│       ▲ web-02      blocked        │
├────────────────────────────────────┤
│ workspace                          │
│ ◆ win-2022      Crashed            │
│ ┌ ◆ The guest crashed at 13:58 ───┐│
│ │ Last task: start_vm ✕ (13:51)   ││
│ │ "unsupported configuration: …"  ││
│ │ [ View task ] [ Open logs ]     ││
│ │ [ Force stop ] [ Start ]        ││
│ └─────────────────────────────────┘│
└────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Compteur agrégé sur les parents → ligne ◆ → détail avec cause → actions de récupération. |
| Données (API réelles) | `etat` (`plante`, `bloque`), dernière tâche en échec (`/tasks?cible=&statut=echec`), `erreur` de la tâche. |
| Actions | Ouvrir la tâche, ouvrir les logs, démarrer, arrêt forcé (confirmation renforcée). |
| Fonctions existantes préservées | États VM affichés aujourd'hui par couleur seule ; « seul `actif` compte comme démarré ». |
| Améliorations proposées | Forme + texte, agrégat sur les parents, tri « problèmes d'abord », erreur de tâche lisible. |
| États possibles | Plantée, bloquée, échec de démarrage, erreur de migration. |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. |
| Confirmations et risques | Force stop = perte de données non écrites : confirmation renforcée (n°42). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W08 — Inventory Explorer — ressource avec tâche active

**Objectif utilisateur** : Montrer qu'une opération est en cours sur une ressource et empêcher les actions conflictuelles.

```text
┌─ INVENTORY ────────────────────────┐
│     ● web-01                       │
│     ◌ db-01      backup 42 %       │  ← anneau (statique si mouvement réduit)
│     ⇄ api-03     migrating 67 %    │
│     ● win-2022   ◌ installing      │  ← provisioning
│                                    │
│  info-bulle sur db-01 :            │
│  ┌──────────────────────────────┐  │
│  │ backup_vm · 42 % · 00:01:12  │  │
│  │ started 14:01 by admin       │  │
│  │ [ Open task ]                │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
 dock : ▴ Tasks (3)  ◌ backup_vm db-01 42 %
        ⇄ migrate_vm api-03 67 %   ◌ auto_install win-2022
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | L'anneau attire l'œil sans alerter ; l'info-bulle et le dock donnent le détail. |
| Données (API réelles) | `/tasks?statut=en_cours` (sondage 8 s) corrélé à la ressource par `cible` ; provisioning `/vms/{n}/provisioning` (6 s tant qu'en cours) ; progression réelle pour backup/export/migration/job/update, **indéterminée** pour snapshot/create/clone (le backend ne fournit pas de pourcentage). |
| Actions | Ouvrir la tâche ; les actions incompatibles se désactivent avec raison (« Migration in progress »). |
| Fonctions existantes préservées | Suivi des tâches locales de session + provisioning + migration. |
| Améliorations proposées | Corrélation ressource ↔ tâche (aujourd'hui absente), progression déterminée ou « indeterminate » explicite, anneau remplacé par « 42 % » si mouvement réduit. |
| États possibles | En cours, terminée (✓ 5 s puis disparaît), échouée (◆ persistante), ancienne (> 15 min : « stale? »). |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. Progression : `role=progressbar` + `aria-valuenow`, annonce à chaque palier de 25 %. |
| Confirmations et risques | Tâches asynchrones sans `task_id` renvoyé (backup, restore, export, job) : la corrélation par type + cible peut être ambiguë. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W09a — Inventory Explorer — chargement

**Objectif utilisateur** : Éviter l'écran vide ou le saut de mise en page pendant le premier chargement.

```text
┌─ INVENTORY ────────────────────────┐
│ [■ Server] [  Pool ]               │
│ 🔍 Find a node or VM               │
├────────────────────────────────────┤
│ ▾ ◍ Datacenter                     │
│   ▾ ▭ ▒▒▒▒▒▒▒▒▒                    │
│       ▒▒▒▒▒▒▒▒▒▒▒▒                 │
│       ▒▒▒▒▒▒▒▒                     │
│       ▒▒▒▒▒▒▒▒▒▒                   │
│   ▸ ▭ ▒▒▒▒▒▒                       │
│                                    │
│ Loading inventory…  (2 of 3 nodes) │
└────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Squelette à la forme de l'arbre ; les nœuds répondent un par un. |
| Données (API réelles) | `/nodes` d'abord (rapide), puis `/vms?node=` par nœud en parallèle. |
| Actions | Attendre ; la recherche est déjà utilisable sur ce qui est chargé. |
| Fonctions existantes préservées | `LoadingState` avec message après 10 s. |
| Améliorations proposées | Chargement progressif par nœud (aujourd'hui : écran « Loading the infrastructure… » global qui démonte tout). |
| États possibles | > 10 s : « Taking longer than usual — Retry ». |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `aria-busy=true` sur l'arbre ; annonce « Loading inventory ». |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W09b — Inventory Explorer — vide

**Objectif utilisateur** : Guider un démarrage à zéro.

```text
┌─ INVENTORY ────────────────────────┐
│ ▾ ◍ Datacenter        1 node       │
│   ▾ ▭ hl-devhub  [local]      ●    │
│     ▾ ▫ Virtual machines       0   │
│        No virtual machines yet     │
│        [ ＋ Create VM ]            │
│     ▾ ▫ Containers             0   │
│        No containers               │
└────────────────────────────────────┘
   observateur : « No virtual machines » (sans bouton)
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Message court + une action (si le rôle le permet). |
| Données (API réelles) | Réponses vides des endpoints. |
| Actions | Créer une VM (admin). |
| Fonctions existantes préservées | Aucun état vide dédié dans l'arbre actuel. |
| Améliorations proposées | État vide utile par catégorie. |
| États possibles | Vide pour admin / vide pour observateur / vide car filtré (« Clear search »). |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W09c — Inventory Explorer — hors ligne / erreur API

**Objectif utilisateur** : Dire clairement que l'API est injoignable et ce que l'on voit encore.

```text
┌─ INVENTORY ────────────────────────┐
│ ▲ Cannot reach the server          │
│   Last updated 14:03:21 (2 min ago)│
│   Retrying in 8 s…  [ Retry now ]  │
├────────────────────────────────────┤
│ ▾ ◍ Datacenter        ○ stale      │
│   ▾ ▭ hl-devhub                    │
│       ○ db-01     last: running    │
│       ○ web-01    last: running    │
│ (données grisées, actions désactivées)
└────────────────────────────────────┘
   erreur 500 : « ◆ Inventory failed to load — HTTP 500 [Copy details] [Retry] »
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Bandeau → arbre grisé et marqué périmé → actions désactivées. |
| Données (API réelles) | Dernier état en mémoire ; erreur normalisée du client (jamais `[object Object]`). |
| Actions | Réessayer, copier le détail, attendre la reconnexion automatique (recul exponentiel 2→30 s). |
| Fonctions existantes préservées | Erreur « Error: … » permanente aujourd'hui (jamais effacée). |
| Améliorations proposées | Effacement automatique au rétablissement, données périmées visibles, arrêt du sondage inutile. |
| États possibles | Réseau coupé, 401 (session expirée → connexion sans perdre l'URL), 403, 500, réponse non JSON. |
| Responsive | ≥ 1440 px : panneau fixe 288 px ; 1024–1439 px : 240 px ; < 1024 px : tiroir gauche ouvert par le bouton « Inventory » (voir n°11). |
| Accessibilité | Bandeau `role=alert` (assertive) ; état périmé annoncé une seule fois. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W10 — Inventory Explorer — laptop compact

**Objectif utilisateur** : Garder l'Explorer utile sur 1280×720 sans écraser le contenu.

```text
┌─────────────────────────────────────────────────────────────────────┐
│ ◈ Hyperlite  [🔍 Find a node or VM  ⌘K]  ＋ ⟳ 🔔2 EN ◐ ▾            │
├──┬───────────────────┬──────────────────────────────────────────────┤
│▣ │ [Server|Pool]  ⇤  │ ● db-01  Running        [▶][■▾][↻][>_][⋯]    │
│▤ │ 🔍 …              │ Summary Metrics Console Hardware Storage ▸   │
│▥ │ ▾ Datacenter   ▲2 │──────────────────────────────────────────────│
│▦ │  ▾ hl-devhub   ●  │ (contenu ; tableaux : colonnes secondaires   │
│▧ │   ▾ VMs      12   │  masquées via le bouton « Columns »)         │
│⚙ │    ● db-01        │                                              │
│  │    ● web-01       │                                              │
│  │    ◆ win-2022     │                                              │
├──┴───────────────────┴──────────────────────────────────────────────┤
│ ▸ Tasks (1) · Alerts (2)                        (dock replié, 40 px)│
└─────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Explorer 240 px, lignes 24 px, sous-libellés masqués ; onglets déroulants (`▸`) ; dock replié. |
| Données (API réelles) | Identiques. |
| Actions | Comme n°1 ; densité « Compact » proposée par défaut à cette taille. |
| Fonctions existantes préservées | Ctrl/Cmd+B conservé. |
| Améliorations proposées | Densité, colonnes configurables, dock replié par défaut. |
| États possibles | Idem n°2. |
| Responsive | Point d'arrêt 1024–1439 px ; hauteur < 760 px : dock masqué. |
| Accessibilité | `role=tree`/`treeitem`/`group`, `aria-level`, `aria-expanded`, `aria-selected` ; tabindex itinérant ; nom accessible complet de chaque ligne (« vm-01, virtual machine, running, on node hl-devhub ») ; état = forme + texte masqué ; annonce `aria-live=polite` du nombre de résultats. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W11a — Inventory Explorer — tablette (820 px) en tiroir

**Objectif utilisateur** : Rendre l'Explorer accessible sur tablette (aujourd'hui : pas de bouton de menu à 820 px).

```text
┌───────────────────────────────────────────┐
│ ☰ Inventory   Hyperlite      🔍  ＋  🔔 ◐ │
├───────────────────────────────────────────┤
│ ● db-01                  Running          │
│ [▶][■▾][↻][>_][⋯]                         │
│ Summary Metrics Console Hardware ▸        │
│ ─────────────────────────────────────────│
│ …                                         │
└───────────────────────────────────────────┘
 ☰ ouvre : ┌───────────────────────┬─ (voile) ─┐
           │ INVENTORY   ✕         │           │
           │ [Server|Pool]         │           │
           │ 🔍 Find a node or VM  │           │
           │ ▾ Datacenter …        │           │
           └───────────────────────┴───────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Bouton ☰ Inventory toujours visible ; le tiroir recouvre le contenu et se ferme à la sélection. |
| Données (API réelles) | Identiques. |
| Actions | Ouvrir/fermer, sélectionner (ferme le tiroir), chercher. |
| Fonctions existantes préservées | Sidebar shadcn (`Sheet`) déjà utilisée sur mobile. |
| Améliorations proposées | Bouton toujours présent, focus piégé, fermeture par Échap et clic sur le voile. |
| États possibles | Ouvert, fermé. |
| Responsive | Tablette portrait 820 px ; paysage → panneau fixe 240 px. |
| Accessibilité | Le bouton a `aria-expanded` et `aria-controls` ; focus piégé dans le tiroir ; retour du focus au bouton à la fermeture. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W11b — Inventory Explorer — mobile (390 px) en tiroir

**Objectif utilisateur** : Consulter et agir en mobilité.

```text
┌─────────────────────────┐
│ ☰  Hyperlite    🔍  🔔  │
├─────────────────────────┤
│ ● db-01     Running     │
│ [▶ Start] [>_ Console] ⋯│
│ ┌ CPU 34 % ┐┌ RAM 76 %┐ │
│ Summary ▸ Metrics ▸ …   │
├─────────────────────────┤
│ Overview VMs Activity ⋯ │  ← navigation basse (4 entrées)
└─────────────────────────┘
  🔍 : champ plein écran + résultats plats
     « db-01 · hl-devhub › VMs »
  ☰ : tiroir 85 % de largeur
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Actions sûres en avant ; actions destructrices dans `⋯` (dialogue plein écran). |
| Données (API réelles) | Identiques. |
| Actions | Rechercher (loupe), ouvrir le tiroir, agir. |
| Fonctions existantes préservées | Tiroir actuel à 390 px ; recherche et « Create container » masquées sous 640 px (à corriger). |
| Améliorations proposées | Recherche accessible, cibles ≥ 44 px, navigation basse. |
| États possibles | Ouvert, fermé, recherche plein écran. |
| Responsive | < 768 px : tableaux → cartes. |
| Accessibilité | Cibles ≥ 44 px ; zoom 200 % sans perte. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W12 — Overview

**Objectif utilisateur** : Répondre en 5 secondes : l'infrastructure est-elle saine, qu'est-ce qui a changé, où investiguer ?

```text
┌ Overview ───────────────────────────────────────────────────────────────  ⟳ updated 12 s ago ┐
│ ● Healthy — 4 nodes online · 0 incidents            (si problème : ▲/◆ bandeau unique + lien)  │
├──────────────┬──────────────┬──────────────┬──────────────────────────────────────────────────┤
│ Nodes        │ VMs          │ Containers   │ Capacity                                         │
│ ● 3  ○ 1     │ ● 31 ■ 5 ◆ 1 │ ● 2  ■ 1     │ CPU  ▇▇▇▇▁▁▁▁▁▁ 38 %    (local host only*)      │
│              │ ‖ 0  ⇄ 0     │              │ RAM  ▇▇▇▇▇▇▁▁▁▁ 61 %                            │
│              │              │              │ Disk ▇▇▇▇▇▇▇▇▇▁ 91 % ▲ tank                     │
├──────────────┴──────────────┴──────────────┴──────────────────────────────────────────────────┤
│ Needs attention (3)                                              │ Running tasks (2)          │
│  ◆ win-2022 crashed 13:58                              [Open]    │  ◌ backup_vm db-01   42 %  │
│  ▲ pool tank 91 % used                                 [Open]    │  ⇄ migrate api-03    67 %  │
│  ○ node-b offline since 14:02                          [Open]    │ Recent activity            │
│                                                                  │  14:02 admin start_vm web-01 ✓│
├──────────────────────────────────────────────────────────────────┴────────────────────────────┤
│ CPU history (24 h) ▁▂▃▅▃▂▃▅▆▅▃▂▁   * remote nodes: capacity not reported by the API           │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Santé globale → KPI par type → « Needs attention » → tâches → activité → historique. |
| Données (API réelles) | `/dashboard`, `/nodes`, `/vms`, `/containers`, `/storage`, `/tasks`, `/audit` (admin), `/host/metrics/history` (local) ; **capacité des nœuds distants absente de l'API** (valeurs `null` aujourd'hui) → affichée « not reported ». |
| Actions | Ouvrir un problème, une tâche, un nœud ; changer la plage du graphique. |
| Fonctions existantes préservées | Onglet Summary + Recent activity du Datacenter (tuiles, jauges, tâches récentes 10 s, métriques 15 s, « View the whole journal »). |
| Améliorations proposées | « Needs attention » (dérivé côté client), compteurs par état avec formes, fraîcheur, honnêteté sur les données non disponibles, lien du journal corrigé (mène au Journal, plus à Recent activity). |
| États possibles | Chargement (squelettes), vide (aucune VM), hors ligne, périmé, 403 sur l'audit (bloc masqué avec explication). |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W13 — Liste des VM

**Objectif utilisateur** : Table de travail : trouver, comparer et agir sur plusieurs VM.

```text
┌ Virtual machines  37 (12 shown)   [+ Create VM]                                     ⟳ 12 s ┐
│ 🔍 filter…   State: Running ×  Node: hl-devhub ×   [Clear all]        Columns ▾  Density ▾  ⤓ CSV│
├──┬───┬───────────┬───────────┬────────┬──────┬────────┬───────────────┬──────────┬───────┬────┤
│☐ │St │ Name ▲    │ Node      │ OS     │vCPU  │ RAM    │ IP            │ Uptime   │ Task  │ ⋯  │
├──┼───┼───────────┼───────────┼────────┼──────┼────────┼───────────────┼──────────┼───────┼────┤
│☐ │ ◆ │ win-2022  │ hl-devhub │ Win 22 │    4 │ 8 GiB  │ —             │ —        │       │ ⋯  │
│☐ │ ▲ │ web-02    │ node-c    │ Debian │    2 │ 4 GiB  │ 10.0.0.31     │ 3d 4h    │       │ ⋯  │
│☑ │ ● │ db-01     │ hl-devhub │ Debian │    4 │ 8 GiB  │ 10.0.0.21     │ 12d      │◌ 42 % │ ⋯  │
│☑ │ ● │ web-01    │ hl-devhub │ Ubuntu │    2 │ 4 GiB  │ 10.0.0.22     │ 12d      │       │ ⋯  │
│☐ │ ■ │ backup-01 │ hl-devhub │ Debian │    1 │ 2 GiB  │ —             │ —        │       │ ⋯  │
├──┴───┴───────────┴───────────┴────────┴──────┴────────┴───────────────┴──────────┴───────┴────┤
│ 2 selected   [▶ Start] [■ Stop] [↻ Restart] [⋯ More]   ·  Esc clears           1–12 of 37  ‹ ›│
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Filtres → tableau (problèmes d'abord par défaut) → barre d'actions groupées. |
| Données (API réelles) | `/vms` (toutes les colonnes existantes : nom, état, vCPU, mémoire, IP, OS, uptime) + `/containers` ; **colonne Disque : « — » tant que le backend ne la fournit pas** (aujourd'hui forcée à `--`) ; ID = `id` libvirt (abandonné par `mapVm`, à récupérer) ; **Tags absents du backend** (documenté) ; « dernière activité » dérivable de `/tasks`. |
| Actions | Filtrer, trier, choisir les colonnes, sélectionner plusieurs lignes, actions groupées (Start/Stop/Restart/Delete), exporter, ouvrir une VM (clic sur le nom), menu de ligne. |
| Fonctions existantes préservées | VMTable et VMCard (Start/Stop/actions groupées, tiroir de détail au double clic, `VMActionMenu`), recherche du nœud. |
| Améliorations proposées | Colonnes configurables, densité, tri « problèmes d'abord », virtualisation (1 000 VM), export CSV, état de la vue dans l'URL, **droits appliqués aux boutons** (aujourd'hui non filtrés par rôle). |
| États possibles | Chargement (squelette de lignes), 0 VM, 0 résultat de filtre, erreur partielle (un nœud ne répond pas), permission refusée sur une action. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Actions groupées destructrices : une seule confirmation listant chaque VM ; Stop groupé demande confirmation (aujourd'hui aucune). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W14 — Détail VM — Summary

**Objectif utilisateur** : Comprendre l'état d'une VM et agir dessus.

```text
┌ ● db-01 · Running · hl-devhub                       [▶][■ Stop ▾][↻ Restart][>_ Console][⋯]┐
│ Summary  Metrics  Console  Hardware  Storage  Network  Snapshots  Tasks & Logs  Settings    │
├─────────────────────────────────────┬─────────────────────────────────────────────────────┤
│ Identity                            │ Live (4 s)                                          │
│  UUID   4f0c-…-9a1e  [⧉]            │  CPU  ▁▂▃▅▃▂▃▅▆▅  34 %                              │
│  OS     Debian 12                   │  RAM  ▃▃▄▄▅▅▅▆▆▆  6.1 / 8 GiB                        │
│  IP     10.0.0.21    [⧉]            │  Disk read 1.2 MB/s  write 0.4 MB/s                  │
│  SSH    ops@10.0.0.21 [⧉]           │  Net  ↓ 2.1 MB/s  ↑ 0.3 MB/s                         │
│  Uptime 12d 3h                      ├─────────────────────────────────────────────────────┤
│  Storage ZFS: no                    │ Protection                                          │
├─────────────────────────────────────┤  HA: off [Enable…]   Auto-cleanup: off              │
│ Recent tasks                        │  Last backup: 02:00 ✓ (cold)   Next: tomorrow 02:00 │
│  ◌ backup_vm  42 %        [→]       │  Snapshots: 3                                       │
│  ✓ start_vm   14:02                 ├─────────────────────────────────────────────────────┤
│  ✕ create_snapshot 13:51 [→]        │ Danger zone      [ Clone… ] [ To template… ] [ Delete… ]│
└─────────────────────────────────────┴─────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | En-tête (état + actions) → identité → temps réel → protection → tâches → zone dangereuse. |
| Données (API réelles) | `GET /vms/{n}`, `/metrics` (4 s), `/provisioning` (6 s si en cours), `/backups`, `/backup-schedule`, `/snapshots`, `/ha`, `/auto-cleanup`, `/tasks?cible=`. |
| Actions | Start/Stop/Force stop/Restart, console, clone, conversion en modèle, suppression, migration, HA on/off, auto-nettoyage on/off. |
| Fonctions existantes préservées | Toutes les actions du VMSummaryTab avec **les mêmes conditions d'activation** (Start `etat≠actif` ; Stop/Restart `actif` ; Clone/To template/Delete `≠actif` ; Migrate `actif` + ≥ 1 autre nœud en ligne). |
| Améliorations proposées | Raison des boutons désactivés, copie IP/UUID, confirmation du Restart (l'existant fait un arrêt brutal), formulaire de clone à la place de `window.prompt`, tâches liées. |
| États possibles | Arrêtée, en provisioning (barre par phase), en migration, en erreur, nœud distant (bandeau « some operations local only »), verrouillée par tâche. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. |
| Confirmations et risques | Restart = « hard power cycle » ; Delete irréversible ; Migrate à chaud (voir n°44). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W15 — Détail VM — Metrics

**Objectif utilisateur** : Diagnostiquer une pression CPU/RAM/disque/réseau sur une période.

```text
┌ ● db-01 · Metrics              Range: (1h) 24h 7d 30d     ⏸ Pause live    ⤓ table view ┐
├────────────────────────────────────────────────────────────────────────────────────────┤
│ CPU %                                                                                  │
│ 100┤                                                                                   │
│  50┤        ▃▅▆▅▃            ▂▃▃▂                                                      │
│   0┼──────────────────────────────────────────  14:00  14:15  14:30  14:45  15:00      │
│ Memory used / allocated (GiB)      ──── used ─ ─ allocated (8)                         │
│ Disk throughput  ▪ read  ▲ write   (KB/s)      Network  ● rx  ■ tx  (KB/s)             │
│ ⓘ Samples every 10–30 s (1 h) or hourly average (24 h+). Local running VMs only.       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Sélecteur de plage → 4 graphiques alignés dans le temps → notes de fiabilité. |
| Données (API réelles) | `/vms/{n}/metrics/history?range=1h\|24h\|7j\|30j`, `/vms/{n}/metrics` (direct 4 s). **Absents du backend** : IOPS, latence, historique par disque/interface, VM d'un nœud distant, conteneurs. |
| Actions | Changer la plage, pause/reprise du direct, survol pour valeurs exactes, afficher en tableau. |
| Fonctions existantes préservées | Carte d'historique du Summary (`MetricsHistoryCard`, `MetricChart`, `RangeToggle` mort) ; direct de 120 points. |
| Améliorations proposées | Onglet dédié, marqueurs de forme et motifs (pas la couleur seule), tableau de secours, état « pas encore assez de données » (aujourd'hui courbe vide), correction du rechargement à chaque rendu. |
| États possibles | Chargement, VM arrêtée (« No live data while stopped » + historique conservé), pas de données, erreur, distante (« not available for remote nodes »). |
| Responsive | ≥ 1024 px : grille 2×2 ; < 1024 px : pile ; échelles horizontales défilables. |
| Accessibilité | Graphique = `role=img` + résumé texte + tableau ; boutons de plage = `role=radiogroup` ; pause = `aria-pressed`. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W16 — Détail VM — Console

**Objectif utilisateur** : Ouvrir un accès graphique ou terminal à la VM en sécurité.

```text
┌ ● db-01 · Console ───────────────────────────────────────────────────────────────────────┐
│ ┌ Graphical console (VNC) ─────────────┐  ┌ SSH terminal ───────────────────────────┐    │
│ │ Opens in a separate window.          │  │ Opens a shell as ops@10.0.0.21.         │    │
│ │ Requires: VM running · vm.console    │  │ Requires: VM running · SSH user set     │    │
│ │ [ Open console ↗ ]                   │  │ [ Open terminal ↗ ]                     │    │
│ └──────────────────────────────────────┘  └─────────────────────────────────────────┘    │
│ ⓘ A one-time ticket (valid 30 s) is created when you click. Pop-up blocked? [ Open here ]│
└──────────────────────────────────────────────────────────────────────────────────────────┘
  fenêtre autonome /console/db-01 :
┌ db-01 · VNC · connected ───────── [⛶ Fullscreen] [⌨ Ctrl+Alt+Del] [⧉ Paste] [✕ Disconnect]┐
│                            (écran de la VM)                                              │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Deux cartes d'accès (VNC / terminal) → fenêtre autonome avec barre d'état. |
| Données (API réelles) | `POST /vms/{n}/console-ticket`, `POST /vms/{n}/terminal-ticket`, WebSocket `/vms/{n}/console\|terminal?ticket=` ; sonde `GET /vms/{n}` 5 s. |
| Actions | Ouvrir la console, ouvrir le terminal, plein écran, coller, déconnecter, reconnecter. |
| Fonctions existantes préservées | Lanceur `VMConsoleTab`, `ConsoleWindow` (mode `?mode=terminal`), noVNC/xterm ; route `/console/:name` conservée. |
| Améliorations proposées | État de connexion visible (aujourd'hui une déconnexion VNC reste silencieuse), reconnexion manuelle avec nouveau ticket, repli « Open here » si pop-up bloqué (aujourd'hui `window.open` noopener renvoie null), raison de l'indisponibilité. |
| États possibles | VM arrêtée, ticket expiré (4401), WebSocket perdu, refus de permission. |
| Responsive | Sur mobile la console s'ouvre en pleine page. |
| Accessibilité | Le canevas VNC est marqué comme application ; entrée clavier capturée avec sortie documentée (`Ctrl+Alt+Échap`) ; alternative textuelle : terminal SSH. |
| Confirmations et risques | **Sécurité (constat B1)** : le terminal SSH d'une VM n'exige côté backend que `vm.console`, que reçoit tout observateur ; le bouton reste soumis à une règle explicite visible en attendant un correctif backend. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W17 — Détail VM — Hardware

**Objectif utilisateur** : Modifier vCPU, mémoire, CD-ROM et limites, à chaud quand c'est possible.

```text
┌ ● db-01 · Hardware ─────────────────────────────────────────────────────────────────────┐
│ Compute                                                                                 │
│  vCPU     [ 4 ▾ ]  1–16 (host 16)        Memory  [ 8192 ] MiB  512–32768 (host 31 GiB)  │
│  ⓘ Changes apply live only if the guest supports hot-plug; otherwise after restart.     │
│                                              [ Revert ] [ Apply changes ]               │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ CD-ROM   sata0  [ debian-12.iso ▾ ]  [ Eject ]     Drivers ISO [ virtio-win.iso ▾ ]      │
│ Limits (advanced ▸)  vCPU max · memory max · policy: limites | surallocation | libre     │
│ Disk controller  virtio  (read-only after creation)                                     │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Calcul → média amovible → limites avancées. |
| Données (API réelles) | `PATCH /vms/{n}` (vCPU, mémoire), `GET/PUT /vms/{n}/limits`, `POST /vms/{n}/cdrom` (insérer/éjecter), `GET /host/limits`. |
| Actions | Changer vCPU/mémoire, insérer/éjecter un ISO, monter les pilotes, régler les limites. |
| Fonctions existantes préservées | VMHardwareTab, VMOptionsTab (limites), `DriversIsoControl`, `OverallocationNote`. |
| Améliorations proposées | Bornes visibles, note de sur-allocation affichée, « Revert », classes CSS `.input` définies enfin, confirmation d'un changement qui redémarre. |
| États possibles | Arrêtée (édition à froid), en marche (hot-plug), refus de limite (409/422 lisibles), verrou par tâche. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. Champs numériques avec unités et `aria-describedby` des bornes. |
| Confirmations et risques | Réduction de RAM à chaud : avertissement ; disque non redimensionnable (aucun endpoint : documenté). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W18 — Détail VM — Storage

**Objectif utilisateur** : Gérer les disques attachés, en attacher ou détacher un, exporter.

```text
┌ ● db-01 · Storage ──────────────────────────────────────────────────────────────────────┐
│ Attached disks                                                    [ + Attach disk ]     │
│  Dev   Volume                    Pool      Size     Bus     Actions                     │
│  vda   db-01.qcow2               default   80 GiB   virtio  (root — cannot detach)      │
│  vdb   db-01-data.qcow2          default   200 GiB  virtio  [Export ⤓] [Detach…]        │
│ Importable disks (vm-disks)      [ ⤒ Upload ] drop .qcow2/.img here…                     │
│  Attach dialog: (●) new volume  size [ 20 ] GiB   ( ) existing volume [ ▾ ]   pool default│
│  ⓘ Hot-plug when running (except SATA).                                                 │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Disques → attache → importables. |
| Données (API réelles) | `GET /vms/{n}/disks`, `POST/DELETE /vms/{n}/disks`, `POST /storage/{pool}/volumes`, `GET/POST/DELETE /vm-disks`, `POST /vms/{n}/export` + `/vm-exports` (jeton de téléchargement 60 s). Volume supprimable par API **sans écran** (`DELETE /storage/{pool}/volumes/{v}`) ; `deleteVmDisk` sans appelant. |
| Actions | Attacher (nouveau/existant), détacher (confirmation), exporter, envoyer/supprimer un disque importable. |
| Fonctions existantes préservées | Partie disques de VMHardwareTab, `VmDiskUploadDropzone`, action « Export the disk » du menu. |
| Améliorations proposées | Onglet dédié ; **suppression de volume et de disque importable exposées** (endpoints existants) ; envoi avec progression réelle et échec visible (aujourd'hui « Upload complete » à 100 % même en échec) ; pool choisi au lieu de `default` codé en dur. |
| États possibles | Aucun disque additionnel, envoi en cours/échec, pool ZFS (à vérifier), VM d'un nœud distant (désactivé + raison). |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Détacher un disque ne supprime pas les données : le texte le dit ; suppression de volume = irréversible (n°45). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W19 — Détail VM — Network

**Objectif utilisateur** : Voir et modifier les interfaces et règles réseau de la VM.

```text
┌ ● db-01 · Network ──────────────────────────────────────────────────────────────────────┐
│ Interfaces                                                        [ + Add interface ]   │
│  #  MAC                 Network    Model    IP          Actions                         │
│  0  52:54:00:aa:bb:cc   default    virtio   10.0.0.21   [Remove…]                       │
│ Firewall (this VM)   default policy [ accept ▾ ]                    [ + Add rule ]      │
│  ↓ in   tcp  22    allow      [✕]        ↓ in  tcp 5432  allow  [✕]                     │
│  ⓘ Rules apply on the local host (iptables).            [ Discard ] [ Save rules ]      │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Interfaces → pare-feu de la VM. |
| Données (API réelles) | `GET /vms/{n}/network`, `POST/DELETE` interfaces, `PUT /vms/{n}/network` (**sans écran aujourd'hui**), `GET/PUT /vms/{n}/firewall`, `GET /networks/{n}` (baux DHCP). **Absents du backend** : débit et erreurs par interface (historique). |
| Actions | Ajouter/retirer une interface, éditer les règles. |
| Fonctions existantes préservées | Interfaces de VMHardwareTab, `FirewallRulesEditor` (VMOptionsTab). |
| Améliorations proposées | Onglet dédié, édition d'une interface existante (endpoint prêt), bail DHCP affiché, règles avec aperçu avant enregistrement. |
| États possibles | Aucune interface, règle invalide, réseau inactif. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. |
| Confirmations et risques | Retirer l'interface active coupe la connectivité : confirmation. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W20 — Détail VM — Snapshots / backup / restore

**Objectif utilisateur** : Protéger la VM et revenir en arrière en sécurité.

```text
┌ ● db-01 · Snapshots  (Snapshots | Backups | Clone & migrate) ───────────────────────────┐
│ Snapshots (libvirt internal)        [ + Take snapshot ]                                 │
│  ◈ before-upgrade   2026-09-24 13:40 UTC   with memory   [Restore…] [Delete…]           │
│  ◈ clean-install    2026-09-01 09:12 UTC   disk only     [Restore…] [Delete…]           │
│ Backups   mode: hot (running)      Schedule: daily 02:00 UTC · keep 7        [Edit]     │
│  ✓ 2026-09-24 02:00  12.4 GiB  cold   sha256 9f2c…  [Restore…] [Delete…]                │
│  ✕ 2026-09-23 02:00  failed: “no space left”  [Delete…]   (aujourd'hui impossible)       │
│ [ Back up now ]                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Snapshots → sauvegardes → planification. |
| Données (API réelles) | `/vms/{n}/snapshots` (+ restore/delete, `task_id` renvoyé), `/vms/{n}/backups`, `/vms/{n}/backup-schedule`, `/backups`, `POST /backups/{id}/restore`, `POST /vms/{n}/clone`, `/vms/{n}/migrate` + `migration-check`. |
| Actions | Créer/restaurer/supprimer un snapshot, sauvegarder maintenant, planifier, restaurer/supprimer une sauvegarde, cloner, migrer. |
| Fonctions existantes préservées | VMSnapshotsTab, VMBackupTab, Clone/Migrate du Summary ; ZFS : disque seulement, restauration à l'arrêt. |
| Améliorations proposées | Dates avec fuseau **UTC affiché** (aujourd'hui non étiqueté), suppression des sauvegardes échouées, formulaire de restauration à la place de `window.prompt`, progression réelle, avertissement de rétention (`retention_count` s'applique à toutes les sauvegardes terminées). |
| États possibles | Aucun snapshot, tâche en cours (boutons désactivés), échec (message du backend), VM ZFS (limites expliquées). |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Restauration = écrase l'état courant ; confirmation avec impact ; suppression irréversible. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W21 — Détail VM — Tasks & Logs

**Objectif utilisateur** : Diagnostiquer là où l'on regarde : tâches et journal de cette VM.

```text
┌ ● db-01 · Tasks & Logs ─────────────────────────────────────────────────────────────────┐
│ Tasks  status: All ▾  type: All ▾  since: 24 h ▾         🔍 filter                       │
│  St  Type            Started            Duration  By      Progress                      │
│  ◌   backup_vm       14:01:03           1m 12s    admin   ▓▓▓▓░░░░ 42 %     [Open]     │
│  ✓   start_vm        14:02:10           3s        admin                      [Open]     │
│  ✕   create_snapshot 13:51:44           2s        admin   “libvirt: …”       [Open]     │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│ Logs (audit)   level: All ▾   🔍   ▶ Follow  ⧉ Copy  ⤓                                   │
│  14:02:10.482 INFO  admin  start_vm  db-01  success                                     │
│  13:51:46.019 ERROR admin  create_snapshot  db-01  echec  “internal error: …”           │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Tâches de la VM → journal (audit) filtré sur la VM. |
| Données (API réelles) | `GET /tasks?cible=<vm>` (tous filtres backend : `statut`, `type`, `username`, `depuis`, `tri`), `GET /tasks/{id}` (avec `logs`), `GET /audit?resource=<vm>` (admin). |
| Actions | Filtrer, ouvrir le détail, copier, suivre. |
| Fonctions existantes préservées | Dock de session et Activity (pas de vue par ressource aujourd'hui). |
| Améliorations proposées | Nouvel onglet à partir de données existantes ; filtres backend jamais envoyés (`type`, `username`, `depuis`) enfin exposés. |
| États possibles | Aucune tâche, 403 sur le journal (observateur : bloc « Requires administrator »), tâche « en cours » très ancienne (« may be stuck »). |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. Zone de logs : `role=log`, `aria-live=off` par défaut (annonce seulement à la demande). |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W22 — Wizard de création de VM

**Objectif utilisateur** : Créer une VM sans erreur ni perte de saisie.

```text
┌ Create VM ──────────────────────────────────────────────────────────────────── ✕ ┐
│ ①Source ②Identity ③Placement ④Compute ⑤Storage ⑥Network ⑦Advanced ⑧Review          │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ④ Compute                                                                         │
│  vCPU    [ 2 ]  1–16 (host 16)          Memory  [ 4096 ] MiB  512–31744           │
│  ▲ Windows detected: minimum raised to 2 vCPU / 4096 MiB (host limits allow it)   │
│  ⓘ Allocation policy: limites — you cannot exceed the host.                       │
│                                                                                   │
│  ▸ Advanced (0 modified)                                                          │
├───────────────────────────────────────────────────────────────────────────────────┤
│ Draft saved locally · [ ‹ Back ]                                  [ Next › ]      │
└───────────────────────────────────────────────────────────────────────────────────┘
 ⑧ Review : Source Debian 12 · debian-12.qcow2 | Name db-02 | Node hl-devhub (local) |
   2 vCPU / 4 GiB | Disk 40 GiB virtio | Net default | Auto-cleanup 30 d | Drivers ISO —
   ⓘ Creation runs as a task.   [ ‹ Back ]   [ Create VM ]  (bloqué pendant l'envoi)
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Étapes numérotées cliquables (déjà validées) → formulaire de l'étape → résumé fidèle. |
| Données (API réelles) | `POST /vms` (charge utile inchangée : name, vcpu, memory_mb, disks, network, username, password, iso, drivers_iso, guest_os, disk_controller, import_disk, storage_pool, auto_cleanup_days), `/templates`, `/isos`, `/vm-disks`, `/host/limits`, `/networks`, `/storage`. |
| Actions | Choisir source, remplir, valider, créer ; envoyer un ISO ; annuler avec confirmation si saisie. |
| Fonctions existantes préservées | 5 étapes actuelles (Node, Template, Resources, Network, Review) : **tous les champs** ; règles : ISO ⊕ import ; pilotes ⇒ ISO ; ajustement Windows ; nom/utilisateur/mot de passe validés. |
| Améliorations proposées | 8 étapes, brouillon conservé, bornes hôte, **double envoi impossible**, erreur 422 lisible, sentinelle `__pending__` corrigée, résumé incluant auto-nettoyage/pilotes/disques, progression + lien de tâche après création, étape « Placement » explicite (nœud décoratif documenté). |
| États possibles | Validation par étape, envoi en cours, échec (saisie conservée), succès (« View task » / « Open VM »). |
| Responsive | Plein écran sous 1024 px ; navigation des étapes en liste déroulante sous 768 px. |
| Accessibilité | Focus sur le titre de l'étape à chaque changement ; erreurs sous les champs + résumé ; radios groupés avec `name` (flèches fonctionnelles) ; `aria-current=step`. |
| Confirmations et risques | Aucune action destructrice ; mot de passe jamais réaffiché ; risque : création non idempotente (protection double envoi). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W23 — Liste des nœuds

**Objectif utilisateur** : Voir la santé de chaque nœud et gérer le cluster.

```text
┌ Nodes  4                                                            [ + Add node… ]     ┐
├──┬────────────┬──────────────┬─────────┬───────────┬───────────┬───────┬───────────────┤
│St│ Name       │ Address      │ VMs     │ Storage   │ Checked   │ HA    │ ⋯             │
│● │ hl-devhub  │ local        │ 12 ● 1 ■│ 72 % of 1T│ —         │ —     │ ⋯             │
│● │ node-c     │ 10.0.0.13    │ 8 ● 2 ■ │ 41 % of 2T│ 14:03     │ 2 VMs │ ⋯             │
│○ │ node-b     │ 10.0.0.12    │ ?        │ ?         │ 14:02 ✕   │ 1 VM  │ [Diagnose]    │
├──┴────────────┴──────────────┴─────────┴───────────┴───────────┴───────┴───────────────┤
│ ⓘ CPU/RAM of remote nodes are not reported by the API. Cluster public key: [⧉ Copy]    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Nœuds → capacité → dernière vérification. |
| Données (API réelles) | `/nodes`, `/nodes/{n}/summary`, `/nodes/cluster-pubkey`, `POST/DELETE /nodes`, `/nodes/{n}/compatibility`. |
| Actions | Ajouter (avec diagnostic avant), retirer (confirmation), diagnostiquer, copier la clé publique. |
| Fonctions existantes préservées | NodesTab (champ `ssh_port` **sans contrôle** aujourd'hui), CompatibilityTab. |
| Améliorations proposées | Champ `ssh_port` exposé dans « Advanced », diagnostic de compatibilité **avant** l'ajout, dernière vérification, HA par nœud. |
| États possibles | Hors ligne, inconnu, ajout en cours, échec SSH. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Retirer un nœud : confirmation nommant les VM qui y sont enregistrées ; ajout = opération SSH. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W24 — Détail d'un nœud

**Objectif utilisateur** : Comprendre la charge et la configuration d'un nœud.

```text
┌ ● hl-devhub [local] · Node ─────────────────────────────────────────────────────────────┐
│ Summary  System  Network  Disk  Tasks  Compatibility  Shell (local admin)               │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Health ● online · uptime 34d · libvirt 10.0 · QEMU 9.0 · Debian 13 (kernel 6.12)         │
│ CPU 16 logical · KVM ✓ · NUMA 2 nodes     Memory 31 GiB (18 free)     Secure Boot: off   │
│ VMs on this node 12 (● 10 ■ 2)  [ table ]                                               │
│ Services / maintenance mode / node reboot: not provided by the backend (documented).     │
│ ▲ Remote nodes: CPU/RAM/system/shell show the LOCAL host — hidden for remote nodes.     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Santé → ressources → VM du nœud → onglets techniques. |
| Données (API réelles) | `/host/capabilities`, `/host/metrics/history`, `/health`, `/nodes/{n}/summary\|capabilities`, `/tasks?node=`. **Absents** : services, mode maintenance, redémarrage/arrêt du nœud. |
| Actions | Ouvrir le shell hôte (admin, fenêtre autonome), lancer le diagnostic, voir les tâches. |
| Fonctions existantes préservées | NodeSummary/System/Network/Disk/Tasks/Compatibility/Shell (7 onglets). |
| Améliorations proposées | Bandeau honnête pour nœud distant, filtre de tâches corrigé (nom d'hôte libvirt), version libvirt lisible (aujourd'hui entier brut). |
| États possibles | Distant (onglets non routés désactivés), hors ligne, chargement, refus. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. |
| Confirmations et risques | Shell hôte = root : admin seulement, journalisé (tâche `host_shell`). |
| Confirmation de capacité | Partiellement confirmée : services, maintenance et arrêt/redémarrage de nœud n'existent pas dans le code — ils sont documentés comme absents. |

---

## W25 — Liste et détail du stockage

**Objectif utilisateur** : Voir la capacité, la santé et le contenu des pools.

```text
┌ Storage                                                [ + Create pool ] [ ⤒ Upload ISO ] ┐
│ Pools      St  Name     Type  Node       Used              Free     State                │
│            ●   default  dir   hl-devhub  ▇▇▇▇▇▇▇░░░ 72 %   280 GiB  active               │
│            ▲   tank     zfs   hl-devhub  ▇▇▇▇▇▇▇▇▇░ 91 %   90 GiB   degraded  ⓘ local    │
├─ default ─────────────────────────────────────────────────────────────────────────────────┤
│ Volumes  db-01.qcow2 80 GiB (in use) │ …          ISOs  debian-12.iso 640 MiB  [Delete…] │
│ Performance: not provided by the backend (capacity and state only).                      │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Pools (saturation d'abord) → détail (volumes, ISO). |
| Données (API réelles) | `/storage`, `/storage/{pool}/volumes`, `POST/DELETE /storage`, `/isos`, `/vm-disks`. |
| Actions | Créer un pool (répertoire, NFS, ZFS), supprimer, envoyer/supprimer un ISO, voir les volumes. |
| Fonctions existantes préservées | StorageTab, `IsoUploadDropzone`. |
| Améliorations proposées | Barre d'usage avec seuil (ambre ≥ 80 %, rouge ≥ 90 %, texte + forme), pool ZFS marqué « local à un nœud », suppression de volume exposée, ISO dans Storage (le menu « Templates / ISO » y mène). |
| États possibles | Vide, dégradé, inaccessible, envoi en cours/échec. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Suppression de pool ZFS = `zfs destroy` (n°45). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W26 — Vue réseau

**Objectif utilisateur** : Voir les réseaux, ponts et règles ; diagnostiquer un réseau dégradé.

```text
┌ Networks                                                            [ + Create network ] ┐
│ St  Name     Type    Bridge   Subnet          DHCP leases   Autostart   Active            │
│ ●   default  nat     virbr0   192.168.122.0/24  6            yes         yes              │
│ ■   lab      isole   virbr1   10.9.0.0/24       0            no          no               │
├─ default ─────────────────────────────────────────────────────────────────────────────────┤
│ Leases: MAC 52:54:00:aa:bb:cc · 192.168.122.21 · db-01     Firewall: policy accept · 2 rules│
│ Host interfaces (capabilities): br0 (vlan 20) up · eno1 up                                │
│ Throughput/errors per interface: not provided by the backend.                             │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Réseaux → détail (baux, pare-feu, interfaces hôte). |
| Données (API réelles) | `/networks`, `/networks/{n}`, `PUT /networks/{n}/firewall`, `POST/DELETE /networks`, `/host/capabilities` (interfaces, VLAN). |
| Actions | Créer, supprimer (confirmation), éditer le pare-feu. |
| Fonctions existantes préservées | NetworkOverviewTab, `FirewallRulesEditor` (masque `subnet_netmask` sans contrôle). |
| Améliorations proposées | Baux DHCP visibles, `subnet_netmask` exposé en « Advanced », état d'inactivité lisible, état de détail par ligne (aujourd'hui un état partagé qui affiche des données périmées). |
| États possibles | Vide, doublon, pont invalide, inactif. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Supprimer un réseau utilisé par des VM : confirmation nommant les VM. |
| Confirmation de capacité | Confirmée pour bridges/VLAN/IP/MAC ; débit et erreurs par interface absents du backend. |

---

## W27 — Centre de tâches

**Objectif utilisateur** : Suivre et retrouver toute opération.

```text
┌ Tasks   status ▾  type ▾  target 🔍  user ▾  since ▾  sort ▾                    ⟳ 8 s    ┐
├──┬──────────────────┬────────────┬──────────┬────────────┬──────────┬────────────────────┤
│St│ Type             │ Target     │ Node     │ Started    │ Duration │ Progress / result  │
│◌ │ backup_vm        │ db-01      │ hl-devhub│ 14:01:03   │ 1m12s    │ ▓▓▓▓░░░░ 42 %      │
│◌ │ migrate_vm       │ api-03     │ node-c   │ 13:58:40   │ 4m35s    │ ▓▓▓▓▓▓░░ 67 %      │
│✕ │ create_snapshot  │ db-01      │ hl-devhub│ 13:51:44   │ 2s       │ libvirt: internal… │
│✓ │ start_vm         │ web-01     │ hl-devhub│ 14:02:10   │ 3s       │ done               │
├──┴──────────────────┴────────────┴──────────┴────────────┴──────────┴────────────────────┤
│ ⚠ 1 task “running” for > 15 min (host_shell): may be stuck   [ Show ]      1–50 of 312 ‹ ›│
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Filtres → table → alerte de tâche ancienne. |
| Données (API réelles) | `GET /tasks` (filtres `statut, type, username, cible, node, depuis, tri, ordre, limit ≤ 1000`). |
| Actions | Filtrer, trier, ouvrir, exporter CSV. **Pas d'annulation** (le backend n'en fournit pas). |
| Fonctions existantes préservées | ActivityTab, TaskLogPanel (session), cloche d'en-tête. |
| Améliorations proposées | Filtres backend jamais utilisés (`type`, `username`, `depuis`), libellés de type complets (dictionnaire unique ; aujourd'hui deux jeux divergents et « Migrer VM »), utilisateur réel (aujourd'hui « admin » codé en dur). |
| États possibles | Vide, chargement, erreur, ancienne tâche « en cours ». |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W28 — Détail de tâche

**Objectif utilisateur** : Comprendre pourquoi une opération a échoué ou où elle en est.

```text
┌ ✕ create_snapshot · db-01 ─────────────────────────────────────────── [⧉ Copy] [ Open VM ] ┐
│ Status  Failed      Started 13:51:44   Ended 13:51:46 (2 s)    By admin    Node hl-devhub    │
│ Error   internal error: unable to execute QEMU command 'savevm': …                          │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│ Log (audit entries for this target, last 20)                                                │
│  13:51:44.101 create_snapshot db-01 en_cours                                                │
│  13:51:46.019 create_snapshot db-01 echec  “internal error…”                                │
│ Suggested: check free space on `default` (91 %) · Related: pool tank ▲                       │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Résultat → erreur complète → journal → actions liées. |
| Données (API réelles) | `GET /tasks/{id}` (`logs` = 20 dernières lignes d'audit de la cible), `erreur`, `progres`. |
| Actions | Copier l'erreur, ouvrir la ressource, relancer l'action (depuis la ressource). |
| Fonctions existantes préservées | Ligne extensible du TaskLogPanel. |
| Améliorations proposées | Page dédiée avec lien profond, erreurs longues sans coupure, liens vers les ressources. |
| États possibles | En cours (indéterminé ou %), échec, terminé, introuvable. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W29 — Logs

**Objectif utilisateur** : Lire, filtrer et copier les journaux d'audit.

```text
┌ Logs (audit journal)   user ▾  action ▾  resource 🔍  result ▾  from [ ] to [ ]  ⤓ CSV    ┐
│ ▶ Follow   ⧉ Copy visible   wrap ☐                                                        │
│ 2026-09-24 14:02:10.482 UTC  INFO   admin   start_vm        web-01      success           │
│ 2026-09-24 14:01:03.118 UTC  INFO   admin   backup_vm       db-01       success           │
│ 2026-09-24 13:51:46.019 UTC  ERROR  admin   create_snapshot db-01      echec  internal…   │
│ 2026-09-24 13:40:12.006 UTC  WARN   system  alert_seuil_depasse  hl-devhub  cpu ≥ 90 %    │
│ ⓘ Host system logs (journald) and guest logs are not exposed by the API.                 │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Filtres → flux mono. |
| Données (API réelles) | `GET /audit` (filtres `username, action, resource, depuis, jusqu_a` — **`jusqu_a` jamais exposé**, limite ≤ 1000) ; admin seulement. |
| Actions | Filtrer (débounce 300 ms, aujourd'hui une requête par frappe), suivre, copier, exporter. |
| Fonctions existantes préservées | JournalTab. |
| Améliorations proposées | Plage `de/à`, debounce, niveau de gravité dérivé (`result`), copie/suivi, ligne détaillée. |
| États possibles | 403 (observateur), vide, très long (virtualisé). |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `role=log`, texte mono sélectionnable, filtres étiquetés. |
| Confirmations et risques | Les GET écrivent des lignes d'audit (B7) : ne pas sonder ce journal trop souvent. |
| Confirmation de capacité | Journal d'audit confirmé ; journaux système de l'hôte et des invités absents (documenté). |

---

## W30 — Événements

**Objectif utilisateur** : Voir ce qui vient de changer dans l'infrastructure.

```text
┌ Events   category ▾ (Power · Nodes · HA · Backups · Access · System)   since ▾           ┐
│ 14:03  ○ node-b went offline                       node_statut_change     [Open node]    │
│ 14:02  ▶ web-01 started by admin                   start_vm               [Open VM]      │
│ 13:58  ◆ win-2022 crashed                          etat → plante          [Open VM]      │
│ 13:40  ▲ CPU ≥ 90 % on hl-devhub                   alert_seuil_depasse    [Open node]    │
│ ⓘ No separate events feed exists: this view is derived from the audit journal and state changes. │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Flux chronologique par catégorie. |
| Données (API réelles) | Dérivé de `GET /audit` (actions `node_statut_change`, `ha_alert`, `alert_seuil_depasse`, `update_available`…) et des changements d'état observés (front). Admin seulement pour l'audit. |
| Actions | Filtrer, ouvrir la ressource. |
| Fonctions existantes préservées | Rien d'équivalent aujourd'hui (Recent activity = tâches). |
| Améliorations proposées | Nouveau : lecture d'événements sans nouvelle API ; **vrai flux d'événements = backend (A-14)**. |
| États possibles | Vide, 403, chargement. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Pas d'endpoint d'événements : vue dérivée du journal d'audit (à valider). |

---

## W31 — Alertes

**Objectif utilisateur** : Prioriser ce qui demande une action.

```text
┌ Alerts   Open 3 · Snoozed 1                                     severity ▾  resource 🔍   ┐
│ ◆ Critical  win-2022 crashed                 since 13:58 (12 min)   [Open VM] [Snooze 1h] │
│ ▲ Warning   pool tank 91 % used              since 09:10            [Open pool]           │
│ ○ Offline   node-b unreachable               since 14:02            [Diagnose]            │
│ ⓘ Alerts are derived on the client from states and audit entries; thresholds are fixed by  │
│   the backend (CPU/RAM ≥ 90 %). No alert API, no acknowledgement server-side.              │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Gravité → cause → durée → action. |
| Données (API réelles) | Dérivées : états (`plante`, `bloque`, `hors_ligne`), stockage ≥ 80/90 %, tâches en échec, `alert_seuil_depasse` et `ha_alert` (audit). |
| Actions | Ouvrir, différer localement (préférence navigateur), diagnostiquer. |
| Fonctions existantes préservées | Pas d'écran d'alertes aujourd'hui (seuils dans l'audit uniquement). |
| Améliorations proposées | Vue d'alertes réalisable immédiatement à partir des données existantes ; **API d'alertes et seuils configurables = backend (A-14)**. |
| États possibles | Aucune alerte (« All clear » sobre), périmé, 403 partiel. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Snooze local ne masque rien côté serveur : le texte le précise. |
| Confirmation de capacité | Partielle : pas d'endpoint d'alertes ; vue dérivée côté client. |

---

## W32 — Utilisateurs

**Objectif utilisateur** : Créer et gérer les comptes.

```text
┌ Security › Users  5                                                    [ + Create user ]  ┐
│ 🔍   Role ▾  Source ▾                                                                     │
│ Username   Role         Source  2FA   Last login    Actions                               │
│ admin      admin        local   ✓     14:00         [Reset password…]                     │
│ anna       observateur  local   —     yesterday     [Change role…] [Reset…] [Delete…]      │
│ sso-bob    observateur  oidc    —     3 d ago       [Change role…]                        │
│ ⚠ Requires the administrator role. Observers get a permission notice, not a spinner.     │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Liste → création → actions par ligne. |
| Données (API réelles) | `GET/POST /auth/users`, `PUT/DELETE /auth/users/{u}` (admin), `auth_source` (aujourd'hui abandonné par le client). |
| Actions | Créer, changer de rôle, réinitialiser le mot de passe, supprimer. |
| Fonctions existantes préservées | PermissionsTab (section utilisateurs). |
| Améliorations proposées | Source d'authentification affichée, état 2FA si fourni, suppression avec avertissement sur les ACL/jetons conservés (B11), mot de passe jamais affiché. |
| États possibles | 403, vide, doublon (409), verrouillage. |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Supprimer un utilisateur : ses ACL et jetons API subsistent côté serveur ; le dialogue l'écrit. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W33 — Rôles et permissions

**Objectif utilisateur** : Comprendre et attribuer les droits.

```text
┌ Security › Roles & permissions      Users │ Groups │ Roles │ Resource pools │ Assignments (ACL) ┐
│ Roles     Built-in: admin · observateur · lecteur · operateur · gestionnaire   Custom: qa-ops   │
│  qa-ops  (custom)   vm.view ✓ vm.power ✓ vm.console ✓ vm.snapshot ✗ vm.resize ✗ …  [Edit][Del]│
│ Assignments   Subject         Role         Scope                                              │
│  group:devs   gestionnaire    pool:staging                       [Remove…]                    │
│  user:anna    operateur       vm:web-01                          [Remove…]                    │
│  [ + Add assignment ]  subject [ ▾ ] role [ ▾ ] scope [ vm | pool | container ▾ ]              │
│ Matrix: privilege × role (read-only view) ; “Effective rights for user anna…” [ Check ]       │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Rôles → affectations → vérification des droits effectifs. |
| Données (API réelles) | `/acl`, `/groups`, `/pools`, rôles personnalisés (`custom:<id>`), privilèges `vm.*` et `container.*`. |
| Actions | Créer un rôle, affecter, retirer, gérer groupes/pools/membres. |
| Fonctions existantes préservées | PermissionsTab (617 lignes : 6 sections). |
| Améliorations proposées | Matrice privilège × rôle lisible, aperçu des droits effectifs, `aria-label` corrigé (`p.nom` indéfini), confirmations conservées. |
| États possibles | 403, vide, doublon d'affectation (non détecté par le backend), sujet/ressource inexistant (non vérifié par le backend). |
| Responsive | ≥ 1024 px : tableau complet ; 768–1023 px : colonnes prioritaires (état, nom, nœud, actions) ; < 768 px : liste de cartes. |
| Accessibilité | `<table>` sémantique ou `role=grid` ; `aria-sort` ; en-têtes collants ; sélection annoncée « 3 selected » ; menu `⋯` au clavier ; focus visible ; nombres alignés à droite. |
| Confirmations et risques | Les listes ne sont pas filtrées par ACL côté backend (B2) : l'écran l'indique ; ne jamais suggérer qu'un droit masque une donnée. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W34 — Paramètres

**Objectif utilisateur** : Régler le comportement de l'installation.

```text
┌ Settings   General │ Automation │ Notifications │ Updates │ Profile & policy │ About        ┐
│ Profile & allocation policy                                                               │
│  Deployment profile  (○ homelab  ● standard  ○ avance)   recommended: standard            │
│  Allocation policy   (● limites ○ surallocation ○ libre)   ⓘ effective limits shown below │
│  Metrics interval 15 s (from profile)                                                     │
│ Updates   Current 1.14.2 · Latest 1.14.3 [Check] [Update…]  (auto-rollback if unhealthy)   │
│ Notifications  Channels: webhook ops-alerts ✓ [Test] [Edit] [Delete…]   [ + Add channel ]  │
│ Automation  Jobs: nightly-lb (dry-run ✓) [Run…] [Delete…]                [ + Create job ]  │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Sections → réglages → actions à risque en bas. |
| Données (API réelles) | `/host/profile`, `PUT /host/profile\|allocation`, `/update/*`, `/notifications/*`, `/jobs*`. |
| Actions | Choisir profil/politique, tester un canal, créer/exécuter/supprimer un job, mettre à jour. |
| Fonctions existantes préservées | AutomationTab, NotificationsTab, UpdateModal, profil dans Compatibility. |
| Améliorations proposées | Regroupement par thème, `use_tls` exposé, « Run » et suppression de job **confirmés** avec texte d'impact exact, ancienneté du dernier contrôle de mise à jour. |
| États possibles | 403, test de canal en cours/échec, mise à jour en cours (le service redémarre : message « reconnecting… »). |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Un seul `h1` (nom de la ressource), `h2` par carte ; ordre de lecture = ordre visuel ; graphiques avec tableau de données de secours ; états annoncés par `aria-live`. |
| Confirmations et risques | Mise à jour = redémarrage du service ; job « Run » exécute des commandes ; le webhook peut contenir un secret (masqué). |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W35 — Recherche globale / command palette

**Objectif utilisateur** : Aller n'importe où et agir sans souris.

```text
┌─────────────────────────────────────────────────────────────────┐
│ 🔍 db▌                                              Esc to close │
├─────────────────────────────────────────────────────────────────┤
│ Virtual machines                                                │
│  ● db-01           hl-devhub › VMs         ↵ Open               │
│  ● db-replica      hl-devhub › VMs                              │
│ Actions                                                         │
│  ▶ Start db-01                              (VM stopped only)   │
│  ＋ Create VM                                                   │
│ Go to                                                           │
│  Infrastructure › Storage      g s                              │
│ ↑↓ navigate · ↵ select · > actions · @ nodes · # tasks          │
└─────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Champ → groupes (ressources, actions, navigation). |
| Données (API réelles) | Même index que l'Explorer + actions autorisées pour le rôle ; `cmdk` déjà dans le projet (primitive `command` inutilisée aujourd'hui). |
| Actions | Taper, choisir, exécuter une action (les destructrices ouvrent une confirmation). |
| Fonctions existantes préservées | Recherche d'en-tête (`SearchBar`) ; les raccourcis `⌘K`/`C`/`S` annoncés dans l'UI actuelle n'existent pas. |
| Améliorations proposées | Palette réelle, raccourcis `g`+lettre documentés dans « Keyboard shortcuts » (`?`). |
| États possibles | Aucun résultat, actions indisponibles (raison), chargement de l'index. |
| Responsive | Pleine largeur sous 768 px. |
| Accessibilité | `role=dialog`, `role=combobox` + `listbox`, focus piégé, annonces des résultats. |
| Confirmations et risques | Actions destructrices jamais exécutées directement depuis la palette. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W36 — État — chargement

**Objectif utilisateur** : Montrer la forme de la vue pendant l'attente.

```text
┌ Virtual machines                                                        ⟳ loading… ┐
│ ▒▒▒▒▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒▒▒   ▒▒▒▒▒▒                                                    │
├──┬──┬───────────────┬───────────┬──────┬────────                                    │
│▒▒│▒▒│ ▒▒▒▒▒▒▒▒▒▒▒   │ ▒▒▒▒▒▒▒▒  │ ▒▒▒  │ ▒▒▒▒▒                                       │
│▒▒│▒▒│ ▒▒▒▒▒▒▒▒      │ ▒▒▒▒▒▒▒▒  │ ▒▒▒  │ ▒▒▒▒▒                                       │
│                    Still loading… (10 s)  This is taking longer than usual [ Retry ]│
└────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Squelette fidèle à la mise en page ; message après 10 s. |
| Données (API réelles) | Aucune donnée. |
| Actions | Réessayer. |
| Fonctions existantes préservées | `LoadingState` (message à 10 s) et « Loading the infrastructure… » global (à supprimer : il démonte l'écran). |
| Améliorations proposées | Chargement local par zone, sans démonter le reste. |
| États possibles | Initial, rechargement (données conservées avec indicateur discret), lent. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | `aria-busy`, texte masqué « Loading virtual machines ». |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W37 — État — vide

**Objectif utilisateur** : Expliquer l'absence de contenu et proposer l'action suivante.

```text
┌ Snapshots ───────────────────────────────────────────────────┐
│                       ◈                                      │
│              No snapshots for db-01 yet                      │
│   Snapshots let you roll back in seconds before a change.    │
│              [ + Take snapshot ]                             │
└──────────────────────────────────────────────────────────────┘
    lecture seule : « No snapshots yet. » (sans bouton)
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Icône sobre → phrase → une action. |
| Données (API réelles) | Réponse vide. |
| Actions | Créer. |
| Fonctions existantes préservées | Textes vides épars et hétérogènes. |
| Améliorations proposées | Composant unique `EmptyState` selon droits. |
| États possibles | Vide, vide filtré (« Clear filters »), vide faute de droit. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Texte dans un `role=status` ; bouton nommé. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W38 — État — erreur

**Objectif utilisateur** : Rendre l'erreur compréhensible et actionnable.

```text
┌ ◆ Could not load backups ────────────────────────────────────┐
│ The server answered 500: Internal server error.              │
│ Request  GET /backups   ·   14:03:21                         │
│ [ Retry ]  [ ⧉ Copy details ]  [ Open logs ]                 │
└──────────────────────────────────────────────────────────────┘
   validation 422 : champ « vCPU » : « must be ≥ 1 » (jamais [object Object])
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Titre (ce qui a échoué) → cause → actions. |
| Données (API réelles) | Erreur normalisée : `detail` chaîne \| liste de chaînes \| liste d'objets pydantic (B8). |
| Actions | Réessayer, copier, ouvrir les logs. |
| Fonctions existantes préservées | `[object Object]` sur 422, erreur initiale jamais effacée. |
| Améliorations proposées | Normaliseur unique, effacement à la reprise, lien vers la tâche/le journal. |
| États possibles | 500, 409 conflit, 422 validation, réponse illisible (non JSON), délai dépassé. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | `role=alert` pour bloquant, `status` pour non bloquant. |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W39 — État — hors ligne

**Objectif utilisateur** : Distinguer « l'API ne répond pas » de « la ressource est arrêtée ».

```text
┌ ▲ Cannot reach the server ─ last update 14:03:21 ──────────── retrying in 8 s [ Retry now ] ┐
│ (contenu grisé, marqué « stale » ; actions désactivées ; consoles ouvertes indiquent « disconnected ») │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
  après une mise à jour de Hyperlite : « Hyperlite is restarting… back in ~20 s » (auto-reconnect)
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Bandeau global fixe → contenu périmé. |
| Données (API réelles) | Échecs réseau du client (`Cannot reach the server…`), `/health`. |
| Actions | Réessayer. |
| Fonctions existantes préservées | Message d'erreur simple aujourd'hui, sans reprise ni indication de péremption. |
| Améliorations proposées | Reconnexion automatique (recul exponentiel), données marquées périmées, prise en charge du redémarrage lors d'une mise à jour. |
| États possibles | Hors ligne, en reconnexion, rétabli (toast). |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | Bandeau `role=alert` (une fois). |
| Confirmations et risques | Aucun. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W40 — État — permission refusée

**Objectif utilisateur** : Expliquer pourquoi on ne peut pas voir ou faire, sans spinner infini.

```text
┌ 🔒 You don't have permission to view the Journal ────────────┐
│ This area requires the administrator role.                   │
│ Your role: observateur   [ Request access → mailto admin ]   │
└──────────────────────────────────────────────────────────────┘
   bouton désactivé : [ ▶ Start ]  ⓘ « Requires vm.power on db-01 »
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Icône cadenas → règle → recours. |
| Données (API réelles) | `/auth/me` (rôle) + ACL de l'utilisateur ; 403 du backend. |
| Actions | Contacter un admin. |
| Fonctions existantes préservées | Aujourd'hui : onglets visibles pour l'observateur avec spinner éternel ou toasts d'erreur toutes les 8 s ; utilisateurs ACL sans bouton. |
| Améliorations proposées | Masquer ce qui n'est jamais permis, désactiver avec raison le reste, aucun sondage après un 403. |
| États possibles | 403 sur écran, 403 sur action. |
| Responsive | ≥ 1024 px : grille 2–4 colonnes ; 768–1023 px : 2 colonnes ; < 768 px : colonne unique, sections repliables. |
| Accessibilité | `role=status`, privilège requis dans le texte. |
| Confirmations et risques | Ne jamais élargir un droit ; le backend reste l'autorité. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W41 — Modale de suppression d'une VM

**Objectif utilisateur** : Empêcher une suppression accidentelle et nommer l'impact.

```text
┌ Delete VM “db-01” ─────────────────────────────────────── ✕ ┐
│ ◆ This permanently deletes the VM and its disks.             │
│ • 2 disks (280 GiB) will be deleted                          │
│ • 3 snapshots will be deleted                                │
│ • Backups (5), backup schedule and HA registration are NOT   │
│   removed automatically                                      │
│ • The VM must be stopped (it is: ■ stopped)                  │
│ Type the VM name to confirm:  [ db-01            ]           │
│                          [ Cancel ]  [ Delete VM ] (rouge)   │
└──────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Titre avec verbe + nom → impact → saisie du nom → bouton rouge (désactivé tant que le nom diffère). |
| Données (API réelles) | `DELETE /vms/{n}?confirm=true&node=`, `/backups`, `/snapshots`, `/ha` pour l'inventaire d'impact. |
| Actions | Annuler (focus par défaut), supprimer. |
| Fonctions existantes préservées | `ConfirmDialog` + `confirm=true` ajouté par le client (le backend refuse sans). |
| Améliorations proposées | Impact exact (le backend laisse sauvegardes/HA/métriques), saisie du nom, VM en marche : « Stop it first ». |
| États possibles | En marche (bloqué), erreur 409, succès (tâche `delete_vm`). |
| Responsive | Plein écran sous 480 px. |
| Accessibilité | Focus initial sur Annuler ; `role=alertdialog` ; description liée. |
| Confirmations et risques | Irréversible. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W42 — Modale de force stop

**Objectif utilisateur** : Rendre l'arrêt brutal explicite.

```text
┌ Force stop “db-01” ────────────────────────────────────── ✕ ┐
│ ▲ Equivalent to pulling the power cable.                     │
│ Unsaved data in the guest may be lost and filesystems may     │
│ need a check on next boot.                                   │
│ Try first:  [ Graceful shutdown ]  (ACPI, waits up to 60 s)  │
│                          [ Cancel ]  [ Force stop ] (ambre)  │
└──────────────────────────────────────────────────────────────┘
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Avertissement → alternative sûre proposée en premier → action. |
| Données (API réelles) | `POST /vms/{n}/stop?force=true&node=`. |
| Actions | Choisir l'arrêt propre, annuler ou forcer. |
| Fonctions existantes préservées | Force stop avec confirmation existante (`ConfirmDialog`). |
| Améliorations proposées | Alternative propre proposée d'abord, impact expliqué. |
| États possibles | En marche seulement. |
| Responsive | Plein écran sous 480 px. |
| Accessibilité | `alertdialog`, focus sur « Graceful shutdown ». |
| Confirmations et risques | Perte de données possible. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W43 — Modale de redémarrage ou d'arrêt

**Objectif utilisateur** : Éviter qu'un Restart (aujourd'hui brutal) ou un Stop soit lancé par erreur.

```text
┌ Restart “db-01” ───────────────────────────────────────── ✕ ┐
│ ▲ Restart performs a hard power cycle (destroy then start):  │
│   the guest is not shut down cleanly.                        │
│ (●) Restart now (hard)      ( ) Graceful stop, then start    │
│ ⓘ Graceful restart needs guest ACPI support.                 │
│                          [ Cancel ]  [ Restart ]             │
└──────────────────────────────────────────────────────────────┘
   Stop propre : « Stop db-01? The guest will receive a shutdown signal. [Cancel][Stop] »
   Nœud (redémarrer/arrêter l'hôte) : **absent du backend — non proposé**.
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Explication du mode → choix → action. |
| Données (API réelles) | `POST /vms/{n}/restart` (le client envoie toujours `force=true`), `POST /vms/{n}/stop`. |
| Actions | Choisir le mode, confirmer. |
| Fonctions existantes préservées | Restart sans confirmation, Stop des cartes sans confirmation (`client.js:398`). |
| Améliorations proposées | Confirmation ajoutée ; choix « graceful » **si validé** (nécessite de ne plus forcer `force=true` : décision D-04). |
| États possibles | En marche seulement. |
| Responsive | Plein écran sous 480 px. |
| Accessibilité | `alertdialog`. |
| Confirmations et risques | Restart forcé = risque de corruption. |
| Confirmation de capacité | VM : confirmée. Redémarrage/arrêt d'un nœud : absent du code, documenté comme non proposé. |

---

## W44 — Modale d'opération critique de nœud

**Objectif utilisateur** : Protéger les opérations qui affectent plusieurs VM ou tout le cluster.

```text
┌ Remove node “node-c” from the cluster ─────────────────── ✕ ┐
│ ◆ 8 VMs are registered on this node (6 running).             │
│ • They are NOT migrated: they stay on node-c and disappear   │
│   from the inventory until the node is added again.          │
│ • 2 VMs are HA-protected: recovery will no longer be offered.│
│ Type the node name:  [ node-c          ]                     │
│                    [ Cancel ]  [ Remove node ]               │
└──────────────────────────────────────────────────────────────┘
   Même gabarit : Disable HA · HA Recover (manuel, jamais automatique) · Live migrate (avec compatibilité) ·
   Apply update (service redémarré ; retour arrière automatique).
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Impact chiffré → conséquences → saisie du nom. |
| Données (API réelles) | `DELETE /nodes/{n}`, `/ha/*` (enable/disable/recover), `migration-check` (`/vms/{n}/migration-check`), `/update/apply`. |
| Actions | Confirmer avec saisie du nom. |
| Fonctions existantes préservées | Confirmations existantes NodesTab (retrait) et HaTab (désactivation) ; **HA Recover sans confirmation** aujourd'hui. |
| Améliorations proposées | HA Recover confirmé avec impact (pas de fencing : risque de double démarrage si le nœud est en réalité vivant), migration précédée du diagnostic de compatibilité. |
| États possibles | Vérification en cours, incompatibilité (raisons + actions), succès. |
| Responsive | Plein écran sous 480 px. |
| Accessibilité | `alertdialog`. |
| Confirmations et risques | Récupération HA sans fencing : le texte l'écrit. |
| Confirmation de capacité | Capacité confirmée par le code. |

---

## W45 — Modale d'opération destructive sur le stockage

**Objectif utilisateur** : Empêcher la perte de données stockées.

```text
┌ Delete storage pool “tank” (ZFS) ──────────────────────── ✕ ┐
│ ◆ This runs `zfs destroy` — all data in the pool is lost.    │
│ • 3 volumes (410 GiB), 2 of them used by VMs: db-02, api-03  │
│ • Local to node hl-devhub (ZFS pools are not shared)         │
│ Type the pool name:  [ tank            ]                     │
│ ☐ I understand this cannot be undone                         │
│                    [ Cancel ]  [ Delete pool ]               │
└──────────────────────────────────────────────────────────────┘
   Même gabarit : supprimer un ISO, un volume, un disque importable, une sauvegarde, un export.
```

| Aspect | Contenu |
|---|---|
| Hiérarchie visuelle | Commande réelle → volumes touchés → double garde (nom + case). |
| Données (API réelles) | `DELETE /storage/{pool}?confirm=true&node=`, `/storage/{pool}/volumes`, `/isos`, `/vm-disks`, `/backups`. |
| Actions | Confirmer par saisie. |
| Fonctions existantes préservées | Confirmations existantes (StorageTab : suppression de pool, ISO). |
| Améliorations proposées | Liste des VM concernées, type de pool, portée locale. |
| États possibles | Pool utilisé (bloqué ou fortement averti), erreur du backend. |
| Responsive | Plein écran sous 480 px. |
| Accessibilité | `alertdialog`. |
| Confirmations et risques | Irréversible. |
| Confirmation de capacité | Capacité confirmée par le code. |
