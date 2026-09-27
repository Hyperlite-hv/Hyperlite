# 06 — Améliorations proposées

> Phase 1 — documentation seulement. **Aucune amélioration n'est implémentée sans votre accord** (colonne « Décision requise »).
> Les inspirations sont fonctionnelles (maturité de Proxmox, vSphere, consoles cloud) ; rien n'est copié.
> Sources : constats de `00-audit-technique.md`, matrice `01`, maquettes `04`.

## 1. Synthèse

| Catégorie | Nombre |
|---|---|
| Réalisables immédiatement avec les données actuelles | 20 |
| Réalisables après validation utilisateur | 7 |
| Nécessitent une nouvelle API / du backend | 8 |
| À préparer pour une version future | 5 |
| **Total** | **40** |


## 2. Réalisables immédiatement avec les données actuelles

| ID | Amélioration proposée | Inspiration fonctionnelle | Problème résolu | Valeur utilisateur | Données nécessaires | Faisabilité | Frontend/API/backend | Risque | Écran concerné | Décision requise |
|---|---|---|---|---|---|---|---|---|---|---|
| A-01 | Palette de commandes réelle (`Ctrl/Cmd+K`) : ressources + actions + navigation | Palettes d'actions des consoles modernes ; recherche globale vSphere | Les raccourcis `⌘K`, `C`, `S` sont affichés mais n'existent pas | Aller partout et agir sans souris | Index de l'Explorer + droits | Élevée (`cmdk` déjà installé) | Frontend | Faible | Top bar, tous | Non |
| A-02 | Arbre clavier complet (↑↓ Home End `*` saisie prédictive, tabindex itinérant, noms accessibles) | Patron WAI-ARIA tree view | `ArrowDown` ne déplace pas le focus (sonde Playwright) ; pastilles d'état sans texte | Conformité WCAG et vitesse de navigation | Aucune | Élevée | Frontend | Faible | Inventory Explorer | Non |
| A-03 | Recherche Explorer : surbrillance, compteur, message vide, recherche par IP/OS/état/nœud, chemin hiérarchique | Recherche de l'inventaire vSphere ; filtre d'arbre Proxmox | Filtre sans retour visuel, masqué < 640 px, placeholder tronqué | Trouver en 2 secondes ; utilisable sur mobile | Champs déjà chargés (`nom`, `ip`, `os`, `etat`, `node`) | Élevée | Frontend | Faible | Inventory Explorer | Non |
| A-06 | Favoris et ressources récentes dans l'Explorer | Favoris/récents des consoles cloud | Retrouver les 5 VM du jour sans chercher | Gain de temps quotidien | Préférence locale (`localStorage`, par utilisateur) | Élevée | Frontend | Faible | Inventory Explorer, palette | Non |
| A-07 | Navigation avec historique réel (`push`) et état complet dans l'URL (sélection, onglet, filtres, mode) | Liens profonds des consoles web | `replace` partout : Précédent ne restitue rien ; liens non partageables | Partage de liens, retour arrière fiable | Aucune | Élevée | Frontend | Moyen (alias des anciennes URL) | Toutes les vues | D-03 (schéma d'URL) |
| A-08 | Indicateur de fraîcheur des données et marquage « périmé » | Horodatage « dernière actualisation » des consoles | Impossible de savoir si l'écran est à jour ; l'erreur initiale reste affichée | Confiance dans l'affichage | Horodatage du dernier `refreshAll` réussi | Élevée | Frontend | Faible | Top bar, Explorer, tableaux | Non |
| A-09 | Indication des tâches actives dans l'arbre et les tableaux (corrélation `/tasks` ↔ ressource) | Barres de tâches récentes vSphere | Rien ne signale qu'une VM est en cours de sauvegarde/migration | Éviter les actions conflictuelles | `GET /tasks?statut=en_cours` (`cible`, `type`) | Moyenne (corrélation par type + cible car pas de `task_id` partout) | Frontend | Moyen | Explorer, listes, VM | Non |
| A-10 | Vue Alertes dérivée (états, stockage ≥ 80/90 %, échecs de tâche, événements d'audit) | Centre d'alertes des consoles d'exploitation | Aucune vue d'alertes ; seuils seulement dans l'audit | Prioriser ce qui demande une action | États, `/storage`, `/tasks`, `/audit` (admin) | Moyenne (dérivé côté client) | Frontend | Moyen (faux positifs si données périmées) | Overview, Activity | Oui : règles de seuil |
| A-16 | Normaliseur d'erreurs unique (chaîne / liste / objets pydantic) | — | 422 affiché `[object Object]` ; erreurs de formes différentes | Messages compréhensibles | Formes documentées (B8) | Élevée | Frontend | Faible | Tous les formulaires | Non |
| A-17 | Protection double envoi + brouillon d'assistant conservé | Assistants de consoles matures | Double clic sur « Create the VM » ; fermeture en plein envoi ; saisie perdue | Zéro doublon, zéro perte | Aucune | Élevée | Frontend | Faible | Create VM/container | Non |
| A-18 | Formulaires à la place de `window.prompt` (clone, modèle, restauration, sauvegarde, conteneurs) | — | Invites natives, sans validation ni contexte | Erreurs évitées, cohérence | Champs existants | Élevée | Frontend | Faible | VM, Containers, Backups | Non |
| A-20 | Droits appliqués aux contrôles (masqué si jamais permis, désactivé avec raison sinon) ; arrêt du sondage après 403 | — | Spinners éternels et toasts toutes les 8 s pour un observateur ; boutons Start/Stop non gardés | Interface honnête, moins de bruit | Rôle de `/auth/me` | Élevée (règle admin/observateur) | Frontend | Moyen (ne jamais élargir un droit) | Tous | Non |
| A-21 | Polices Plex auto-hébergées | — | Google Fonts échoue hors ligne | Installations isolées | Fichiers `woff2` | Élevée | Frontend | Faible | Global | Non |
| A-23 | Thème « système » (`prefers-color-scheme`) en plus du choix manuel, sombre par défaut | — | Démarre en clair et ignore la préférence | Confort visuel | Aucune | Élevée | Frontend | Faible | Top bar | Non |
| A-25 | Densité (Comfortable/Compact/Dense) et colonnes configurables mémorisées | Vues de liste configurables | Une seule densité, colonnes fixes | Adaptation au poste de travail | Préférence locale | Élevée | Frontend | Faible | Tableaux | Non |
| A-28 | Gestionnaire d'interrogation central (pause en onglet caché, recul exponentiel, dédoublonnage, `AbortController`) | — | ~8 requêtes/6 s dont 3× `/nodes`, sondage en onglet caché, aucun délai d'attente, audit inondé par les GET | Charge réduite, batterie, journal d'audit lisible | Aucune | Élevée | Frontend | Moyen (cadence à valider) | Global | Non |
| A-29 | Accessibilité de base : lien d'évitement, focus visible, mouvement réduit, contraste renforcé | WCAG 2.2 AA | Plusieurs manques relevés (§15 de `00`) | Conformité | Aucune | Élevée | Frontend | Faible | Global | Non |
| A-31 | Export CSV des tableaux filtrés | — | Aucun export de listes | Rapports, audits | Données du tableau | Élevée | Frontend | Faible | Tableaux | Non |
| A-32 | Bandeau « nœud distant : certaines opérations agissent sur l'hôte local » et désactivation des onglets non routés | — | Actions silencieusement dirigées vers une VM homonyme locale (B3) | Éviter des erreurs graves | Drapeau `distant` déjà posé (jamais lu) | Élevée | Frontend | Faible | VM/nœud distants | Non |
| A-35 | Corriger `docs/features.md` (Windows est supporté) | — | Documentation contradictoire | Confiance dans la doc | — | Élevée | Docs | Faible | Docs | Non |

## 3. Réalisables après validation utilisateur

| ID | Amélioration proposée | Inspiration fonctionnelle | Problème résolu | Valeur utilisateur | Données nécessaires | Faisabilité | Frontend/API/backend | Risque | Écran concerné | Décision requise |
|---|---|---|---|---|---|---|---|---|---|---|
| A-04 | Mode Pool = pools de ressources réels (appartenance réelle), « Unassigned » | Inventaire par pools/dossiers | Mode Pool actuel : fausses appartenances, doublons, fuite de l'id de nœud | Raisonner par projet/équipe | `/pools` + membres | Élevée | Frontend | Moyen (changement de sens) | Explorer | **D-01** |
| A-19 | Confirmations ajoutées : Restart, arrêt groupé, Automation « Run », HA « Recover » | — | Actions à effet fort sans confirmation | Sécurité opérationnelle | Aucune | Élevée | Frontend | Faible (clics en plus) | VM, Automation, HA | Oui : acceptez-vous des clics de confirmation supplémentaires ? |
| A-22 | Internationalisation EN + FR (catalogues, `Intl`, dictionnaire des valeurs de fil) | — | Chaînes anglaises inline avec fuites françaises | Deux langues à égalité | Catalogues | Élevée | Frontend | Moyen (volume ~1 000 chaînes) | Global | Décidé (EN+FR) ; exception à la règle « tout en anglais » |
| A-24 | Vues enregistrées (filtres + colonnes + tri) locales, puis partageables | Recherches enregistrées vSphere | Refaire les mêmes filtres chaque jour | Vitesse | Préférence locale | Élevée | Frontend | Faible | Listes, Explorer | Oui |
| A-26 | Page de détail conteneur (état, terminal, sauvegardes, clone) et sélection de conteneurs dans l'arbre | Vue d'une ressource par type | Conteneurs seulement dans un onglet ; pas de page ni de sélection | Parité VM/conteneur | `/containers/{n}` (+ actions existantes ; `fetchContainer` sans appelant) | Élevée | Frontend | Moyen (nouvelle surface) | Containers | Oui |
| A-27 | Exposer les capacités déjà servies mais sans écran : suppression de volume, suppression de disque importable, édition d'interface, filtres `type/username/depuis` des tâches, `jusqu_a` de l'audit, `ssh_port`, `use_tls`, `scope` SSO, `subnet_netmask` | — | Fonctions du backend inaccessibles depuis l'UI | Fonctions complètes | Endpoints existants | Élevée | Frontend | Moyen (opérations destructrices) | Storage, Journal, Nodes, SSO, Network | Oui : lesquelles exposer ? |
| A-37 | Étape « Placement » du wizard : nœud choisi ou « local » explicite | — | Le choix du nœud n'a aucun effet | Honnêteté | Aucune (tant que A-13 n'existe pas) | Élevée | Frontend | Faible | Create VM | D-02 |

