# 03 — Design system « Infrastructure Control Plane »

> Phase 1 — documentation seulement. Les valeurs ci-dessous sont **calculées** (contrastes WCAG mesurés, chroma OKLCH mesuré) mais rien n'est encore implémenté.
> Méthode : cadre `impeccable` (mode **Operate** : scanabilité, cohérence, états standardisés, mouvement 150–250 ms sans décor) et `ui-ux-pro-max` (règles de contraste, graphiques accessibles, jumelage police sans/mono pour les données). Ses propositions de palettes noires/néon ont été **écartées** car contraires au brief.

## 1. Direction artistique

**Nom de travail : « Engineering sheet »** — une console qui se lit comme une fiche technique bien tenue : lignes fines, chiffres tabulaires en police à chasse fixe, surfaces mates légèrement bleutées, texte à fort contraste, et **la couleur réservée à l'exception** (un incident, une alerte, une sélection). Une infrastructure saine paraît calme et neutre ; une anomalie est immédiatement repérable parce qu'elle est la seule chose colorée à l'écran.

| Repère | Choix | Pourquoi |
|---|---|---|
| Ton | sobre, dense, mature, fiable | usage de production, sessions de plusieurs heures |
| Thème par défaut | **sombre non noir** (bleu-ardoise) ; thème clair conservé ; option « système » | demande explicite du brief ; l'existant démarre en clair et ignore la préférence système |
| Couleur d'accent | un seul bleu calme, utilisé pour sélection, lien, focus, action principale | pas de multicolore décoratif |
| Couleur d'état | 5 teintes désaturées (info, succès, alerte, danger, hors ligne/inconnu neutres) | la couleur signifie toujours un état, jamais un décor |
| Identité | marque Hyperlite conservée en petit (logo à décider, non bloquant) | PRODUCT.md : « non tranché » |
| Interdits | noir pur généralisé, néons, lueurs, dégradés agressifs, badges fluo, fonds rouge/vert continus, animations distractives, esthétique gaming/cyberpunk/hacker/template SaaS | brief |

Trois autres directions ont été tirées et écartées : « Control room » (trop sombre et contrasté, fatigue), « Paper ledger » (clair dominant, mal adapté au tableau de bord toute la journée), « Blueprint grid » (trame décorative, viole « pas de décor »). Le tirage aléatoire de l'outil (`concept-seed`, indice 7) ne l'emporte pas sur les décisions imposées par le brief.

## 2. Professional Visual Comfort

### 2.1 Palette professionnelle retenue

Base **ardoise bleutée** (teinte ≈ 250°, chroma faible). Surfaces séparées par de petits écarts de clarté plus des bordures fines, pas par des ombres ni des dégradés.

| Rôle | Thème sombre | Thème clair |
|---|---|---|
| App | `#181D25` | `#E8ECF1` |
| Sidebar / rail | `#131820` | `#DFE5EC` |
| Workspace (fond de page) | `#1D232C` | `#F3F5F8` |
| Surface (cartes, tableaux) | `#232A35` | `#FFFFFF` |
| Surface relevée (menus, dialogues) | `#2A3240` | `#FFFFFF` (+ bordure et ombre 1 px) |
| Survol | `#2C3542` | `#EDF1F6` |
| Sélection | `#243A50` | `#DCE9F6` |
| Accent | `#67AEDB` | `#1A6598` |
| Info | `#8EAEDF` | `#2F5FA6` |
| Succès | `#66BA93` | `#1B7150` |
| Alerte | `#E0AC4E` | `#8A5A0B` |
| Danger | `#EF8E85` | `#A4403A` |
| Hors ligne | `#9AA6B8` | `#546074` |
| Inconnu | `#A0A9B8` | `#566275` |

Luminance du fond sombre : la plus basse `0,009` (jamais `#000`) ; les surfaces sombres se situent à L ≈ 0,23–0,34 en OKLCH, ce qui garde l'interface « lumineuse et structurée » plutôt qu'une masse sombre.

### 2.2 Niveau de saturation maximal recommandé

