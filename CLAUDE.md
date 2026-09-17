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
| 25 | Refonte visuelle "indigo console" + audit fonctionnel Playwright | ✅ dans `master` — voir section dédiée plus bas |
| 26 | Stockage réseau partagé (pools NFS) | ✅ dans `master` — `POST`/`DELETE /storage` (pools `dir`/`netfs`), UI dans l'onglet Stockage. Testé de bout en bout avec un vrai serveur NFS (curl + UI). 2 bugs préexistants trouvés en testant : `POOL_STATE_NAMES` désynchronisé de l'énum libvirt réelle (tous les pools actifs s'affichaient "en_construction"), `mapPool()` qui codait `type` en dur à `"dir"` côté frontend — corrigés |
| 27 | Migration à chaud de VM entre nœuds | ✅ dans `master` — `POST /vms/{name}/migrate` (admin uniquement), bouton "Migrer" dans l'onglet Résumé de la VM. **Testé réellement de bout en bout entre kvm-lab et serveur-antho (deux vraies machines physiques, sites différents, via Tailscale)** : une VM active a réellement migré à chaud, vérifié des deux côtés (`virsh list`). Voir la section dédiée plus bas pour le détail des 5 bugs réels trouvés en testant et la limite connue (sens nœud distant → kvm-lab non supporté) |
| 28 | Notifications sortantes (email/webhook) | ⬜ pas commencé — demandé le 2026-09-17. Aujourd'hui tout reste dans l'audit log interne, aucune alerte ne sort de l'app |
| 29 | Politique de rétention des sauvegardes | ⬜ pas commencé — demandé le 2026-09-17. Le chantier 13 fait des sauvegardes complètes mais sans purge automatique (garder N quotidiennes/hebdo/mensuelles) |
| 30 | Sécurité du compte : 2FA (TOTP) + jetons API | ⬜ pas commencé — demandé le 2026-09-17. Aujourd'hui JWT de session uniquement, pas de second facteur, pas de jeton dédié à l'automatisation (Terraform/scripts) |

**Chantier 7, en attente d'un usage réel** : le système de mise à jour
actuel est basé sur `git pull`. Antho a demandé, une fois la liste
terminée, de le remplacer par quelque chose de plus proche du système de
Proxmox (dépôt APT / paquets versionnés) — à ne pas oublier.

**Séquencement demandé le 2026-09-17 ("va au-delà de Proxmox", implémenter
étape par étape avec tests à chaque fois)** : 26 (stockage partagé) → 27
(migration à chaud) → 17 (HA, dépend de 26/27 pour avoir du sens réel) →
28 (notifications) → 29 (rétention sauvegardes) → 30 (2FA + jetons API) →
21 (pare-feu datacenter) → 19 (nettoyage VM inactives) → 20 (SSO) → 16
(doc récap, en dernier). Si tu reprends cette session : regarde d'abord
quel chantier de cette liste a le statut le plus avancé dans le tableau
ci-dessus, c'est le point de reprise. Chaque chantier de cette liste doit
être testé en conditions réelles (pas juste relu) avant merge, même
principe que tout le reste de ce document.

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
  **Mystère `partman-lvm/confirm` RÉSOLU (comportement voulu, pas un bug)** :
  l'écran "Écrire les modifications sur les disques et configurer LVM ?"
  apparaît systématiquement, à CHAQUE run, même avec `partman-lvm/confirm
  boolean true` ET `debconf/priority string critical` tous les deux
  preseedés (vérifié en direct sur `/var/lib/cdebconf/questions.dat` via le
  shell de secours de l'installeur, Ctrl+Alt+F2 : `debconf/priority` n'a
  jamais de `Value:` du tout, preseeder cette question n'a aucun effet).
  C'est un garde-fou VOLONTAIRE de Debian Installer contre la perte de
  données : les deux confirmations finales d'écriture disque de partman
  (`partman/confirm`, `partman-lvm/confirm`) ignorent delibérément le seuil
  de priorité pour forcer une confirmation humaine avant toute action
  irréversible — pas contournable proprement par preseed. **Une seule
  touche Entrée reste donc nécessaire à cette étape précise, sur toute
  l'installation** ; documenté dans la bannière de boot, à accepter comme
  limite connue plutôt que continuer à chercher un correctif.
  **Chemin RAID (2+ disques) probablement CASSÉ** par le fix
  `partman-auto/method string lvm` statique (voir commentaire dans
  `preseed.cfg`) — non prioritaire, le cas réel est un serveur unique.
  **BUG CRITIQUE trouvé le 14/09 sur le VRAI serveur physique d'Antho**
  (première appliance jamais installée en dehors de kvm-lab) : le bouton
  mise à jour plantait avec `FileNotFoundError: [Errno 2] No such file or
  directory: 'git'` — **le paquet `git` n'était pas dans la liste de
  paquets installés** (`pkgsel/include`), donc absent de toute appliance
  installée depuis cet ISO. Corrigé : `git` ajouté à `pkgsel/include`
  dans `preseed.cfg`. Un DEUXIÈME bug lié a suivi immédiatement après :
  une fois `git` installé manuellement (`apt install git`) sur ce même
  serveur, la mise à jour restait bloquée sur "Arbre de travail non
  propre" à cause de `scripts/ensure-tls-cert.sh` et
  `scripts/write-motd.sh` — `postinstall.sh` les copiait depuis
  `/root/hyperlite-installer/` (hors de l'arbre git) directement dans
  `$APP_DIR/scripts/`, jamais ajoutés au commit initial, donc "non
  suivis" (`git status --porcelain` → `?? scripts/...`) EN PERMANENCE sur
  CHAQUE appliance, bloquant le bouton mise à jour indéfiniment (pas
  juste au premier boot). Corrigé à la racine : ces deux scripts vivent
  maintenant dans `scripts/` (dépôt principal, comme
  `scripts/update_watchdog.sh` déjà suivi), donc inclus automatiquement
  dans le commit initial via le rsync de `hyperlite-src/` — plus de copie
  séparée ni côté `build-iso.sh` ni côté `postinstall.sh`. **Le serveur
  physique d'Antho a besoin du même correctif manuel** en attendant une
  vraie mise à jour ou une réinstallation : sur ce serveur,
  `git add scripts/ensure-tls-cert.sh scripts/write-motd.sh -f` ne suffit
  pas seul (il faudrait aussi committer) — plus simple d'attendre que ce
  chantier soit mergé puis de refaire un `git fetch && git reset --hard`
  une fois `git` installé, OU de committer localement ces deux fichiers à
  la main sur ce serveur pour débloquer tout de suite.
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

## Refonte visuelle du frontend + audit fonctionnel (2026-09-17)

**Nouvelle direction visuelle "indigo console"** (`chantier25-refonte-ui`,
mergee) : remplace le theme violet/marine du 2026-09-13 (jugee trop
generique/"IA"). Palette indigo (`#4F46E5`), fond clair fixe, sidebar
unifiee (logo + nav + arbre Datacenter dans une seule colonne, voir
`dashboard/src/layout/Sidebar.jsx`). Recolorisation via
`tailwind.config.js`/`index.css`/`theme/colors.js` -- tous les ecrans en
heritent automatiquement, pas retouches un par un. Tableau de bord
(`DatacenterSummaryTab.jsx`) entierement reecrit (tuiles de stats +
graphiques de tendance CPU/Memoire/Reseau reels via `MetricChart` + table
des nœuds + statut VM + taches recentes), aucune donnee inventee.

**Playwright + Chromium headless installes sur kvm-lab** (`npm install
playwright` + `npx playwright install --with-deps chromium`, ~180 Mo dans
`~/.cache/ms-playwright`) pour tester l'UI reelle en conditions reelles au
lieu de se fier a la seule lecture de code -- Antho a explicitement demande
un audit complet ("trouve tout les bug de la web ui... il faut que tout
soit fonctionnel"). **A reutiliser pour toute future modif frontend
significative**, ca a trouve des bugs reels que la relecture de code seule
n'aurait pas vus :
- Bouton sidebar qui ne changeait jamais d'onglet une fois sur la vue
  Datacenter (deux `useEffect` avec la mauvaise dependance dans
  `CentralPanel.jsx` -- corrige avant l'audit complet).
- Menus deroulants du header (cloche/avatar) qui pouvaient rester bloques
  ouverts ou s'afficher tous les deux en meme temps (fermeture uniquement
  au survol, pas de fermeture au clic exterieur) -- corrige (un seul menu
  a la fois, clic exterieur + Echap).
- Sidebar qui prenait tout l'ecran a largeur telephone (~400px), contenu
  inutilisable -- corrige (tiroir superpose sous le seuil `md`).
- `TaskLogPanel` (barre des taches) qui se rendait comme une colonne
  etroite a droite de l'ecran au lieu d'une barre en bas du contenu --
  regression de la refonte visuelle elle-meme (conteneur racine passe en
  `flex-row` pour la nouvelle sidebar pleine hauteur, sans deplacer
  `TaskLogPanel` a l'interieur de la colonne contenu) -- corrige.

**Methode qui a marche** : compte admin temporaire cree directement en
base (`hash_password()` de `app/core/security.py`, jamais via un mot de
passe en clair dans du code commite), script Playwright qui se connecte
pour de vrai, clique systematiquement tous les onglets Datacenter + les
lignes de l'arbre reel + les onglets d'une vraie VM + menus/modales/
assistants, capture toute `console.error`/`pageerror`/reponse HTTP >=400,
screenshots a chaque etape. **Piege rencontre en ecrivant ce genre de
script** : le filtre `hasText` de Playwright fait un test de
sous-chaine, PAS une egalite exacte, meme avec `exact: true` passe a
`page.locator(selector, { hasText, exact })` -- cette option n'existe pas
pour cette forme de `locator()` (seulement pour `getByText`/`getByRole`),
elle est silencieusement ignoree. Resultat concret ici : chercher un
bouton "Sauvegarde" (onglet VM) matchait aussi "Sauvegardes" (item de
sidebar, pluriel) et `.first()` cliquait le mauvais des deux -- a scoper
le `locator()` a un conteneur precis (ex. `div.overflow-x-auto` pour la
barre d'onglets de `Tabs.jsx`) plutot que de compter sur `hasText` seul.
Compte de test supprime de la base a la fin de l'audit.

## Chantier 27 : migration a chaud (2026-09-17)

`POST /vms/{name}/migrate` (`{"target_node": "..."}`, `node=` optionnel
pour la source) -- admin uniquement (deplacer une VM change l'allocation
de ressources du CLUSTER ENTIER, pas juste de cette VM, d'ou
`require_role("admin")` plutot qu'un privilege ACL scope comme
`vm.clone`). Tache asynchrone (meme pattern que `create_snapshot` :
thread separe, `create_task`/`finish_task`, progression via un DEUXIEME
thread qui poll `domain.jobInfo()` sur sa PROPRE connexion). Bouton
"Migrer" dans `VMSummaryTab.jsx`, actif seulement si la VM tourne.

**Testé reellement de bout en bout** avec les deux vraies machines
physiques disponibles (kvm-lab + serveur-antho, sites differents relies
par Tailscale) : creation d'une VM de test, migration a chaud reelle
kvm-lab -> serveur-antho **confirmee des deux cotes** (`virsh list`sur
serveur-antho montrait la VM "en cours d'execution", plus presente du
tout sur kvm-lab), puis retour serveur-antho -> kvm-lab. Tres peu
probable que cette combinaison de bugs ait pu etre trouvee par la seule
lecture de code.

### Bugs reels trouves en testant (dans l'ordre rencontre)

1. **`VIR_MIGRATE_PERSISTENT` n'existe pas** dans cette version de
   libvirt-python (verifie via `dir(libvirt)`) -- c'est
   `VIR_MIGRATE_PERSIST_DEST`. Plantait le thread AVANT le premier
   `jobInfo()`, HORS du `except libvirt.libvirtError`, donc la tache
   restait bloquee "en_cours" pour toujours sans aucune trace cote UI.
   **Corrige a deux niveaux** : bon nom de constante + un `except
   Exception` generique ajoute en filet de securite (toute erreur
   inattendue doit quand meme cloturer la tache).
2. **Reseau de destination inactif** (`serveur-antho`, installe
   manuellement donc jamais passe par l'installeur Hyperlite, qui lui
   demarre `default` automatiquement) -- migration refusee par libvirt.
   Corrige en ajoutant `_ensure_networks_active()` : Hyperlite demarre
   lui-meme les reseaux necessaires sur la destination avant de migrer,
   plutot que d'echouer et de laisser deviner.
3. **ISO cloud-init jamais copiee** : `VIR_MIGRATE_NON_SHARED_DISK` ne
   copie QUE les disques `device='disk'`, jamais les CD-ROM
   `device='cdrom'` -- l'ISO cloud-init (creee pour chaque VM, voir
   `vm_builder.py::create_cloudinit_iso`) manquait donc systematiquement
   sur la destination. Corrige avec `_copy_file_to_node()` (scp via la
   cle SSH du cluster, meme cle que les connexions qemu+ssh://) --
   uniquement depuis un nœud SOURCE local (Hyperlite n'a pas d'acces
   fichier direct a un nœud distant, limite documentee dans le code).
4. **`VIR_MIGRATE_TUNNELLED` casse** avec `VIR_MIGRATE_PEER2PEER` sur
   cette version de libvirt (9.0.0, Debian 12) des que l'URI qemu+ssh://
   porte des parametres de requete (`keyfile=`/`no_verify=1`/
   `sshauth=privkey`, nos URI de cluster en ont toujours) : erreur
   interne opaque ("la clé de l'argument 'host' ne doit pas avoir une
   valeur Null"), tres probablement un bug de libvirt lui-meme dans ce
   chemin de code precis. **Abandonne le tunnel SSH** (accepte le
   compromis : le flux de donnees QEMU passe directement entre les deux
   hotes, pas par le tunnel SSH -- viable ici car un nœud enregistre
   doit deja etre joignable directement pour SSH, donc l'est presque
   toujours pour ce flux aussi). Passe de `domain.migrate()` a
   `domain.migrateToURI3()` (API plus moderne, params typés).
5. **Hostname auto-rapporte non resolvable** : sans le preciser, QEMU
   tente de resoudre le PROPRE nom d'hote de la destination tel qu'IL le
   connait de lui-meme (`hyperlite.home` pour serveur-antho -- non
   resolvable depuis kvm-lab, qui ne le joint que par IP Tailscale)
   plutot que l'adresse par laquelle Hyperlite l'a effectivement joint.
   Corrige en fournissant `migrate_uri` explicitement (parametre type
   `migrate_uri`, PAS `uri` malgre le nom de la constante Python
   `VIR_MIGRATE_PARAM_URI` qui vaut la chaine `"migrate_uri"`) avec la
   meme adresse `hostname` deja enregistree pour la connexion SSH.

### Limite connue, non contournee (documentee plutot que masquee)

**Migrer un nœud DISTANT vers kvm-lab ne fonctionne pas.** La migration
peer-to-peer est initiee par le libvirtd SOURCE (celui du nœud distant),
qui doit pouvoir se connecter LUI-MEME vers kvm-lab -- ca demande une
confiance SSH INVERSE (nœud distant -> kvm-lab) qui n'existe pas (seule
kvm-lab -> nœud distant est mise en place, voir `cluster.py`). Tente en
reel, echoue avec `Attempt to migrate guest to the same host` (le
libvirtd source interprete `qemu:///system` comme lui-meme). **Bloque
explicitement cote backend** (`422` avec message clair, pas une
tentative qui echoue en silence dans un thread) et **filtre cote
frontend** (la sidebar de migration ne propose meme pas kvm-lab comme
destination si la VM est deja distante). Le sens normal (depuis kvm-lab,
ou tourne Hyperlite, vers un nœud distant) est le sens teste et
fonctionnel -- c'est aussi le sens que l'UI utilise dans l'immense
majorite des cas reels (un seul kvm-lab, plusieurs nœuds geres depuis
lui).

**CPU heterogene entre nœuds** : ajoute `_compute_migratable_cpu_xml()`
(`vm_builder.py`) -- calcule un CPU "plus petit denominateur commun" via
`conn.baselineCPU()` entre TOUS les nœuds du cluster a la CREATION d'une
VM, pour qu'elle reste migrable plus tard, au lieu de `host-model` seul
(fige sur le CPU exact du nœud qui a cree la VM). Retombe sur
`host-model` (comportement inchange) si un seul nœud existe ou si le
calcul echoue pour N'IMPORTE quelle raison. **Rencontre reellement entre
kvm-lab et serveur-antho** : `baselineCPU()` echoue purement et
simplement avec `Unknown CPU model Skylake-Client-v3` -- les deux hotes
tournent des VERSIONS DE QEMU/libvirt differentes, donc des bases de
modeles CPU differentes, et le modele exact detecte sur l'un est
carrement inconnu de l'autre. Confirme que le repli sur `host-model` ne
casse rien (VM de test creee et demarree normalement) mais signifie que
la migration entre CES DEUX machines precises necessite un CPU generique
compatible des deux cotes (verifie manuellement avec succes en testant :
`<cpu mode='custom' match='exact'><model fallback='forbid'>qemu64</model>
<feature policy='disable' name='svm'/></cpu>` -- `qemu64` a le feature
`svm` [virtualisation AMD] active par defaut, qui casse le demarrage sur
un hote Intel, d'ou le `disable` explicite). Pas d'automatisation de ce
contournement dans l'UI pour l'instant -- documente ici pour la
prochaine fois plutot que redecouvert a chaque fois.

**Actions VM (start/stop/delete/etc.) toujours locales uniquement** :
confirme en testant le nettoyage post-migration -- `POST /vms/{name}/stop`
et `DELETE /vms/{name}` n'acceptent PAS de parametre `node` (seul
`GET /vms`/`GET /vms/{name}` et maintenant `POST /vms/{name}/migrate`
le font). Deja documente comme limitation deliberee du chantier 15/24
("visibilite seulement"), reconfirme ici en la percutant reellement --
pas un nouveau bug, juste la preuve que la limite documentee est toujours
d'actualite.
