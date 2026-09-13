# Hyperlite — guide pour Claude Code

Hyperlite est un hyperviseur web maison : FastAPI + libvirt/QEMU-KVM côté
backend, React/Vite côté frontend (`dashboard/`). Il tourne en production sur
un serveur Debian nommé `kvm-lab`, via `systemctl` (unit `hyperlite.service`,
HTTPS auto-signé sur le port 8000). Le dossier `/root/hyperlite` sur ce
serveur EST le déploiement réel — modifier ce dossier modifie ce qui tourne.

## Si tu rejoins ce projet à plusieurs (plusieurs sessions Claude Code)

**Ne travaillez jamais dans le même dossier de travail en même temps.**
Si quelqu'un d'autre code déjà sur `kvm-lab` dans `/root/hyperlite`, clone le
dépôt ailleurs plutôt que d'éditer par-dessus :

```bash
git clone https://github.com/twikles/hyperlite.git /root/hyperlite-<ton-nom>
```

Développe et teste dans ce clone séparé. Le service `systemd` ne lit que
`/root/hyperlite` — ton clone n'affecte rien en prod tant que tu n'as pas
mergé sur `master` et que quelqu'un n'a pas redéployé depuis le vrai dossier.

**Règle non négociable : toujours passer par une branche + Pull Request.**
Jamais de `git push --force` ni de commit direct sur `master` (sauf correctif
trivial et sans risque, ex. `.gitignore`). Workflow standard utilisé sur ce
projet :
```bash
git checkout -b nom-du-chantier
# ... code, teste ...
git add -A && git commit -m "..."
git push -u origin nom-du-chantier
gh pr create --base master --head nom-du-chantier --title "..." --body "..."
gh pr merge <numero> --merge --delete-branch=false   # apres validation
```

## Pièges connus de ce dépôt

- **`hyperlite.db` n'est plus suivi par Git** (retiré volontairement le
  2026-09-13 — le service y écrit en continu, ce qui rendait chaque
  `git checkout`/`merge` conflictuel). Le fichier reste sur disque, ne le
  re-commite pas.
- **SQLite peut renvoyer `database is locked`** sous écriture concurrente
  (le service écrit tâches/audit/métriques en permanence). Rare mais réel,
  observé pendant les tests. Pas encore de vrai correctif (WAL mode /
  busy_timeout) — un candidat pour un futur audit de robustesse.
- **`git checkout <branche>` peut échouer sur `hyperlite.db`** avant sa
  suppression du suivi ; si ça arrive encore ailleurs (un clone qui n'a pas
  encore ce commit), fais `git stash push -- hyperlite.db` avant de changer
  de branche, puis `git stash drop` après.
- **L'API GitHub (`gh pr create`/`gh pr merge`) peut renvoyer des 500
  intermittents** (déjà rencontré) — ce n'est pas un problème côté dépôt,
  réessaie après quelques secondes ; vérifie l'état réel via `git log
  origin/master` plutôt que de faire confiance à la première réponse de `gh`.
- **Toujours tester avant de considérer un chantier terminé** : de
  préférence contre une VM jetable réelle (créée puis détruite par le
  script de test), pas seulement une relecture de code. `python3 -m
  py_compile` sur les fichiers Python touchés, puis `cd dashboard && npm run
  build`, puis `systemctl restart hyperlite && curl -sk
  https://localhost:8000/health` avant de dire qu'une fonctionnalité est
  livrée.

## Où en est la roadmap (mise à jour le 2026-09-13)

Objectif en cours : rapprocher Hyperlite du niveau de vSphere/vCenter, liste
de 16 chantiers triés par charge de travail croissante.

| # | Chantier | Statut |
|---|----------|--------|
| 1 | Horodatage des tâches (Recent Tasks) | ✅ dans `master` |
| 2 | Shell interactif sur l'hôte (WebSocket + pty) | ✅ dans `master` |
| 3 | Logs enrichis (cause d'échec catégorisée) + Journal filtrable | ✅ dans `master` |
| 4 | Snapshots (bug `delete_vm` corrigé, progression) | ✅ dans `master` |
| 5 | Clonage (faille ACL + bug multi-disques corrigés) | ✅ dans `master` |
| 6 | Limites de ressources (cgroups, shares/limites CPU/RAM) | ✅ dans `master` |
| 7 | Mise à jour depuis Git (backup + rollback watchdog) | ✅ dans `master` — **jamais testé en conditions réelles** (arbre toujours sale pendant le dev), à valider avant un vrai `/update/apply` |
| 8 | Rapprochement vSphere général | ✅ analyse écrite (pas de nouveau code, l'essentiel existait déjà) |
| 9 | Réseau virtuel (création, VLAN, pare-feu nwfilter) | ✅ dans `master` |
| 10 | Métriques (collecte continue, historique, Prometheus) | ⏳ branche `chantier10-metriques` poussée, **PR bloquée par une panne GitHub** (500 répétés) — à mergrer dès que possible |
| 11 | Audit de robustesse/sécurité | 🔄 en cours — voir findings ci-dessous |
| 12 | Finalisation Kickstart automatisé | ⬜ pas commencé |
| 13 | Backup/restauration natifs des VM | ⬜ pas commencé |
| 14 | Onglet Automation (moteur de jobs) | ⬜ pas commencé |
| 15 | Multi-nœuds | ⬜ pas commencé |
| 16 | Document récapitulatif final (PDF/Markdown) | ⬜ pas commencé — à faire en dernier |

**Chantier 7, en attente d'un usage réel** : le système de mise à jour
actuel est basé sur `git pull`. Antho a demandé, une fois la liste
terminée, de le remplacer par quelque chose de plus proche du système de
Proxmox (dépôt APT / paquets versionnés) — à ne pas oublier.

### Findings du chantier 11 (audit sécurité), déjà corrigés
- **Endpoints sans le bon niveau de privilège** trouvés et corrigés : upload/
  suppression d'ISO et gestion des templates (`isos.py`, `templates.py`)
  n'étaient protégés que par une simple authentification — n'importe quel
  compte, y compris `observateur` (lecture seule), pouvait créer/supprimer
  des VM via un template ou uploader des ISO. Passés en `require_role
  ("admin")`. `set_vm_cdrom`/`eject_vm_cdrom` sont passés en `vm.hardware`,
  `create_console_ticket` (VNC) en `vm.console` — ce dernier ne respectait
  pas du tout les ACL par VM auparavant (n'importe quel utilisateur pouvait
  obtenir une console sur n'importe quelle VM, même hors de ses droits).
- **Injection XML** dans la création de réseau en mode pont
  (`bridge_name` partait tel quel dans du XML libvirt) — corrigé avec une
  regex de nom d'interface Linux valide.
- **Aucune protection anti-brute-force sur `/auth/login`** — corrigé avec un
  verrou de 5 tentatives / 5 minutes par nom d'utilisateur (en mémoire, se
  réinitialise à un redémarrage du service — limite connue).
- Reste à explorer si quelqu'un reprend l'audit : rate-limiting par IP (pas
  seulement par compte), passage de `hyperlite.db` en mode WAL pour la
  contention SQLite, revue des autres routers (`groups.py`, `pools.py`,
  `acl.py`, `dashboard.py`) pas encore passés en revue ligne à ligne.

## Commandes utiles

```bash
# Backend : compiler/vérifier
python3 -m py_compile app/routers/<fichier>.py

# Frontend : builder
cd dashboard && npm run build

# Déployer et vérifier
systemctl restart hyperlite && sleep 2 && curl -sk https://localhost:8000/health

# Voir les logs du service
journalctl -u hyperlite -n 100 --no-pager
```
