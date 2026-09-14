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
| 18 | Conteneurs (LXC) et tout l'outillage associé | ✅ dans `master` — pilote LXC natif de libvirt (lxc:///system), image de base Debian 12 via debootstrap (cache, clonage rapide par conteneur) **ou image Docker Hub/registre OCI au choix** (`skopeo`+`umoci`, pas de démon Docker requis ; bootstrap post-pull systemd+openssh+sudo côté apt, openrc+openssh+sudo côté apk, création des nœuds `/dev` manquants — testé réellement sur `alpine:3.19` et `debian:12`), terminal web SSH (même clé d'automatisation que les VM), sudo NOPASSWD, onglet Datacenter dédié + champ de sélection d'image dans les deux formulaires de création. **systemd-networkd, pas ifupdown/isc-dhcp-client** (côté apt) : le profil AppArmor de libvirtd sur cet hôte bloque un signal vers dhclient, cassait `destroy`/suppression (trouvé et corrigé en testant). Pas encore fait : snapshots/clonage de conteneur, ACL granulaire (réservé admin pour l'instant), galerie de templates visuelle |
| 23 | Export/Import de VM depuis un fichier disque | ✅ dans `master` — bouton "Exporter le disque" (menu d'actions VM, disque système uniquement, chaud ou froid selon l'état, réutilise le mécanisme du chantier 13), onglet Datacenter > Exports (liste/télécharge/supprime, téléchargement par ticket à usage unique), option "Importer un disque existant" dans le formulaire de création de VM (upload + sélection). Bug réel trouvé et corrigé en testant : un disque importé garde le netplan MAC-épinglé de son tout premier démarrage (cloud-init) — nouvelle MAC = plus aucune interface ne correspond, réseau mort. Corrigé via un ISO de "reseed" cloud-init (nouvel instance-id, même mécanisme que le clonage chantier 5) qui force cloud-init à régénérer son réseau. Testé réellement de bout en bout (export à chaud + import + SSH fonctionnel) |
| 24 | Refonte tableau de bord + barre latérale façon Proxmox VE | ✅ dans `master` — rail de navigation (`SidebarRail.jsx`) ajouté à gauche de l'arbre Datacenter/Nœud/VM existant (purement additif, l'arbre reste les raccourcis VM), calqué sur les onglets Datacenter réels seulement (pas la liste complète de Proxmox). Nouvel onglet "Activité récente" (table `tasks` existante, pas encore exposée au niveau Datacenter). "Statut des VM" devient une vraie liste sur les états réels d'un domaine libvirt. **Pas de vérification visuelle possible depuis cette session (pas de navigateur connecté) — à confirmer par Antho** |
| 19 | Suppression automatique des VM inactives (option à la création, ex. 7 jours sans usage) | ⬜ pas commencé — demandé le 2026-09-13 |
| 20 | SSO (LDAP/OIDC/SAML — à préciser) | ⬜ pas commencé — demandé le 2026-09-13. Aujourd'hui authentification locale uniquement (`app/core/security.py`, JWT) |
| 21 | Pare-feu réseau/cluster | ⬜ pas commencé — demandé le 2026-09-13. **Attention, existe déjà en partie** : pare-feu **par VM** (nwfilter) fonctionnel dans `app/routers/vms.py` (`FirewallConfig`/`set_vm_firewall`) + UI dans `VMHardwareTab.jsx`. Ce chantier = un niveau réseau/global, pas repartir de zéro |
| 22 | Onglet "Système" sur le node | ✅ déjà fait avant cette demande — `dashboard/src/panels/node/NodeSystemTab.jsx`, branché dans `CentralPanel.jsx` (id `system`, "Résumé système"), données réelles (`/health` + historique métriques du chantier 10) |

**Chantier 7, en attente d'un usage réel** : le système de mise à jour
actuel est basé sur `git pull`. Antho a demandé, une fois la liste
terminée, de le remplacer par quelque chose de plus proche du système de
Proxmox (dépôt APT / paquets versionnés) — à ne pas oublier.

