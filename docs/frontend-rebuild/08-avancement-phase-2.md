# 08 — Phase 2 : livrable final

> État au 2026-09-25. Code sur `hl-devhub:/root/hyperlite-review`, branche `feat/frontend-rebuild`, **commité en local uniquement (rien n'est poussé)**. Instance de dev : `https://100.104.191.72:8443` (version `frontend-3ddfa18`) ; une seule instance de dev et une seule de production (non touchée).

## 1. Avancement du plan (`07`)

| Étape | Sujet | État |
|---|---|---|
| 1–2 | Contrats, jetons, thèmes (sombre / clair / système), polices auto-hébergées, test de contraste | Fait |
| 3 | Cadre : barre latérale, barre du haut, panneau d'activité, palette Ctrl+K, URL et historique du navigateur | Fait |
| 4 | Composants partagés | Fait |
| 5 | Inventory Explorer (modes Serveur / Pool, recherche, clavier, menu contextuel, 4 vues, fenêtrage) | Fait |
| 6–7 | Overview, liste et fiche VM (Résumé, Console, Configurer, Options, Snapshots, Sauvegarde) | Fait |
| 8 | Assistant de création de VM en 8 étapes, dialogue de création de conteneur | Fait |
| 9 | Nœuds : liste, ajout, Résumé, Système, Réseau, Disque, Tâches, Compatibilité, Shell | Fait |
| 10–11 | Stockage, Réseau | Fait |
| 12 | Activité, Journal, alertes, Sauvegardes, Exports, Snapshots globaux | Fait |
| 13 | Sécurité (utilisateurs, groupes, pools, rôles, attributions), SSO, Notifications, Automatisation, Modèles, HA, Compatibilité, Conteneurs | Fait |
| 14–15 | Responsive, accessibilité, non-régression | Fait, testé |
| 16 | Retrait de l'ancienne interface | **Non fait : attend votre validation** (voir §9) |

## 2. Pages et composants reconstruits

Toutes les pages du Datacenter, du nœud et de la VM, les deux assistants de création, l'Explorer, la palette, le panneau d'activité et le menu Actions : 24 fichiers de pages (`src/next/pages/`), 11 composants partagés, 2 assistants (`src/next/wizard/`), 4 modules de logique (`src/next/lib/`), 2 catalogues de traduction (≈ 960 clés chacun, parité testée).

Restent en composants conservés, rendus dans le nouveau cadre : l'écran de connexion, la fenêtre de sécurité du compte (2FA, jetons), la fenêtre de mise à jour, l'éditeur de règles de pare-feu, les zones de dépôt (ISO, disques), la mesure d'historique, et les fenêtres console / terminal / shell qui s'ouvrent à part.

## 3. Confirmation : aucune fonction retirée

Les 1115 lignes de la matrice `01` ont un statut final : 614 reconstruites, 100 composants d'infrastructure conservés, 32 conservés et durcis, 12 vérifiées par e2e, 357 contrats API/backend inchangés. **Aucune n'est « supprimée ».** L'Inventory Explorer (modes Serveur et Pool, recherche « Find a node or VM », clavier, favoris, récents) est présent dans la barre latérale, testé par `next-explorer.spec.ts` (23 tests).

## 4. Améliorations ajoutées (référence `06`)

Celles du premier rapport, plus : confirmation avant chaque action destructrice ou sensible (arrêt, suppression, promotion administrateur, exécution d'une tâche avec ses commandes affichées, reprise HA, restauration), validation par champ avec message à côté (nom, VLAN, tailles, planification, URL, ports, adresses), envoi unique et brouillon confirmé dans les assistants, valeurs « Non fourni par l'API » à la place de valeurs inventées, données d'hôte plus jamais affichées sous un nœud distant, suivi automatique des sauvegardes et exécutions en cours, suivi de tâches borné à 5 minutes, port SSH des nœuds, portée (scope) du SSO, nom des snapshots, cible « nœud » de l'onglet Tâches, correctif de sécurité B1 (A-30).

**Non ajoutées** (backend ou décision requis) : A-05, A-11 à A-15, A-33, A-24, A-26 (page dédiée d'un conteneur), A-34, A-36 à A-40.

## 5. Fichiers

Par rapport à `a33dae5` (dossier `dashboard/`) : **144 fichiers touchés, 87 ajoutés, 57 modifiés, 0 supprimé**, 33 commits, +10 890 / −116 lignes. Côté backend : `app/routers/vms/console.py` et `tests/test_permissions.py` (correctif B1). Seules dépendances ajoutées : `tailwind-merge` et `clsx` (le helper `cn` fusionne enfin les classes Tailwind).

## 6. APIs, services et stores réutilisés

Aucun contrat d'API modifié. Réutilisés tels quels : `api/client.js` (un seul ajout : `formatDetail`, erreurs 422 lisibles), `useAuthStore`, `useInfraStore`, `useConfirmStore`, et les hooks `useHostLimits`, `useLiveVMMetrics`.

## 7. Tests et commandes (résultats du 2026-09-25)

| Série | Résultat |
|---|---|
| Backend (`pytest`) | **258 / 258** |
| Unitaires frontend (`vitest`) | **49 / 49** |
| Nouvelle interface (Playwright, 11 fichiers `next-*`) | **60 / 60** (audit de toutes les pages en 2 thèmes × 2 langues, 5 largeurs d'écran, clavier, accessibilité axe, cycle de vie réel d'une VM, assistants) |
| Ancienne interface (Playwright, 13 fichiers) | **107 / 107** |
| Lint (`eslint`) et format backend (`ruff`) | propres |

```bash
cd dashboard
npm ci
npm run lint
npm test                                  # 49 tests unitaires
npx vite build                            # ancienne interface par défaut
VITE_DEFAULT_UI=next npx vite build       # nouvelle interface par défaut
npx playwright test next-                 # suite de la nouvelle interface
npx playwright test $(ls e2e/tests | grep -v '^next-' | sed 's|^|e2e/tests/|')   # suite de l'ancienne
cd .. && venv/bin/python -m pytest tests -q
```

Limite de méthode : plusieurs tests remplacent l'API d'un composant absent de la machine de test (LXC, nœud distant, modèles) par une simulation ; ils vérifient le comportement de la page, pas ce composant. Les autres tournent contre le vrai backend et le vrai libvirt et vérifient l'état réel après chaque action.

## 8. Limites restantes

- La création de VM ignore le nœud choisi (l'API n'a pas de champ nœud) : l'assistant l'indique.
- Débit réseau de l'hôte, CPU/RAM des nœuds distants, historique de stockage, étiquettes : absents de l'API, affichés « Non fourni ».
- Supprimer un utilisateur le retire de ses groupes mais laisse ses attributions en base (à décider).
- Testé sous Chromium uniquement, sans lecteur d'écran, sans mesure de charge à 1 000 VM dans le navigateur (algorithme testé en unitaire).
- Les pages « nœud distant » n'ont pas été vues sur un vrai second nœud.
- `network.spec` (ancienne suite) reste sujet à une erreur 500 intermittente du backend juste après une suppression de réseau.

APIs backend recommandées : champ nœud à la création de VM, débit et métriques des nœuds distants, étiquettes, suppression des attributions à la suppression d'un utilisateur.

## 9. Retrait de l'ancienne interface (étape 16) — à valider

Aujourd'hui, la nouvelle interface est la seule proposée par une compilation `VITE_DEFAULT_UI=next` ; sans cette variable, l'ancienne reste par défaut et la nouvelle s'ouvre avec `?ui=next`. Le retrait consiste à : (1) rendre la nouvelle interface définitive, (2) supprimer les dossiers de l'ancienne interface devenus inutilisés (`layout/`, `panels/` sauf les composants encore réutilisés, `wizard/`), (3) supprimer l'ancienne suite de tests. Il est réversible par `git revert` du commit de retrait. **Je ne le fais qu'avec votre accord écrit.**
