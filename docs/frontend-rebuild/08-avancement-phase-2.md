# 08 — Avancement de la phase 2 et livrable

> État au 2026-09-25. Le code est sur `hl-devhub:/root/hyperlite-review`, branche `feat/frontend-rebuild`, **commité en local uniquement (rien n'est poussé)**. L'instance de développement (`https://100.104.191.72:8443`) sert la dernière version ; il n'existe qu'une instance de dev et une de production.

## 1. Où on en est par rapport au plan (`07`)

| Étape | Sujet | État |
|---|---|---|
| 1 | Contrats et inventaire | Fait (matrice `01`, tests de contrat unitaires, audit Chromium) |
| 2 | Jetons | Fait (`next.css`, sombre/clair/système, polices Inter + IBM Plex Mono auto-hébergées, test de contraste WCAG) |
| 3 | Cadre global | Fait (barre latérale à groupes repliables, barre du haut, panneau d'activité, palette Ctrl+K, routes historiques conservées avec l'historique du navigateur) |
| 4 | Composants partagés | Fait en partie (indicateur d'état, tuiles, jauge, tendance, menu, collection de VM, notice de permission) |
| 5 | Inventory Explorer | Fait (Server/Pool, recherche, clavier complet, menu contextuel, 4 vues, fenêtrage à grande échelle, tiroir) |
| 6 | Overview | Fait |
| 7 | Liste et fiche VM | Liste et Résumé faits ; onglets Console, Configurer, Snapshots, Sauvegarde : ancien code durci |
| 8 | Création de VM | Ancien assistant durci (envoi unique, erreurs lisibles, brouillon, largeur) ; réécriture en 8 étapes non faite |
| 9 | Nœuds | Résumé fait ; autres onglets : ancien code |
| 10 | Stockage | Fait |
| 11 | Réseau | Fait |
| 12 | Tâches, journaux, alertes | Fait (Activité, Journal, panneau d'activité, alertes dérivées) |
| 13 | Sécurité et paramètres | Non refait (ancien code habillé) ; passerelle administrateur faite |
| 14 | Responsive / accessibilité | Fait et testé (audit Chromium) |
| 15 | Tests et non-régression | Fait pour les pages reconstruites ; matrice mise à jour |
| 16 | Retrait de l'ancienne interface | **Non fait — attend votre validation écrite** |

## 2. Pages reconstruites (nouveau design)

Overview · Machines virtuelles (liste) · Fiche VM (Résumé) · Résumé d'un nœud · Activité · Snapshots (vue globale) · Stockage · Réseau · Journal · Sauvegardes (vue globale) · Exports · Inventory Explorer · Palette de commandes · Panneau d'activité · Menu Actions.

## 3. Ce qui reste en ancien code (dans le nouveau cadre)

Sécurité (utilisateurs, groupes, rôles, ACL, SSO) · Automatisation · Notifications · Conteneurs et son assistant · Modèles · Nœuds (liste, ajout) · HA · Compatibilité · onglets Console/Options/Sauvegarde/Snapshots d'une VM · onglets Système/Réseau/Disque/Tâches/Compatibilité/Shell d'un nœud · connexion. Tous sont parcourus par l'audit Chromium (aucune erreur de console, aucune requête en échec, aucun texte mal traduit, aucun défaut d'accessibilité sérieux).

## 4. Améliorations livrées (référence `06`)

A-01 palette réelle · A-02 arbre clavier · A-03 recherche · A-04 mode Pool réel (D-01, selon recommandation) · A-06 favoris/récents (préférences prêtes, interface minimale) · A-07 historique réel · A-08 fraîcheur · A-09 tâches actives · A-10 alertes dérivées · A-16 erreurs 422 lisibles · A-17 envoi unique + brouillon · A-18 fenêtres à la place de `window.prompt` (5 endroits) · A-19 confirmations (arrêt, redémarrage, arrêt forcé, exécution d'un job, récupération HA) · A-20 droits (passerelle administrateur, plus de 403 en boucle) · A-21 polices auto-hébergées · A-22 français/anglais · A-23 thème système · A-25 densité/colonnes (tableaux triables) · A-28 gestionnaire d'interrogation (pause en onglet caché, recul) · A-29 accessibilité de base · A-31 export CSV · A-32 bandeau nœud distant · A-35 non fait (docs).

**Non ajoutées** : A-05, A-11, A-12, A-13, A-14, A-15, A-33 (nécessitent le backend), A-24, A-26 (page conteneur), A-27 (champs cachés exposés : partiellement — `jusqu_a` du journal, filtres des tâches, ports), A-34, A-36 à A-40.

## 5. Fichiers

- Créés : 63 (dossier `dashboard/src/next/` : pages, composants, mise en page, bibliothèque, i18n, jetons, polices ; 4 fichiers de tests e2e ; 4 fichiers de tests unitaires ; `PromptDialog`, `usePromptStore`, `lib/cn.js`).
- Modifiés : 57, dont 27 primitives `components/ui/*` (seul l'import de `cn` change), `App.jsx` (choix d'interface), `api/client.js` (erreurs lisibles), `package.json` (`tailwind-merge`, `clsx` ajoutés, `cn` retiré), panneaux de l'ancienne interface (correctifs listés en `06`).
- Supprimés : aucun.

## 6. APIs, services et stores réutilisés

Aucune API ni aucun contrat backend n'est modifié. Le client `api/client.js`, `useAuthStore`, `useInfraStore` (données, sélection, actions VM) et `useConfirmStore` sont réutilisés tels quels ; les routes historiques (`/datacenter`, `/node/:id`, `/vm/:id`, `/console/:name`, `/host-shell`, `/container-terminal/:name`) sont inchangées.

## 7. Tests et commandes

```bash
cd dashboard
npm ci
npm run lint
npm test                               # 49 tests unitaires (Vitest)
VITE_DEFAULT_UI=next npm run build     # nouvelle interface par défaut ; sans variable : ancienne par défaut
npm run test:e2e                       # Playwright, démarre un backend réel jetable
```

Fichiers e2e de la nouvelle interface : `next-explorer.spec.ts` (23), `next-audit.spec.ts` (8 : crawl de 31 pages en 2 thèmes × 2 langues, six largeurs d'écran, clavier, fenêtres et menus, assistant, cycle de vie d'une VM réelle), `next-pages.spec.ts` (6). Ancienne suite : 13 fichiers, inchangés sauf trois tests adaptés.

**Résultat au 2026-09-25** : 37 tests sur la nouvelle interface, 49 tests unitaires, ancienne suite 106/107. Le seul échec, `network.spec` (détails et suppression d'un réseau), vient d'une erreur 500 du backend sur `GET /networks` juste après la suppression d'un réseau (libvirt « Network not found »). Un test de l'ancienne suite (arrêt forcé) est instable sous charge et passe seul.

## 8. Limites restantes

- Le nœud n'est toujours pas transmis par le backend à la plupart des routes VM (B3) : l'interface l'indique et n'agit que sur l'hôte local là où l'API l'impose.
- **Faille B1 corrigée (A-30)** le 2026-09-25 : la création du ticket de terminal SSH d'une VM exige désormais le rôle admin (`app/routers/vms/console.py`), avec un test de non-régression (observateur avec ACL « gestionnaire » → 403). Suite backend : 258 tests passent, ruff propre. Commit local `d91105f`.
- Le débit réseau de l'hôte, le CPU/RAM des nœuds distants, l'historique de stockage et les tags n'existent pas dans l'API : l'interface l'écrit au lieu d'afficher une valeur inventée.
- Testé sous Chromium seulement ; pas de test avec lecteur d'écran ; pas de mesure de charge à 1 000 VM dans le navigateur (l'algorithme est testé en unitaire).
- Les scans d'accessibilité sont automatiques (WCAG 2 A/AA, sévérités sérieuse et critique).

## 9. Données simulées

Aucune donnée simulée dans l'application. Un jeu synthétique de 1 000 VM n'existe que dans un test unitaire.

## 10. Retrait de l'ancienne interface (étape 16)

À faire seulement quand : (1) les pages « ancien code » de la section 3 sont reconstruites ou explicitement conservées, (2) la matrice `01` ne contient plus de « décision attendue », (3) vous validez par écrit. Procédure : nouvelle interface par défaut sans variable d'environnement, suppression des dossiers `layout/`, `panels/`, `wizard/` remplacés, gardes des routes historiques conservées en alias ; retour arrière = `git revert` du commit de retrait.