**Répartition en cours (2026-09-13)** : un collègue travaille depuis son
propre clone (`/root/hyperlite-ami`, session `hyperlite-ami-5c` si tu veux
le contacter via SendMessage/ListAgents) sur deux chantiers en parallèle :
- **Chantier 12** (branche `chantier12-kickstart-multi-os`) : Kali (preseed
  embarqué dans l'initrd) et Alpine (apkovl) ajoutés à l'installation
  automatisée. Windows bloqué (l'ISO uploadée n'est pas un média
  d'installation valide, voir chantier 12 ci-dessus). RHEL 10 et Ubuntu
  26.04 desktop restent à vérifier. **Ne retouche pas
  `app/core/unattended_install.py` ni `get_vm_provisioning` sans
  coordination** — attends sa PR plutôt que de dupliquer le travail.
- **Chantier 18** : support conteneurs LXC + choix d'image Docker Hub +
  recherche Docker Hub par mot-clé, terminé et mergé (PR #10, #15, #16).
- **Chantier 23** : export/import de VM par fichier disque, terminé et
  mergé (PR #16), voir tableau ci-dessus.
- **Chantier 24** : refonte tableau de bord + barre latérale, terminé et
  mergé (PR #17), voir tableau ci-dessus.
- **Chantier 12, dernier essai Kali (v7, 2026-09-13)** : le correctif
  base64 sur le contenu du fichier `.network` (late_command) n'a **pas**
  résolu le problème — même échec qu'avant : `Failed to process the
  preconfiguration file from file:///preseed.cfg. The file may be
  corrupt.`. Point important pour la suite : cette erreur apparaît **avant**
  toute exécution de `late_command` (c'est un échec de *parsing* du
  preseed.cfg lui-même par debconf, au tout début de l'installeur) — donc le
  bug n'est presque certainement PAS dans le contenu du late_command
  (déjà base64-encodé) mais plus en amont : soit dans la génération du
  preseed.cfg (un caractère qui casse le format debconf ailleurs dans le
  fichier), soit dans la façon dont il est injecté dans l'initrd
  (cpio/gzip, encodage, fin de ligne). Prochaine piste : extraire le
  preseed.cfg réellement présent dans l'initrd généré (pas celui généré en
  Python avant injection) et le comparer octet à octet à un preseed.cfg
  Debian valide connu, plutôt que de continuer à itérer sur le
  late_command.

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
- **Chantier 7, premier vrai test en conditions réelles (2026-09-13) : bug
  trouvé et corrigé.** Le bouton "Vérifier les mises à jour" échouait avec
  `Impossible de contacter le dépôt distant : fatal: could not read
  Username for 'https://github.com': No such device or address`. Cause :
  `hyperlite.service` tourne en root mais **sans `User=`**, donc systemd ne
  positionne pas `$HOME=/root` automatiquement ; le `git fetch` lancé en
  sous-processus par `app/routers/update.py` ne trouvait donc pas
  `~/.gitconfig` (root) où est configuré `credential.helper =
  !gh auth git-credential` — indispensable puisque le dépôt GitHub est
  **privé**. Corrigé en ajoutant `Environment=HOME=/root` dans
  `/etc/systemd/system/hyperlite.service` (`systemctl daemon-reload &&
  systemctl restart hyperlite` après modif). Reproduit et vérifié via
  `env -i PATH=... git fetch` avec/sans `HOME` avant et après le fix.
  **Suite du même jour : dépôt passé en public**, sur le modèle Proxmox
  (dépôt `pve-no-subscription` accessible sans authentification). Ça
  supprime totalement la dépendance à `gh auth login` par machine — une
  appliance fraîche pourra faire `git fetch` sans aucune configuration,
  exactement l'objectif "plus jamais reconstruire d'ISO pour une mise à
  jour". Avant de basculer en public : l'historique complet a été audité
  (aucun secret en clair trouvé — l'exposition du mot de passe admin
  mentionnée dans le commit `08b9172` du 12/09 avait déjà été purgée avant
  cette session) puis **réécrit avec `git filter-repo`** pour retirer
  `hyperlite.db` (suivi par erreur dans ~18 commits très anciens, 11-12
  sept., avant d'être exclu du suivi) de tout l'historique, sur toutes les
  branches, avec force-push. **Tous les hash de commit ont changé** — toute
  personne avec un clone existant doit re-cloner (`hyperlite-ami` prévenu
  et re-cloné). Vérifié par un clone HTTPS anonyme (sans `gh`, `HOME` vide)
  avant et après bascule.
- **Reconstruction de l'ISO appliance (2026-09-13/14) : gros chantier de
  debug en cours, PAS ENCORE COMMITÉ.** Fichiers modifiés (tous dans
  `installer/`, non commités au moment d'écrire cette note — regarder
  `git diff`/`git log` pour voir si c'est toujours vrai) : `build-iso.sh`,
  `preseed.cfg`, `postinstall.sh`, `partman-auto.sh`.
  **Si tu reprends ce travail** : `git diff` sur ces 4 fichiers pour voir
  l'etat exact, et vérifier `virsh list --all` sur kvm-lab pour une VM de
  test `hl-iso-test` qui pourrait etre en cours ou terminée (voir en bas
  de cette note comment l'interpréter).
  **Cause racine trouvée** (la vraie, après plusieurs fausses pistes) :
  l'installeur Debian charge UN SEUL fichier de preseed (le premier trouvé)
  et ignore ensuite le paramètre noyau `preseed/file=/cdrom/...` — notre
  `preseed.cfg` embarqué dans l'initrd (technique déjà en place pour la
  langue) "gagnait" toujours, donc `/cdrom/hyperlite/preseed.cfg` n'était
  JAMAIS lu, et avec lui ni `preseed/include_command` (partman-auto.sh) ni
  surtout `preseed/late_command` (point d'entrée UNIQUE de
  postinstall.sh — donc Hyperlite lui-même n'était jamais installé, alors
  que l'installation Debian se terminait sans aucun écran bloqué).
  Découvert en lisant `/var/log/installer/syslog` sur une VRAIE machine
  installée (`grep -c late_command` → 0). **Corrigé** : `build-iso.sh`
  embarque maintenant directement le `preseed.cfg` du dépôt (une seule
  source de vérité) au lieu de reconstruire un sous-ensemble à la main.
  **Conséquence** : quelques valeurs qui n'existaient QUE dans l'ancien
  mécanisme temporaire (jamais dans `preseed.cfg` lui-même) ont dû être
  ajoutées au vrai fichier après coup, découvertes une par une en
  testant : `apt-setup/cdrom/set-first/set-next/set-failed`,
  `partman-auto/method/choose_recipe`, `partman-auto-lvm/guided_size`,
  `passwd/root-password` (+ mot de passe root repris en dur dans
  `postinstall.sh` si le fichier généré par `partman-auto.sh` manque).
  **Mystère non résolu** : l'écran "Écrire les modifications sur les
  disques et configurer LVM ?" (`partman-lvm/confirm`, déjà preseedé à
  `true`) est quand même apparu une fois pendant un test alors que rien
  d'autre autour n'a bougé — validé manuellement (Entrée) pour ce test,
  PAS re-confirmé sur un run 100% automatique depuis. A vérifier sur le
  syslog d'installation (`/var/log/installer/syslog` sur la VM installée)
  avant de considérer l'ISO fiable à 100% sans surveillance.
  **Chemin RAID (2+ disques) probablement CASSÉ** par le fix
  `partman-auto/method string lvm` statique (voir commentaire dans
  `preseed.cfg`) — non prioritaire, le cas réel est un serveur unique.
  **Comment tester** :
  ```bash
  cd /root/hyperlite/installer && ./build-iso.sh
  qemu-img create -f qcow2 /var/lib/libvirt/images/hl-iso-test.qcow2 12G
  virt-install --name hl-iso-test --memory 2048 --vcpus 2 \
    --disk path=/var/lib/libvirt/images/hl-iso-test.qcow2,format=qcow2,bus=virtio \
    --cdrom /root/hyperlite/installer/hyperlite-appliance-amd64.iso \
    --os-variant generic --network network=default \
    --graphics vnc,listen=127.0.0.1 --noautoconsole --noreboot
  # attendre "shut off" (virsh domstate hl-iso-test), puis :
  virsh start hl-iso-test   # doit booter le systeme installe, pas l'ISO
  # identifiants : root / hyperlite (SSH ou console graphique via
  # virsh screenshot / virsh send-key --codeset linux KEY_X)
  ```
  Si un écran reste bloqué (`virsh screenshot` régulièrement pour voir),
  chercher le nom exact de la question debconf affichée et l'ajouter
  statiquement dans `preseed.cfg` plutôt que de re-deviner. Une fois un
  run 100% automatique confirmé (aucune touche envoyée manuellement) du
  début jusqu'à `root@hyperlite:~#` accessible ET `curl -sk
  https://<ip-vm>:8000/health` qui répond, chantier terminé : commit sur
  une branche (`chantier-iso-fix` ou similaire) + PR, jamais direct sur
  `master` vu l'ampleur du changement.

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