## 4. Nécessitent une nouvelle API / du backend

| ID | Amélioration proposée | Inspiration fonctionnelle | Problème résolu | Valeur utilisateur | Données nécessaires | Faisabilité | Frontend/API/backend | Risque | Écran concerné | Décision requise |
|---|---|---|---|---|---|---|---|---|---|---|
| A-05 | Endpoint des privilèges effectifs de l'utilisateur (`GET /auth/me/privileges` : par VM/conteneur) | — | Le front ne connaît que `admin` ; un utilisateur ACL n'a aucun bouton | Boutons corrects pour les rôles ACL | Nouvelle route | Moyenne | Backend + frontend | Moyen | Tous | Oui |
| A-11 | Tags de ressources (VM, conteneur, réseau) + filtre et recherche par tag | Étiquettes des consoles cloud | Aucun regroupement transverse | Organisation à grande échelle | Table + routes | Moyenne | Backend + frontend | Moyen | Explorer, listes | **D-09** |
| A-12 | Endpoint groupé d'état/CPU/RAM/disque utilisés de toutes les VM (et ID, dernière activité) | — | Colonnes disque/mémoire utilisée vides ; 1 requête par VM à 1 000 VM | Listes riches et rapides | Agrégation serveur | Moyenne | Backend + frontend | Moyen | Liste VM, Explorer | Oui |
| A-13 | Paramètre `node` sur toutes les sous-routes VM (snapshots, disques, réseau, console, métriques, sauvegardes, clone, création) | — | Actions et lectures sur l'hôte local pour une VM distante (B3) | Gestion multi-nœuds fiable | Extension des routeurs | Faible à moyenne (gros chantier) | Backend | Élevé | VM, wizard | Oui |
| A-14 | API d'événements et d'alertes (flux, accusé de réception, seuils configurables) | Alarmes vSphere / événements Proxmox | Alertes seulement dans l'audit ; seuils fixes | Vraie supervision | Tables + routes | Moyenne | Backend + frontend | Moyen | Alerts, Events | Oui |
| A-15 | Déconnexion serveur (révocation) et rafraîchissement de jeton | — | JWT 4 h sans rafraîchissement ; déconnexion locale | Sécurité et confort | Routes auth | Moyenne | Backend + frontend | Moyen | Session | Oui |
| A-30 | **Sécurité** : exiger admin (ou privilège dédié) pour le terminal SSH d'une VM | — | Un observateur peut ouvrir un shell dans une VM (B1) | Fermer une élévation de privilège | Une ligne dans `console.py` | Élevée | Backend | Faible | Console VM | Oui : correctif séparé, prioritaire |
| A-33 | Renvoyer `task_id` pour sauvegarde, restauration, export, job, création, clone | — | Corrélation ressource ↔ tâche incertaine | Suivi fiable | Extension de réponses | Élevée | Backend | Faible | Tâches | Oui |