- Couleurs fonctionnelles : **chroma OKLCH ≤ 0,135** (mesuré : accent 0,097 sombre / 0,108 clair ; info 0,080 / 0,126 ; succès 0,101 / 0,096 ; alerte 0,126 / 0,105 ; danger 0,119 / 0,134 ; hors ligne et inconnu ≤ 0,036).
- Surfaces et bordures : **chroma ≤ 0,03**.
- Aucune couleur sémantique en aplat sur plus de ~4 % de la surface visible ; fonds d'état = mélange à 12–16 % de la couleur dans la surface (`color-mix`), texte de l'état à contraste plein.

### 2.3 Règles d'utilisation des couleurs sémantiques

1. **Un état = une forme + un mot + une couleur** (jamais la couleur seule) : ● Running, ■ Stopped, ▲ Warning, ◆ Error, ◐ Migrating/Paused, ○ Offline/Unknown. Voir §6.
2. La couleur est **réservée à l'exception** : les lignes saines d'un tableau restent neutres (une pastille verte discrète suffit) ; c'est le rouge/ambre qui doit sauter aux yeux.
3. **Danger** = erreur ou action destructrice ; **Alerte** = dégradé ou risque non bloquant ; **Info** = neutre informatif ; **Succès** = confirmation, jamais décor ; **Hors ligne / Inconnu** = gris, jamais rouge (l'absence de donnée n'est pas une panne).
4. Un état critique n'inonde jamais l'écran : bande de 3 px + icône + libellé ; le bandeau d'incident est unique et repliable.
5. Le rouge d'une action destructrice est réservé au bouton final de confirmation, pas au bouton qui ouvre la boîte de dialogue.

### 2.4 Contrastes minimums visés (mesurés)

| Paire | Cible | Mesuré (pire cas, sombre / clair) |
|---|---|---|
| Texte principal sur toute surface | ≥ 7:1 | 9,6 / 12,8 |
| Texte secondaire | ≥ 4,5:1 | 5,6 / 6,2 |
| Texte atténué (`muted`) | ≥ 4,5:1 | 4,6 / 5,1 |
| Couleurs d'état comme texte ou icône | ≥ 4,5:1 (icône ≥ 3:1) | 4,8–5,7 / 4,8–5,2 |
| Texte sur bouton accent | ≥ 4,5:1 | 7,5 / 6,3 |
| Anneau de focus | ≥ 3:1 (visé ≥ 4,5) | 7,1–7,8 / 4,9–5,4 |
| Bordure de champ / contrôle | ≥ 3:1 (`border-strong`) | 3,4 / 3,7 |
| Texte désactivé | exempté (information redondante) | 3,0 |

Constat honnête : la sélection et le survol ne se distinguent du fond que par ≈ 1,2:1 (normal pour un fond de ligne) ; la sélection porte donc **un second indicateur non chromatique** (barre de 2 px à gauche + texte en semi-gras), conformément à la règle « pas la couleur seule ».

### 2.5 Règles de fonds et surfaces

- Trois niveaux seulement : *workspace* (page), *surface* (cartes, tableaux), *surface relevée* (menus, dialogues). Pas de quatrième teinte.
- Sidebar/rail plus sombre que le workspace en thème sombre, plus foncée que la page en thème clair : elle **reste neutre** (fini le violet indépendant du thème).
- Aucun dégradé, aucune image de fond, aucune transparence qui réduit le contraste du texte.
- Ombres : uniquement pour les surfaces flottantes (menu, dialogue, toast) ; `0 4px 16px rgba(0,0,0,.25)` sombre, `0 2px 8px rgba(0,0,0,.12)` clair.

### 2.6 Règles de bordures

- Séparateur de structure : `border-subtle` ; bord de carte/tableau : `border-default` (1 px) ; bord de champ interactif : `border-strong` (≥ 3:1) ; focus : `border-focus` en anneau de 2 px avec décalage de 2 px.
- Jamais de bordure colorée pleine autour d'une carte pour signaler un état : on utilise la barre latérale de 3 px + l'icône.

### 2.6 bis Structure

Barre supérieure 48 px ; rail 56 px ; Inventory Explorer 288 px (240–420 px redimensionnable) ; dock 40 px replié / 240 px ouvert ; contenu max 1 600 px sauf tableaux.

### 2.7 Règles de typographie

