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
- **`database is locked` sous écriture concurrente** : rencontré à plusieurs
  reprises, corrigé le 2026-09-13 (mode WAL + timeout 30s sur `get_conn()`).
  Si ça revient malgré tout sous forte charge, c'est le premier endroit à
  regarder.
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
| 10 | Métriques (collecte continue, historique, Prometheus) | ✅ dans `master` |
| 11 | Audit de robustesse/sécurité | ✅ passe partielle dans `master` — voir findings ci-dessous, pas exhaustif |
| 12 | Finalisation Kickstart automatisé | 🔄 en cours (branche `chantier12-kickstart-multi-os`, dans ce clone `/root/hyperlite-ami`) — la base RHEL (Anaconda)/Ubuntu (autoinstall) était déjà dans `master`. Ajoute Kali (Debian-installer, preseed embarqué dans l'initrd) et Alpine (apkovl) ; RHEL 10 et Ubuntu 26.04 *desktop* restent à vérifier par un vrai boot. **Windows non couvert** : l'ISO uploadée en prod (`26100...SERVER_LOF...iso`) n'est PAS un media d'installation (pas de `setup.exe`/`sources/boot.wim`, c'est un ISO de compléments de langue) — bloqué tant que la vraie ISO d'installation Windows n'est pas fournie. Voir note "Répartition en cours" ci-dessous. |
| 13 | Backup/restauration natifs des VM | ✅ dans `master` |
| 14 | Onglet Automation (moteur de jobs) | ✅ dans `master` |
| 15 | Multi-nœuds (qemu+ssh://) | ✅ dans `master` — testé en boucle sur kvm-lab lui-même (pas de second hôte disponible), pas de vrai test inter-sites |
| 16 | Document récapitulatif final (PDF/Markdown) | ⬜ pas commencé — à faire en dernier |
| 17 | Onglet HA (Load Balancing, Ceph) | ⬜ pas commencé — demandé le 2026-09-13. Dépend largement du chantier 15 (multi-nœuds) pour avoir du sens |
| 18 | Conteneurs (LXC) et tout l'outillage associé | 🔄 en cours (branche `chantier18-conteneurs`, dans `/root/hyperlite-ami`) — pilote LXC natif de libvirt (lxc:///system), image de base Debian 12 via debootstrap, terminal web SSH (même clé d'automatisation que les VM), onglet Datacenter dédié. Tests réels en cours sur ce host. Docker envisagé, pas retenu pour cette première itération (LXC colle mieux à l'architecture libvirt existante) |
| 19 | Suppression automatique des VM inactives (option à la création, ex. 7 jours sans usage) | ⬜ pas commencé — demandé le 2026-09-13 |
| 20 | SSO (LDAP/OIDC/SAML — à préciser) | ⬜ pas commencé — demandé le 2026-09-13. Aujourd'hui authentification locale uniquement (`app/core/security.py`, JWT) |
| 21 | Pare-feu réseau/cluster | ⬜ pas commencé — demandé le 2026-09-13. **Attention, existe déjà en partie** : pare-feu **par VM** (nwfilter) fonctionnel dans `app/routers/vms.py` (`FirewallConfig`/`set_vm_firewall`) + UI dans `VMHardwareTab.jsx`. Ce chantier = un niveau réseau/global, pas repartir de zéro |
| 22 | Onglet "Système" sur le node | ✅ déjà fait avant cette demande — `dashboard/src/panels/node/NodeSystemTab.jsx`, branché dans `CentralPanel.jsx` (id `system`, "Résumé système"), données réelles (`/health` + historique métriques du chantier 10) |

**Chantier 7, en attente d'un usage réel** : le système de mise à jour
actuel est basé sur `git pull`. Antho a demandé, une fois la liste
terminée, de le remplacer par quelque chose de plus proche du système de
Proxmox (dépôt APT / paquets versionnés) — à ne pas oublier.

**Répartition en cours (2026-09-13)** : un collègue travaille depuis son
propre clone (`/root/hyperlite-ami`, session `hyperlite-ami-cf` si tu veux
le contacter via SendMessage/ListAgents) sur deux chantiers en parallèle :
- **Chantier 12** (branche `chantier12-kickstart-multi-os`) : Kali (preseed
  embarqué dans l'initrd) et Alpine (apkovl) ajoutés à l'installation
  automatisée. Windows bloqué (l'ISO uploadée n'est pas un média
  d'installation valide, voir chantier 12 ci-dessus). RHEL 10 et Ubuntu
  26.04 desktop restent à vérifier. **Ne retouche pas
  `app/core/unattended_install.py` ni `get_vm_provisioning` sans
  coordination** — attends sa PR plutôt que de dupliquer le travail.
- **Chantier 18** (branche `chantier18-conteneurs`) : support conteneurs
  LXC, voir ci-dessus. **Ne retouche pas `app/core/container_builder.py`,
  `app/routers/containers.py` ni `open_lxc_conn` sans coordination.**

Tests réels en cours sur ce host (VM/conteneurs jetables, hors service
HTTP — zéro interférence avec ce qui tourne sur `/root/hyperlite`). Prévenu
avant tout `systemctl restart hyperlite`.

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
- **Mode WAL activé sur `hyperlite.db`** (`app/core/database.py::get_conn`,
  timeout de connexion porté à 30s) suite à un vrai `database is locked`
  rencontré en testant le chantier 13 -- corrige la contention SQLite citée
  plus haut comme piège connu.
- Reste à explorer si quelqu'un reprend l'audit : rate-limiting par IP (pas
  seulement par compte), revue des autres routers (`groups.py`, `pools.py`,
  `acl.py`, `dashboard.py`) pas encore passés en revue ligne à ligne.

### Notes diverses (2026-09-13)
- **Mot de passe root/admin de l'appliance ISO fixé à `hyperlite`** (au lieu
  d'aléatoire, demande explicite d'Antho) — `installer/partman-auto.sh`.
  Volontairement simple pour une première connexion facile, PAS pensé pour
  rester tel quel : à changer immédiatement après install. Documenté comme
  point de vigilance à reprendre dans le chantier 16 (checklist sécurité).
- **L'ISO appliance embarque maintenant un vrai commit Git** (voir
  `installer/build-iso.sh`) — avant ce correctif, le code copié sur une
  appliance fraîche n'était PAS un dépôt Git (`.git` explicitement exclu du
  rsync), donc le bouton de mise à jour (chantier 7) ne pouvait pas
  fonctionner dessus et il fallait reconstruire/reflasher un ISO entier à
  chaque version. Corrigé : un unique commit (pas tout l'historique de
  kvm-lab) est créé dans le code embarqué au moment de fabriquer l'ISO —
  suffisant pour que `git fetch` + `git reset --hard origin/<branche>`
  fonctionne ensuite normalement (testé réellement contre le vrai dépôt
  GitHub, voir le commit du chantier 15).
- **Tailscale installé sur kvm-lab** (`100.88.184.24`) en vue de connecter
  un second serveur physique d'Antho, sur un réseau différent (deux sites
  distants, pas juste deux machines du même LAN) — connexion pas encore
  finalisée du côté du second serveur au moment d'écrire cette note. Une
  fois les deux machines liées au même compte Tailscale, enregistrer le
  second serveur comme nœud via `POST /nodes` avec son adresse `100.x.x.x`.

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