## 5. À préparer pour une version future

| ID | Amélioration proposée | Inspiration fonctionnelle | Problème résolu | Valeur utilisateur | Données nécessaires | Faisabilité | Frontend/API/backend | Risque | Écran concerné | Décision requise |
|---|---|---|---|---|---|---|---|---|---|---|
| A-34 | Édition de jobs et de canaux de notification (aujourd'hui création/suppression seulement) | — | Recréer pour modifier | Confort | Routes PATCH | Moyenne | Backend + frontend | Faible | Settings | Plus tard |
| A-36 | Agent invité (qemu-guest-agent) : IP réelles, systèmes de fichiers, utilisation disque, arrêt propre fiable | Outils d'invité vSphere | Pas de données invité | Diagnostic profond | Agent + routes | Faible (hors périmètre actuel) | Backend | Élevé | VM | Plus tard |
| A-38 | Vues d'inventaire partagées entre utilisateurs et glisser-déposer entre pools | Dossiers vSphere | — | Organisation d'équipe | Stockage serveur | Faible | Backend + frontend | Moyen | Explorer | Plus tard |
| A-39 | Fenêtres de maintenance de nœud (évacuation, mode maintenance, redémarrage/arrêt) | Mode maintenance vSphere/Proxmox | Absent | Opérations planifiées | Nouvelles routes + orchestration | Faible | Backend + frontend | Élevé | Nodes | Plus tard |
| A-40 | Pagination et recherche côté serveur pour > 1 000 VM | — | Filtre client suffisant jusqu'à ~1 000 | Grand parc | Paramètres de liste | Faible | Backend + frontend | Moyen | Listes | Si l'échelle l'exige |

## 6. Améliorations de l'Inventory Explorer (index)

| Demande du brief | ID | Catégorie |
|---|---|---|
| Favoris | A-06 | immédiat |
| Ressources récentes | A-06 | immédiat |
| Recherche par IP | A-03 | immédiat |
| Recherche par ID | A-03 (+ A-12 pour l'`id` libvirt, aujourd'hui abandonné par `mapVm`) | immédiat / backend léger |
| Recherche par tag | A-11 | backend |
| Résultats avec chemin hiérarchique | A-03 | immédiat |
| Informations compactes configurables | A-25 | immédiat |
| Indication des tâches actives | A-09 | immédiat |
| Indication de fraîcheur des données | A-08 | immédiat |
| Vue Alerts | A-10 (dérivée) / A-14 (vraie API) | immédiat / backend |
| Tags | A-11 | backend |
| Raccourcis clavier | A-01, A-02 | immédiat |
| Menus contextuels sécurisés | maquette n°5 (mêmes gardes que l'en-tête ; A-20) | immédiat |
| Mode Pool réel | A-04 | après validation |

## 7. Décisions à prendre

| ID | Question | Recommandation | Impact si différé |
|---|---|---|---|
| D-01 | Que signifie le mode **Pool** : pools de ressources (appartenance réelle) ou pools de stockage ? | Pools de ressources ; les pools de stockage restent dans Server par nœud | Le mode Pool actuel est conservé tel quel (avec ses défauts) |
| D-02 | Sentinelle nœud `local` : la conserver côté front ou utiliser le nom d'hôte libvirt ? | Conserver `local` dans l'URL et l'API ; afficher le nom d'hôte | Aucun |
| D-03 | Schéma d'URL `/vms/:node/:name` avec alias des anciennes routes ? | Oui, alias maintenus au moins une version | Le nom seul reste ambigu entre nœuds |
| D-04 | Restart : arrêt brutal (actuel) ou choix « graceful » ? | Proposer les deux, brutal explicite | Restart reste brutal (avec confirmation) |
| D-05 | Autoriser `@tanstack/react-virtual` (~3 ko) pour arbre/tableaux ? | Oui (échelle ~1 000 VM) | Virtualisation maison ou limite de fluidité |
| D-06 | Logo Hyperlite : conserver ou redessiner ? | Conserver en petit, redessiner plus tard | Aucun |
| D-07 | Version anglaise de ces documents avant fusion (règle « tout en anglais ») ? | Oui, avant toute fusion | Non-conformité aux règles du dépôt |
| D-08 | Traiter B1 (terminal SSH pour observateur) comme correctif backend séparé et prioritaire ? | Oui | Élévation de privilège persistante |
| D-09 | Tags : oui/non et sur quelles ressources ? | Oui, VM et conteneurs d'abord | Colonne/recherche par tag désactivées |
| D-10 | Fonctions cachées à exposer (A-27) : lesquelles ? | Toutes sauf la suppression de volume (à décider) | Elles restent inaccessibles depuis l'UI |