- **UI : IBM Plex Sans** ; **données : IBM Plex Mono** (IPs, UUID, MAC, chemins, ID, valeurs, logs, commandes) avec chiffres tabulaires ; jumelage recommandé par `ui-ux-pro-max` (mono pour les données, sans pour les libellés) et déjà proche de l'existant (Plex Mono).
- **Polices auto-hébergées** (fichiers `woff2` dans l'application, sous-ensemble latin + latin étendu pour le français) : l'existant charge Google Fonts, ce qui échoue sur une installation hors ligne ; c'est une exigence de la reconstruction.
- Échelle : 12 / 13 / 14 / 16 / 20 px ; corps de tableau 13 px, formulaires 14 px, jamais moins de 12 px ; hauteur de ligne 1,25 (titres) et 1,5 (texte) ; chiffres tabulaires et alignés à droite dans les colonnes numériques ; unités fixes (`MiB`, `GB`, `%`) séparées par une espace insécable.
- Les longues chaînes techniques ne cassent jamais la mise en page : `overflow-wrap:anywhere` dans les cellules de détail, points de suspension au milieu (`fe80::…:1a2b`) dans les cellules de tableau avec info-bulle + bouton « copier ».
- Casse : phrase (« Start VM »), pas de MAJUSCULES sauf micro-étiquettes de 11–12 px avec espacement de lettres.

### 2.8 Règles de confort pour les longues sessions

- Pas de blanc pur en thème sombre (texte principal `#E4E9F0`) ; pas de noir pur (fond `#131820` minimum).
- Aucun mouvement en boucle sauf l'anneau de progression des tâches actives (désactivé si `prefers-reduced-motion`, remplacé par « 42 % »).
- Rafraîchissement sans clignotement : la mise à jour des données ne redessine ni ne fait sauter le tableau (les valeurs changent en place ; une ligne en cours d'édition n'est jamais remplacée).
- Densité réglable (Comfortable 40 px / Compact 32 px / Dense 28 px par ligne) ; le choix est mémorisé.
- Zoom navigateur jusqu'à 200 % sans perte de contenu (WCAG 1.4.4) ; largeur de lecture des textes ≤ 75 caractères.
- Indication de **fraîcheur** (« mis à jour il y a 12 s ») pour les données interrogées, en gris ; une donnée périmée (> 3 intervalles) passe à « ○ périmé » plutôt que d'induire en erreur.

### 2.9 Règles pour graphiques, tableaux, logs et alertes

- **Graphiques** (Recharts conservé) : une série principale en accent ; séries secondaires distinguées par **motif de trait et forme de marqueur**, pas seulement par la couleur ; palette catégorielle de 5 couleurs à chroma ≤ 0,10 (bleu `#67AEDB`, ambre `#E0AC4E`, vert `#66BA93`, violet grisé `#A79BD6`, rose grisé `#D69AB0` en sombre) ; grille `border-subtle`, axes en mono 12 px ; info-bulle en surface relevée ; **tableau de données de secours** (« View as table ») pour l'accessibilité ; bouton pause/reprise du direct ; les graphiques à un seul point affichent un état « pas encore assez de données » (aujourd'hui : courbe vide et étiquettes coupées) ; échelle Y toujours à partir de 0 pour les pourcentages.
- **Tableaux** : en-têtes collants, colonnes triables (indicateur ▲▼ + `aria-sort`), pas de zébrures (bordures fines), sélection par ligne avec case ; actions de ligne dans un menu `⋯` ; état vide, chargement (squelette de lignes) et erreur intégrés ; virtualisation > 200 lignes ; export CSV des vues filtrées.
- **Logs** : police mono 12,5 px, ligne = horodatage précis (`2026-09-24 14:03:21.482 UTC`) + niveau (forme + texte) + message ; retour à la ligne activable ; recherche, filtre de gravité, « suivre » / pause, **copier la sélection / tout**, liens vers la ressource ; jamais de secret affiché.
- **Alertes** : liste triée par gravité puis ancienneté ; chaque alerte porte cause, ressource liée (lien), durée, action suggérée ; accusé de réception local (« Snooze 1 h » = préférence, sans effet sur le backend).

### 2.10 Interdictions

Noir pur généralisé · néons · lueurs (`box-shadow` colorée, `text-shadow`) · dégradés agressifs · badges fluorescents · fonds rouge/vert continus · animations distractives · icônes emoji · couleur seule pour porter un état · texte < 12 px · effet de verre (`backdrop-filter`) sur les données.

### 2.11 Visibilité des états critiques sans anxiété

Un incident se signale **par contraste avec le calme** : (1) une seule bande d'incident en haut du workspace quand quelque chose est réellement rouge, avec compteur et lien ; (2) pastille d'état + forme distincte dans l'arbre, avec **compteur agrégé sur les nœuds parents** ; (3) tri par défaut « problèmes d'abord » dans la liste des VM ; (4) l'onglet Alerts du dock affiche un point ● seulement si une alerte non lue existe ; (5) pas de son, pas de clignotement, pas de plein écran rouge ; (6) l'état « hors ligne / inconnu » reste gris pour ne pas crier à la panne quand la donnée manque ; (7) les toasts d'erreur restent jusqu'à fermeture (pas d'expiration), les toasts de succès disparaissent en 5 s.

## 3. Jetons (tokens) CSS

Trois couches (approche `design-system` : primitives → sémantiques → composants). Seule la couche **sémantique** est utilisée par les composants ; les primitives ne sont jamais référencées directement.

```css
:root[data-theme="dark"], :root {
  /* Surfaces */
  --color-bg-app:            #181D25;
  --color-bg-sidebar:        #131820;
  --color-bg-workspace:      #1D232C;
  --color-bg-surface:        #232A35;
  --color-bg-surface-raised: #2A3240;
  --color-bg-hover:          #2C3542;
  --color-bg-selected:       #243A50;

  /* Bordures */
  --color-border-subtle:     #2E3745;
  --color-border-default:    #3B4657;
  --color-border-strong:     #6D7C93;
  --color-border-focus:      #86BBEA;

  /* Textes */
  --color-text-primary:      #E4E9F0;
  --color-text-secondary:    #AAB5C5;
  --color-text-muted:        #98A4B7;
  --color-text-disabled:     #66728A;
  --color-text-on-accent:    #0E1620;

  /* Couleurs fonctionnelles */
  --color-accent:            #67AEDB;
  --color-info:              #8EAEDF;
  --color-success:           #66BA93;
  --color-warning:           #E0AC4E;
  --color-danger:            #EF8E85;
  --color-offline:           #9AA6B8;
  --color-unknown:           #A0A9B8;

  /* Dérivés d'état (fonds discrets, 14 %) */
  --color-success-subtle:    color-mix(in oklab, var(--color-success) 14%, var(--color-bg-surface));
  --color-warning-subtle:    color-mix(in oklab, var(--color-warning) 14%, var(--color-bg-surface));
  --color-danger-subtle:     color-mix(in oklab, var(--color-danger) 14%, var(--color-bg-surface));
  --color-info-subtle:       color-mix(in oklab, var(--color-info) 14%, var(--color-bg-surface));

  /* Typographie */
  --font-ui:   "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  --font-size-xs: 0.75rem;    /* 12 px : micro-étiquettes, axes */
  --font-size-sm: 0.8125rem;  /* 13 px : tableaux, logs, arbre */
  --font-size-md: 0.875rem;   /* 14 px : corps, formulaires */
  --font-size-lg: 1rem;       /* 16 px : sous-titres */
  --font-size-xl: 1.25rem;    /* 20 px : titre de page */
  --line-height-tight:  1.25;
  --line-height-normal: 1.5;

  /* Espacement (base 4 px) */
  --space-1: 0.25rem;  /* 4  */
  --space-2: 0.5rem;   /* 8  */
  --space-3: 0.75rem;  /* 12 */
  --space-4: 1rem;     /* 16 */
  --space-5: 1.25rem;  /* 20 */
  --space-6: 1.5rem;   /* 24 */
  --space-8: 2rem;     /* 32 */
  --space-10: 2.5rem;  /* 40 */

  /* Structure */
  --radius-xs: 2px;
  --radius-sm: 4px;
  --radius-md: 6px;
  --border-width: 1px;
  --sidebar-width: 3.5rem;      /* rail 56 px */
  --inventory-width: 18rem;     /* 288 px, 240–420 px redimensionnable */
  --topbar-height: 3rem;        /* 48 px */

  /* Mouvement */
  --duration-fast: 120ms;
  --duration-base: 180ms;
  --duration-slow: 240ms;
  --ease-standard: cubic-bezier(0.2, 0, 0, 1);
}

:root[data-theme="light"] {
  --color-bg-app: #E8ECF1;          --color-bg-sidebar: #DFE5EC;
  --color-bg-workspace: #F3F5F8;    --color-bg-surface: #FFFFFF;
  --color-bg-surface-raised: #FFFFFF; --color-bg-hover: #EDF1F6;
  --color-bg-selected: #DCE9F6;
  --color-border-subtle: #DDE3EB;   --color-border-default: #C3CCD8;
  --color-border-strong: #79869A;   --color-border-focus: #1F6FA8;
  --color-text-primary: #18212D;    --color-text-secondary: #455266;
  --color-text-muted: #525F73;      --color-text-disabled: #8C97A6;
  --color-text-on-accent: #FFFFFF;
  --color-accent: #1A6598;          --color-info: #2F5FA6;
  --color-success: #1B7150;         --color-warning: #8A5A0B;
  --color-danger: #A4403A;          --color-offline: #546074;
  --color-unknown: #566275;
}
/* `system` : le thème suit `prefers-color-scheme` tant que l'utilisateur n'a pas choisi. */
```

Les valeurs ont été passées au calcul de contraste (voir §2.4) et de chroma (§2.2) avant d'être retenues ; sept valeurs initiales échouaient et ont été corrigées (texte atténué, bordures de contrôle, alerte, danger clair…).

Correspondance avec l'existant : `--background/--foreground/--card/--primary/--border/--ring/--chart-*` de shadcn sont **réaffectés** à ces jetons (couche de compatibilité) pour que les primitives shadcn/Radix conservées restent fonctionnelles ; les jetons inutilisés (`panel`, `surface`, `accent-pink`, `chrome-*`, `shadow-panel`) disparaissent ; les couleurs d'état ne sont plus dupliquées entre CSS et JS (une seule source, exposée aux graphiques par `getComputedStyle`).

## 4. Formes d'état (jamais la couleur seule)

| État | Forme | Libellé EN / FR | Couleur |
|---|---|---|---|
| Running (`actif`) | ● disque plein | Running / En marche | success |
| Stopped (`arrete`, `en_arret`) | ■ carré plein | Stopped / Arrêtée | offline |
| Paused (`en_pause`) | ‖ double barre | Paused / En pause | info |
| Suspended (`suspendu`) | ◑ demi-disque | Suspended / Suspendue | info |
| Blocked (`bloque`) | ▲ triangle | Blocked / Bloquée | warning |
| Crashed (`plante`) | ◆ losange plein + `!` | Crashed / Plantée | danger |
| Unknown (`inconnu`) | ○ cercle vide | Unknown / Inconnue | unknown |
| Migrating | ⇄ flèches | Migrating / Migration | info (anneau actif) |
| Provisioning | ◌ anneau pointillé | Provisioning / Installation | info |
| Node online / offline / unknown (`en_ligne` / `hors_ligne` / `inconnu`) | ● / ○ barré / ○ | Online / Offline / Unknown | success / offline / unknown |
| Pool active / degraded / inactive / unreachable | ● / ▲ / ■ / ◆ | Active / Degraded / Inactive / Unreachable | success / warning / offline / danger |
| Task running / done / failed | anneau / ✓ / ✕ | Running / Done / Failed | info / success / danger |

Chaque état a un **nom accessible** (`aria-label` ou texte masqué visuellement) : « Running ». Les valeurs de fil restent françaises ; la table `enum.etat.*` fournit les libellés dans les deux langues.

## 5. Règles par composant

### Boutons
- Hiérarchie : **Primaire** (un seul par vue, accent plein), **Secondaire** (bord `border-strong`), **Tertiaire** (texte + icône), **Icône** (nom accessible obligatoire + info-bulle).
- Hauteur 32 px (dense) / 36 px (défaut) ; cible tactile ≥ 44 px sur mobile ; états : repos, survol, actif, focus (anneau), désactivé (`aria-disabled` + raison en info-bulle), chargement (spinner + libellé conservé, largeur stable, double clic bloqué).
- Un bouton de création ouvre un menu **Create** ; les actions rares vivent dans `⋯`.

### Boutons destructifs
- Style : contour danger au repos ; **aplat danger uniquement dans la boîte de confirmation**. Toujours séparés visuellement des actions sûres (séparateur, bas du menu, zone « Danger zone »).
- Confirmation : titre avec verbe + ressource (« Delete VM `db-01` »), liste d'impact exacte (disques supprimés, snapshots/sauvegardes conservés ou non, tâches en cours), et pour les actions irréversibles **saisie du nom** avant d'activer le bouton. Action par défaut au focus = Annuler.

### Badges
Hauteur 20 px, texte 12 px, forme + libellé ; fond `*-subtle`, texte couleur pleine ; jamais plus de deux badges par ligne de tableau ; badges neutres (rôle, `local`) en gris.

### États de VM / de nœud
Voir §4 ; affichés par `StatusIndicator` (forme + texte, option compacte avec info-bulle). En liste : colonne « État » triable ; dans l'arbre : forme seule + nom accessible complet.

### Arborescence (Inventory Explorer)
Ligne 28 px (compact 24 px) ; chevron 16 px (zone cliquable 28 px) ; icône de type 16 px ; libellé tronqué avec info-bulle ; sous-libellé mono `text-muted` ; sélection = `bg-selected` + barre 2 px + semi-gras ; survol = `bg-hover` ; focus clavier = anneau interne ; agrégat d'état sur les parents ; indentation 16 px par niveau, lignes-guides `border-subtle`. Spécification complète en `02` §4.

### Recherche de l'Inventory Explorer
Champ 32 px pleine largeur, icône loupe, placeholder « Find a node or VM » (jamais tronqué : `Find a node or VM` est court), raccourci `/` et `Ctrl/Cmd+K` affiché en `kbd` ; bouton effacer ; compteur « 3 results » en `aria-live="polite"` ; correspondance surlignée (soulignement + `bg-selected`, pas de couleur criarde) ; chemin hiérarchique en sous-libellé (« hl-devhub › Virtual machines ») ; état vide « No node or VM matches “xyz” — Clear search ».

### Tables
Voir §2.9. Alignement : texte à gauche, nombres à droite (mono), états au centre optique ; colonne « Nom » collante à gauche ; sélection multiple avec barre d'actions groupées qui remplace l'en-tête ; préférences de colonnes par vue mémorisées.

### Filtres
Barre de filtres au-dessus du tableau : recherche texte, filtres à puces (« State: Running × »), bouton « Clear all », compteur « 12 of 37 » ; l'état est **dans l'URL** ; filtres enregistrés = vues locales (amélioration).

### Formulaires
Étiquette visible au-dessus du champ (jamais placeholder seul) ; aide sous le champ ; **erreur sous le champ** avec `aria-describedby`, plus un résumé en tête pour les longs formulaires ; validation à la sortie du champ puis à la frappe ; unité dans le champ (`GB`) ; bornes lues de `/host/limits` et affichées (« 1–64 vCPU, host has 16 ») ; bouton principal désactivé seulement avec une raison visible ; **aucune perte de saisie** (brouillon conservé jusqu'à fermeture explicite confirmée).

### Champs avancés
Section « Advanced » repliable (fermée par défaut, **mémorise son ouverture**), badge « Advanced » sur chaque champ qui l'est, aucune option avancée existante supprimée (matrice `01`, colonne « avancé »). Un champ modifié dans « Advanced » affiche un point « modified » quand la section est fermée.

### Modales et panneaux
Dialogue centré (≤ 560 px) pour confirmations et formulaires courts ; **panneau latéral** (≥ 480 px) pour détail/édition sans perdre le contexte ; page pleine pour l'assistant. Piège de focus, retour du focus au déclencheur, `Échap` ferme (sauf action en cours), titre = `aria-labelledby`. Jamais de dialogue empilé sur un dialogue (remplace le `window.prompt` natif actuel).

### Graphiques
Voir §2.9 ; hauteur 160–240 px ; légende cliquable ; pas d'animation d'entrée ; états « chargement », « aucune donnée », « erreur » standardisés.

### Logs
Voir §2.9 ; hauteur adaptable ; virtualisés au-delà de 1 000 lignes ; téléchargement du texte brut.

### Info-bulles
Délai 400 ms, accessibles au clavier (focus), jamais seul support d'une information critique ; contenu court ; `role="tooltip"`.

### Toasts
Coin bas-droit (haut sur mobile) ; succès 5 s ; erreur persistante avec bouton fermer et « Copy details » ; aucune pile de doublons (dédoublonnage par clé) ; zone `aria-live` (`polite` succès, `assertive` erreur) ; le résultat d'une tâche longue est aussi dans le dock.

### Squelettes
Squelette à la forme de la vue (lignes de tableau, tuiles KPI) ; pas de rotation infinie ; après 10 s sans réponse : message « This is taking longer than usual » + Réessayer (repris de l'existant `LoadingState`).

### États vides / erreur / hors ligne / permission refusée
| État | Contenu obligatoire |
|---|---|
| Vide | Pourquoi c'est vide + **une action utile** (« No VMs yet — Create VM ») |
| Erreur | Message normalisé du backend (jamais `[object Object]`), code HTTP, **Réessayer**, « Copy details », lien vers le journal |
| Hors ligne (API injoignable) | Bandeau fixe « Cannot reach the server » + dernière mise à jour + reconnexion automatique avec compte à rebours ; les données en cache restent visibles grisées et marquées « stale » |
| Permission refusée | Icône cadenas + « You don't have permission to view this » + privilège requis (`vm.snapshot`) + « Ask an administrator » ; **pas de spinner infini** (aujourd'hui : Permissions/Journal restent sur « Loading… » pour un observateur) |
| Partiel | Bandeau « 2 of 3 nodes responded » + liste des échecs |

### Focus
Anneau `border-focus` 2 px, décalage 2 px, visible sur tous les contrôles (jamais `outline:none` sans remplaçant) ; lien d'évitement « Skip to content » en premier ; ordre de tabulation = ordre visuel ; `:focus-visible` seulement.

### Responsive
Voir `02` §9 ; cibles tactiles ≥ 44 px ; pas de défilement horizontal de page ; tableaux : défilement horizontal **interne** avec colonne « Nom » collante, ou cartes sous 768 px.

### Accessibilité (WCAG 2.2 AA)
Navigation clavier complète (arbre, tableaux, menus, dialogues) ; noms accessibles pour toute icône ; états annoncés (`aria-live`) ; `aria-sort`, `aria-expanded`, `aria-selected`, `aria-busy` ; cible minimale 24 × 24 px (44 tactile) ; `prefers-reduced-motion`, `prefers-color-scheme`, `prefers-contrast: more` (bordures renforcées) ; test axe automatisé sur chaque écran (déjà 6 aujourd'hui) + parcours clavier automatisé.

## 6. Mouvement

150–250 ms, courbe standard ; uniquement pour : dépliage d'arbre, ouverture de tiroir/panneau, apparition de toast, progression. Aucune animation d'entrée de page, aucun rebond, aucun défilement animé. `prefers-reduced-motion` : transitions instantanées, anneaux de progression remplacés par un pourcentage.

## 7. Bibliothèques et dette technique

- **Conservés** : React 19, Vite, Tailwind 4, shadcn/ui + Radix (primitives : dialogue, menu, onglets, tiroir, infobulle), Zustand, Recharts, `cmdk` (palette), `sonner`, `lucide-react`. **Aucune dépendance lourde ajoutée** ; une bibliothèque de virtualisation légère (`@tanstack/react-virtual`, ~3 ko) est proposée pour l'arbre et les tableaux (décision D-05 dans `06`).
- **Ajoutés (fichiers, pas de paquet)** : polices Plex `woff2` dans `dashboard/public/fonts/`, catalogues `i18n`, jeu de jetons.
- **Retirés au fil du remplacement** : sidebar violette, jetons inutilisés, `RangeToggle` mort, classes `.input`/`.btn-secondary` non définies, doublons de couleurs JS/CSS.

## 8. Vérifications visuelles prévues (phase 2)

Captures desktop / laptop / tablette / mobile en sombre **et** en clair pour chaque écran ; scans axe ; contrôle de contraste automatisé sur les jetons (`contrast.py` devient un test) ; parcours clavier ; test de chaînes longues (IPv6, UUID, chemins, erreurs de 300 caractères) ; revue « une heure devant l'écran » (fatigue) et « rien de rouge quand tout va bien ».
