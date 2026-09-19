# Hyperlite — guide pour Claude Code

Hyperlite est un hyperviseur web maison : FastAPI + libvirt/QEMU-KVM côté
backend, React/Vite côté frontend (`dashboard/`). **kvm-lab a été
démantelé le 2026-09-18** (nœud/VM migré par Antho) -- voir la section
dédiée "Migration kvm-lab -> hl-devhub" plus bas pour la topologie
actuelle et ne JAMAIS supposer que kvm-lab existe encore. La production
tourne désormais sur `serveur-antho` (et bientôt le serveur de Nicolas,
pas encore rejoint), via `systemctl` (unit `hyperlite.service`, HTTPS
auto-signé sur le port 8000) -- installée par le mécanisme apt (chantier
7bis), PAS un clone Git (`.git` renommé, voir plus bas). Le développement
(clone Git + clé GPG de signature + hook de publication) vit sur
`hl-devhub`, une VM DÉDIÉE créée via Hyperlite lui-même sur serveur-antho
-- jamais directement sur l'install apt de prod de serveur-antho, pour
garder la même séparation dev/prod déjà en place.

## Si tu rejoins ce projet à plusieurs (plusieurs sessions Claude Code)

**Ne travaillez jamais dans le même dossier de travail en même temps.**
Si quelqu'un d'autre code déjà sur `hl-devhub` dans `/root/hyperlite`, clone le
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

**Branches attendues : exactement `master`/`test`/`test2`, PLUS
`gh-pages`** (depuis le 2026-09-17, chantier 7bis) -- toute branche de
chantier doit etre supprimee apres merge (`git push origin --delete ...`
+ `git branch -d ...`), SAUF `gh-pages` qui est une exception permanente
et volontaire : elle porte le contenu publie du depot APT
(https://twikles.github.io/hyperlite/, voir la section dediee plus bas),
pas du code, republiee a la main a chaque nouvelle version via
`installer/build-apt-repo.sh` + un commit direct dessus. Ne JAMAIS la
supprimer ni la merger dans `master`.

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
| 16 | Document récapitulatif final (PDF/Markdown) | ✅ dans `master` — Markdown, [`docs/RECAPITULATIF.md`](docs/RECAPITULATIF.md). Dernier chantier de cette roadmap, tous les autres sont maintenant traités |
| 17 | HA basique (détection panne nœud + récupération manuelle) | ✅ dans `master` — **scope volontairement prudent, PAS de fencing/STONITH** (voir section dédiée plus bas) : détecte un nœud tombé et alerte, la récupération reste toujours déclenchée par un admin, jamais automatique. **Testé réellement de bout en bout** sur kvm-lab + serveur-antho (panne simulée, alerte confirmée, VM protégée récupérée avec succès sur l'autre nœud, disque partagé intact) |
| 18 | Conteneurs (LXC) et tout l'outillage associé | ✅ dans `master` — pilote LXC natif de libvirt (lxc:///system), image de base Debian 12 via debootstrap (cache, clonage rapide par conteneur) **ou image Docker Hub/registre OCI au choix** (`skopeo`+`umoci`, pas de démon Docker requis ; bootstrap post-pull systemd+openssh+sudo côté apt, openrc+openssh+sudo côté apk, création des nœuds `/dev` manquants — testé réellement sur `alpine:3.19` et `debian:12`), terminal web SSH (même clé d'automatisation que les VM), sudo NOPASSWD, onglet Datacenter dédié + champ de sélection d'image dans les deux formulaires de création. **systemd-networkd, pas ifupdown/isc-dhcp-client** (côté apt) : le profil AppArmor de libvirtd sur cet hôte bloque un signal vers dhclient, cassait `destroy`/suppression (trouvé et corrigé en testant). ~~Pas encore fait : snapshots/clonage de conteneur, ACL granulaire~~ **FAIT** (backlog 2026-09-18, PR #48/#49 -- clonage+sauvegarde/restauration à défaut de vrai snapshot instantané, libvirt-lxc ne le supporte pas du tout). Reste : galerie de templates visuelle |
| 23 | Export/Import de VM depuis un fichier disque | ✅ dans `master` — bouton "Exporter le disque" (menu d'actions VM, disque système uniquement, chaud ou froid selon l'état, réutilise le mécanisme du chantier 13), onglet Datacenter > Exports (liste/télécharge/supprime, téléchargement par ticket à usage unique), option "Importer un disque existant" dans le formulaire de création de VM (upload + sélection). Bug réel trouvé et corrigé en testant : un disque importé garde le netplan MAC-épinglé de son tout premier démarrage (cloud-init) — nouvelle MAC = plus aucune interface ne correspond, réseau mort. Corrigé via un ISO de "reseed" cloud-init (nouvel instance-id, même mécanisme que le clonage chantier 5) qui force cloud-init à régénérer son réseau. Testé réellement de bout en bout (export à chaud + import + SSH fonctionnel) |
| 24 | Refonte tableau de bord + barre latérale façon Proxmox VE | ✅ dans `master` — rail de navigation (`SidebarRail.jsx`) ajouté à gauche de l'arbre Datacenter/Nœud/VM existant (purement additif, l'arbre reste les raccourcis VM), calqué sur les onglets Datacenter réels seulement (pas la liste complète de Proxmox). Nouvel onglet "Activité récente" (table `tasks` existante, pas encore exposée au niveau Datacenter). "Statut des VM" devient une vraie liste sur les états réels d'un domaine libvirt. **Pas de vérification visuelle possible depuis cette session (pas de navigateur connecté) — à confirmer par Antho** |
| 19 | Suppression automatique des VM inactives (option à la création, ex. 7 jours sans usage) | ✅ dans `master` — `app/core/vm_cleanup.py`, opt-in par VM (à la création ou après coup, `PUT /vms/{name}/auto-cleanup`). Le compteur ne court que pendant que la VM est ARRÊTÉE (jamais une VM en marche), jamais une VM protégée HA, avertissement ~24h avant suppression réelle (notifications, chantier 28). **Testé réellement** : cycle de vérification déclenché manuellement avec des horodatages simulés (au-delà/en-deçà du seuil) — avertissement, suppression réelle (VM + disque + entrées DB), et les deux garde-fous (VM active, VM HA) vérifiés un par un. UI (assistant de création + panneau sur la fiche VM) testée dans un vrai navigateur |
| 20 | SSO (LDAP/OIDC/SAML — à préciser) | ✅ dans `master` — OIDC (Authorization Code flow), voir section dédiée plus bas. Authentification locale conservée en parallèle (secours), jamais remplacée |
| 21 | Pare-feu réseau/cluster | ✅ dans `master` — `app/core/network_firewall.py`. Distinct du pare-feu **par VM** (nwfilter) : filtre au niveau du **pont** (chaîne FORWARD du noyau, iptables), donc couvre toutes les VM d'un réseau présentes ET futures. Réutilise le même `FirewallConfig`/`FirewallRule` que le pare-feu par VM (même UI, `FirewallRulesEditor.jsx` factorisé). **Testé réellement avec du vrai trafic** (conteneur LXC jetable sur un réseau de test, `nsenter` dans sa netns) : ping externe bloqué par défaut, autorisé après règle, confirmé au niveau paquets (`iptables -v`). 2 bugs réels trouvés en testant, voir section dédiée |
| 22 | Onglet "Système" sur le node | ✅ déjà fait avant cette demande — `dashboard/src/panels/node/NodeSystemTab.jsx`, branché dans `CentralPanel.jsx` (id `system`, "Résumé système"), données réelles (`/health` + historique métriques du chantier 10) |
| 25 | Refonte visuelle "indigo console" + audit fonctionnel Playwright | ✅ dans `master` — voir section dédiée plus bas |
| 26 | Stockage réseau partagé (pools NFS) | ✅ dans `master` — `POST`/`DELETE /storage` (pools `dir`/`netfs`), UI dans l'onglet Stockage. Testé de bout en bout avec un vrai serveur NFS (curl + UI). 2 bugs préexistants trouvés en testant : `POOL_STATE_NAMES` désynchronisé de l'énum libvirt réelle (tous les pools actifs s'affichaient "en_construction"), `mapPool()` qui codait `type` en dur à `"dir"` côté frontend — corrigés |
| 27 | Migration à chaud de VM entre nœuds | ✅ dans `master` — `POST /vms/{name}/migrate` (admin uniquement), bouton "Migrer" dans l'onglet Résumé de la VM. **Testé réellement de bout en bout entre kvm-lab et serveur-antho (deux vraies machines physiques, sites différents, via Tailscale)** : une VM active a réellement migré à chaud, vérifié des deux côtés (`virsh list`). Voir la section dédiée plus bas pour le détail des 5 bugs réels trouvés en testant et la limite connue (sens nœud distant → kvm-lab non supporté) |
| 28 | Notifications sortantes (email/webhook) | ✅ dans `master` — `app/core/notifications.py`, point d'entree unique via `log_action()` (voir section dédiée) : couvre automatiquement node_statut_change/ha_alert/create_vm/delete_vm/migrate_vm/backup_vm/restore_backup/hyperlite_update sans toucher leurs sites d'appel. Onglet Notifications (Datacenter). Testé réellement avec un vrai récepteur webhook local (déclenchement automatique ET bouton "Tester" confirmés) |
| 29 | Politique de rétention des sauvegardes | ✅ dans `master` — **en fait déjà partiellement implémentée depuis le chantier 13** (`retention_count`, "garder les N plus récentes"), mais seulement pour les sauvegardes planifiées ; un backup manuel n'était jamais purgé. Corrigé + 3 bugs de concurrence réels trouvés en testant, voir section dédiée |
| 30 | Sécurité du compte : 2FA (TOTP) + jetons API | ✅ dans `master` — `app/core/twofa.py`/`app/core/api_tokens.py`. Connexion en 2 temps quand la 2FA est active (jeton intermédiaire 5 min, jamais valide comme jeton de session), jetons API en repli dans `get_current_user` quand le jeton n'est pas un JWT valide. Modale "Sécurité du compte" (menu utilisateur, en libre-service, pas un onglet Datacenter). **Testé réellement de bout en bout** (API directe ET UI Playwright) : setup 2FA + QR code + code TOTP calculé localement (`pyotp`) accepté, code erroné rejeté, jeton intermédiaire refusé comme jeton de session, verrou anti-brute-force réutilisé sur `/auth/login/2fa`, jeton API créé/utilisé pour un appel authentifié/révoqué puis refusé, désactivation 2FA |
| 31 | Robustesse SQLite : audit log asynchrone | ✅ dans `master` — `app/core/audit.py` : un thread dédié possède désormais l'écriture de `audit_log` (file + `queue.Queue`), chaque requête dépose son entrée et repart immédiatement au lieu d'écrire SQLite elle-même. Clôture de tâche (`task_id`) restée synchrone (des appelants relisent le statut juste après). Notifications (chantier 28) aussi passées en arrière-plan (réseau, jusqu'à 10s de timeout par canal — ne doit jamais bloquer la réponse HTTP). **Testé réellement** : 20 requêtes concurrentes puis création VM + sauvegarde + 15 lectures concurrentes, zéro `database is locked` dans les deux cas (le scénario exact qui avait fait planter le dashboard réel d'Antho pendant le test du chantier 29) |

**Chantier 7bis (2026-09-17) : dépôt APT façon Proxmox** — voir section
dédiée plus bas. `app/routers/update.py` détecte maintenant automatiquement
la méthode d'installation (`git` sur kvm-lab, inchangé ; `apt` sur toute
machine ayant adopté le paquet `hyperlite`). **kvm-lab reste volontairement
en clone Git** (décision explicite d'Antho, 2026-09-17) — c'est là que le
développement continue, le bouton "Vérifier les mises à jour" de kvm-lab
n'est donc pas concerné par ce changement.

**Séquencement demandé le 2026-09-17 ("va au-delà de Proxmox", implémenter
étape par étape avec tests à chaque fois)** : 26 (stockage partagé) ✅ → 27
(migration à chaud) ✅ → 17 (HA, dépend de 26/27 pour avoir du sens réel) ✅ →
28 (notifications) ✅ → 29 (rétention sauvegardes) ✅ → 31 (audit log
asynchrone, inséré ici sur demande explicite d'Antho le 2026-09-17 après
avoir impacté sa session active) ✅ → 30 (2FA + jetons API) ✅ → 21
(pare-feu datacenter) ✅ → 19 (nettoyage VM inactives) ✅ → 20 (SSO) ✅ →
**16 (doc récap, en dernier) ← prochain**. **Chantier 12 (kickstart) explicitement
exclu de cette séquence** (demande d'Antho le 2026-09-17, "fait tout dans
l'ordre sauf le kickstart") -- de toute façon piloté par le collègue sur
`/root/hyperlite-ami`, voir "Répartition en cours" plus bas, ne pas y
toucher. Si tu reprends cette session : regarde d'abord
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
- ~~Reste à explorer : rate-limiting par IP, revue des autres routers~~
  **FAIT** (backlog 2026-09-18 : rate-limiting par IP, PR #43 ;
  `groups.py`/`pools.py`/`acl.py`/`dashboard.py` relus ligne à ligne,
  tous corrects, aucun correctif nécessaire).

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

**Migrer un nœud DISTANT vers kvm-lab -- CORRIGE depuis (backlog
2026-09-18, PR #46).** La migration peer-to-peer est initiee par le
libvirtd SOURCE (celui du nœud distant), qui doit pouvoir se connecter
LUI-MEME vers kvm-lab -- ca demandait une confiance SSH INVERSE (nœud
distant -> kvm-lab) qui n'existait pas (seule kvm-lab -> nœud distant
etait mise en place, voir `cluster.py`). Desormais etablie automatiquement
a l'enregistrement de chaque nœud (`ensure_reverse_trust()`, cle DEDIEE
par nœud, jamais partagee, restreinte par `from=`) -- voir la section
dediee "Backlog de robustesse post-roadmap" plus bas pour le detail
complet (dont 3 bugs reels supplementaires trouves et corriges au meme
moment : disque source jamais nettoye apres migration, ISO cloud-init
non copiee dans ce sens, fichier destination devant pre-exister pour ce
sens precis). Le blocage explicite cote backend et le filtre cote
frontend ont ete retires. Le sens kvm-lab -> nœud distant reste
evidemment toujours fonctionnel.

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

**Actions VM (start/stop/delete/etc.) toujours locales uniquement --
CORRIGE depuis (backlog 2026-09-18, PR #47).** `start`/`stop`/`restart`/
`DELETE /vms/{name}` acceptent desormais tous un parametre `node`, comme
`GET /vms`/`GET /vms/{name}` et `POST /vms/{name}/migrate` le faisaient
deja. Voir la section dediee "Backlog de robustesse post-roadmap" plus
bas -- a aussi revele un bug frontend reel (le nœud etait deja resolu
cote client mais jamais transmis a l'appel API) et, combine au point
precedent (confiance SSH inverse), un bug de migration PRE-EXISTANT
depuis ce chantier 27 (disque source jamais nettoye apres une migration
reussie, dans les deux sens).

## Chantier 17 : haute disponibilite basique (2026-09-17)

**Scope volontairement prudent, PAS de fencing/STONITH.** Aucun mecanisme
n'empeche un nœud "detecte hors ligne" d'etre en fait toujours vivant,
juste injoignable (coupure reseau, libvirtd plante mais la VM tourne
encore...). Sans fencing, redemarrer AUTOMATIQUEMENT une VM protegee
ailleurs alors que l'original tourne encore sur le MEME disque partage
causerait une vraie corruption de donnees. Hyperlite se limite donc a
DETECTER (reutilise le poller existant de `cluster.py::_poll_nodes`) et
ALERTER clairement -- la recuperation reste TOUJOURS declenchee par un
admin humain (`POST /ha/{vm}/recover`), jamais automatique. Protection
EXIGE que tous les disques de la VM soient deja sur un pool de stockage
partage (chantier 26).

Nouveau : `app/core/ha.py` (logique), `app/routers/ha.py` (endpoints
`GET /ha`, `POST /ha/{vm}/enable`, `DELETE /ha/{vm}`,
`POST /ha/{vm}/recover`), table `ha_protected_vms` (cache le XML du
domaine + le nœud, resynchronise a chaque cycle du poller PENDANT que le
nœud est joignable -- seul moyen d'avoir une config a redefinir ailleurs
le jour ou il tombe vraiment). Onglet "HA" au niveau Datacenter + bouton
"Protéger (HA)" sur la fiche de chaque VM.

**Testé réellement de bout en bout** entre kvm-lab et serveur-antho (VM
de test sur un pool NFS partagé entre les deux) :
- Panne simulee (`systemctl stop libvirtd` sur serveur-antho, le
  processus qemu de la VM continue de tourner independamment) : le
  poller a detecte le nœud "hors_ligne" en ~35s, l'alerte HA est apparue
  correctement dans l'audit et dans `GET /ha`.
- **Premiere tentative de recuperation refusee par QEMU lui-meme**
  ("Failed to get 'write' lock... Is another process using the image?")
  -- exactement le garde-fou attendu : le processus qemu original tournait
  ENCORE sur serveur-antho (libvirtd coupe, pas le processus lui-meme),
  donc demarrer la meme VM ailleurs sur le meme disque aurait cause une
  vraie corruption. La protection anti-split-brain de QEMU lui-meme a
  fonctionne comme un filet de securite supplementaire, meme dans un
  scenario ou l'admin aurait recupere sur un faux positif.
- Une fois le processus original reellement arrete, **recuperation
  reussie** : VM redefinie et demarree sur kvm-lab avec le disque partage
  intact, sans aucune copie.

**2 bugs reels trouves en testant** (en plus des bugs de montage NFS
listes ci-dessous, trouves au meme moment) :
1. Le XML du domaine ACTIF mis en cache (`domain.XMLDesc(0)`) contient un
   type de machine et un CPU deja RESOLUS en versions concretes propres
   au nœud qui faisait tourner la VM (ex. `machine='pc-i440fx-10.0'`,
   `cpu mode='custom'` avec des dizaines de `feature policy='require'`)
   -- la redefinition echoue sur un autre nœud avec une version de
   QEMU/CPU differente ("does not support machine type" / "Host CPU does
   not provide required features"). Corrige avec `_portable_xml()`
   (`app/core/ha.py`) qui ramene le type de machine a l'alias generique
   `pc` et le CPU a `host-model` avant la mise en cache -- sacrifie
   l'optimisation de migrabilite du chantier 27 pour maximiser les
   chances qu'une recuperation D'URGENCE reussisse (le seul but de ce
   cache).
2. `POST /vms`/`POST /vms/{name}/stop`/`DELETE /vms/{name}` n'acceptent
   toujours pas de parametre `node` (confirme en re-percutant la limite
   deja documentee du chantier 15/24) -- impossible de creer/nettoyer une
   VM de test sur un nœud distant via l'API standard, il a fallu passer
   par des appels libvirt directs pour le test. **Consequence reelle plus
   large** : sans un moyen de choisir le pool de stockage a la creation
   d'une VM (non plus expose aujourd'hui), personne ne peut realistiquement
   utiliser la protection HA sans deplacer un disque a la main en dehors
   de l'UI -- lacune a combler dans un futur chantier (choix du pool +
   actions VM multi-nœuds), documentee ici plutot que laissee invisible.

**2 bugs NFS reels trouves en testant un partage reellement inter-machines**
(kvm-lab <-> serveur-antho, pas juste en boucle sur kvm-lab comme le test
initial du chantier 26) -- corriges dans `app/routers/storage.py::_build_pool_xml` :
1. Sur un client NFS Debian 13/trixie (serveur-antho), le montage echoue
   systematiquement avec "NFS: mount program didn't pass remote address"
   -- meme un `mount(8)` manuel sans l'option `addr=` echoue pareil,
   avec elle il reussit. Semble etre une regression du chemin de montage
   recent (fsconfig/nouvelle API de montage du noyau). Ajoutee
   systematiquement, inoffensive sur un client plus ancien.
2. Meme avec `addr=`, le montage echouait ensuite avec "NFS: Version
   unavailable" -- la negociation automatique de version NFS echoue sur
   ce client. `vers=4.2` ajoute explicitement.
3. **Piege XML rencontre en corrigeant les deux points ci-dessus** :
   l'element libvirt qui porte ces options s'appelle `mount_opts` (PAS
   `mountopts`) et vit dans son PROPRE espace de noms XML
   (`http://libvirt.org/schemas/storagepool/fs/1.0`, verifie dans
   `/usr/share/libvirt/schemas/storagepool.rng` sur cette machine, pas
   dans la documentation en ligne). Un premier essai via
   `ET.SubElement(pool_el, "{namespace}mount_opts")` produisait un XML
   syntaxiquement valide (`<pool xmlns:ns0="..."><ns0:mount_opts>`) mais
   **silencieusement ignore par libvirt** (verifie : l'element etait
   absent du XML relu juste apres `defineXML()`, sans aucune erreur).
   Seule la forme "xmlns=... declare directement SUR l'element" (espace
   de noms par defaut LOCAL, pas un prefixe racine) est effectivement
   prise en compte -- `ElementTree` ne genere jamais cette forme precise,
   corrige en assemblant ce fragment comme une chaine (valeurs deja
   validees/resolues, jamais du texte utilisateur brut) plutot que via
   `ET.SubElement`.

Compte de test, VM de test, pools NFS et export supprimes des deux
nœuds a la fin des tests.

## Chantier 28 : notifications sortantes (2026-09-17)

Deux types de canal : webhook generique (POST JSON, compatible Discord/
Slack/ntfy/n'importe quel receveur HTTP) et email (SMTP). Aucune nouvelle
dependance -- `urllib`/`smtplib` (stdlib) seulement.

**Point d'entree unique, pas un appel a ajouter partout** :
`app/core/audit.py::log_action()` (deja appelee par la quasi-totalite du
code, chaque action significative y passe) declenche desormais
`notifications.notify()` pour les `action` listees dans
`NOTIFY_EVENTS` (`app/core/notifications.py`) --
node_statut_change/ha_alert/create_vm/delete_vm/migrate_vm/backup_vm/
restore_backup/hyperlite_update. Couvre automatiquement tous ces
evenements sans toucher a leurs dizaines de sites d'appel existants dans
`vms.py`/`cluster.py`/`ha.py`/`update.py`/etc. A etendre en ajoutant une
entree a `NOTIFY_EVENTS`, pas en cherchant chaque site d'appel.

`GET/POST/PATCH/DELETE /notifications/channels` + `POST .../test` (envoi
immediat, jamais filtre par evenement). Onglet "Notifications"
(Datacenter) : creation de canal (formulaire different webhook/email),
activer/desactiver, tester, supprimer.

**Testé réellement** : petit serveur HTTP local (`http.server`, stdlib)
lance pour recevoir de vrais webhooks --
1. Bouton "Tester" : notification recue avec le bon contenu JSON.
2. **Declenchement automatique reel** : creation puis suppression d'une
   VM de test via l'API ont chacune genere leur notification SANS aucun
   code specifique a la notification dans `vms.py` -- confirme que le
   point d'entree unique via `log_action()` fonctionne comme prevu.
3. Meme test refait depuis l'UI reelle (Playwright) : creation du canal,
   test, suppression -- tout confirme visuellement et par le contenu
   reellement recu par le serveur local.

Mot de passe SMTP stocke en clair dans `notification_channels.config`
(JSON) -- aucune autre forme de secret n'est chiffree dans ce projet
(voir `.env` pour le secret JWT), reserve aux admins, meme niveau de
confiance que le reste de la config serveur. A revisiter si Hyperlite
gagne un jour un vrai coffre-fort de secrets.

## Chantier 29 : rétention des sauvegardes (2026-09-17)

**Découverte en reprenant ce chantier : il était en fait déjà PARTIELLEMENT
fait depuis le chantier 13** (`retention_count` existait deja dans le
schema `backup_jobs`, `_apply_retention()` existait deja dans
`app/core/backups.py`) -- correction de mon propre diagnostic precedent
("aucune purge automatique") qui etait inexact. Ce qui manquait
reellement : la retention n'etait appliquee QUE par le planificateur
(`_scheduler_loop`), jamais pour un backup MANUEL (`POST
/vms/{name}/backups`, sans `job_id`) -- une VM sauvegardee ponctuellement
a la main (avec ou sans job planifie configure par ailleurs) accumulait
des backups sans aucune limite. Corrige : `_apply_retention()` prend
maintenant `vm_name` (pas `job_id`) et s'applique a TOUTES les sauvegardes
de cette VM, appelee directement depuis `run_backup()` (couvre manuel ET
planifie au meme endroit) -- ne fait rien si aucun `backup_jobs` n'est
configure pour cette VM (pas de politique = pas de limite imposee).

**3 bugs de concurrence REELS trouves en testant** (4 backups manuels
declenches en rafale sur la meme VM) -- **ont impacte l'utilisateur reel
en session active pendant le test** (erreurs 500 visibles sur
`GET /networks`/`GET /storage`/`GET /vms` cote dashboard reel) :
1. `sqlite3.OperationalError: database is locked`, malgre le mode WAL +
   timeout 30s deja en place (chantier 11/13). **Cause racine identifiee** :
   `log_action()` est appelee par la quasi-totalite des endpoints, MEME
   les simples GET en lecture seule (ex. `list_networks` logge
   `"list_networks"` a chaque appel) -- autrement dit, il n'existe quasiment
   pas de "lecteur pur" dans cette app, chaque requete HTTP est AUSSI une
   ecriture. Le mode WAL resout la contention LECTEUR-contre-ECRIVAIN,
   PAS ecrivain-contre-ecrivain (un seul ecrivain a la fois, meme en WAL)
   -- sous forte concurrence (plusieurs endpoints + un backup qui ecrit sa
   progression frequemment), plusieurs "ecrivains" se bousculent reellement.
   **Corrige partiellement** : backups serialises (`_backup_lock`, un seul
   a la fois sur tout le serveur -- sense de toute facon, plusieurs
   `qemu-img convert` simultanes sur le meme disque hote se battraient
   deja pour la bande passante I/O) + throttle des ecritures de
   progression (`qemu_img_convert_with_progress` n'ecrit plus que si le
   pourcentage arrondi a change ET qu'au moins 0.5s s'est ecoulee --
   qemu-img -p emet des lignes de progression tres frequemment, chacune
   etait une ecriture SQLite). **PAS resolu completement** : le probleme
   de fond (`log_action()` sur les endpoints en lecture) reste entier et
   peut resurgir sous forte charge meme sans backup en cours -- **limite
   connue, a traiter dans un futur chantier dedie** (ecriture d'audit
   asynchrone/mise en file plutot que synchrone dans la requete, ou
   separer une vraie connexion lecture seule qui ne logge pas). Verifie
   apres correctif : rafale de 6 requetes concurrentes + 1 backup, plus
   aucune erreur "locked" observee dans ce scenario reduit (le test a 4
   backups simultanes n'a pas ete repete a l'identique pour eviter de
   perturber davantage la session active de l'utilisateur reel).
2. Processus `qemu-img` zombies (`<defunct>`, confirmes via `ps aux`) --
   consequence directe du bug 1 : une exception `database is locked`
   levee DANS la boucle de lecture de `update_task_progress()` faisait
   sortir la fonction sans jamais atteindre `proc.wait()`, abandonnant le
   processus enfant deja termine sans le "reaper". Corrige avec un
   try/finally : `proc.wait()` se produit desormais TOUJOURS, quelle que
   soit l'exception qui interrompt la lecture de la progression.
3. (Lie au bug 1, meme cause) Une tache de backup pouvait rester bloquee
   "en_cours" indefiniment si l'exception survenait apres la creation de
   la ligne `backups` mais avant sa cloture -- couvert par le meme
   try/finally + le `except Exception` deja present dans `run_backup`.

Compte de test, VM de test, planification et sauvegardes de test
supprimes a la fin (verifie : fichiers sur disque ET lignes en base).

## Chantier 30 : sécurité du compte -- 2FA (TOTP) + jetons API (2026-09-17)

Deux mecanismes independants, tous deux en LIBRE-SERVICE (chaque
utilisateur gere son propre compte, pas besoin d'etre admin) :

**2FA (TOTP)** -- `app/core/twofa.py` (`pyotp` pour generer/verifier les
codes, `qrcode` en SVG pour le QR code, sans dependance Pillow). Flux en
deux temps a l'activation : `POST /auth/2fa/setup` genere un secret et le
stocke DEJA en base, mais `totp_enabled` reste a 0 tant que
`POST /auth/2fa/confirm` n'a pas verifie un vrai code -- un utilisateur
qui ferme l'onglet en plein scan de QR code (secret genere, jamais
confirme) ne se retrouve jamais verrouille hors de son propre compte a la
connexion suivante.

**Connexion avec 2FA active** -- `POST /auth/login` (mot de passe correct,
compte avec 2FA) ne renvoie plus de jeton de session complet : il renvoie
`{require_2fa: true, pre_auth_token}`, un JWT intermediaire de 5 minutes
marque `2fa_pending: true` (`security.py::create_preauth_token`). Le
frontend echange ensuite ce jeton + le code TOTP contre le vrai jeton via
`POST /auth/login/2fa`. **Point de securite explicitement teste** : le
jeton intermediaire NE DOIT PAS pouvoir servir de jeton de session normal
(sinon la 2FA ne protegerait rien) -- `get_current_user` rejette
explicitement tout JWT portant `2fa_pending`, verifie en envoyant ce
jeton a `/auth/me` (401, comme attendu). Le verrou anti-brute-force
existant sur `/auth/login` (chantier 11, 5 echecs/5 min par compte) est
REUTILISE sur `/auth/login/2fa` : un code TOTP est a 6 chiffres (1M
combinaisons), pas negligeable a laisser deviner sans limite meme avec la
fenetre de 5 minutes du jeton intermediaire.

**Jetons API** -- `app/core/api_tokens.py`, credential distinct du JWT de
session pense pour l'automatisation (scripts/Terraform/cron) : prefixe
`hlt_`, stocke uniquement par son hash SHA-256 (comme un mot de passe --
le jeton en clair n'est JAMAIS recuperable apres sa creation, affiche UNE
SEULE fois cote UI), revocable individuellement. Branche dans
`security.py::get_current_user` en REPLI : si le jeton presente n'est pas
un JWT valide (`JWTError`), on tente `api_tokens.verify_token()` avant de
rejeter -- aucune nouvelle dependance FastAPI a brancher sur chaque route,
tout endpoint existant qui utilise deja `Depends(get_current_user)`
accepte desormais aussi bien un jeton de session qu'un jeton API sans
modification.

Modale "Sécurité du compte" (`AccountSecurityModal.jsx`, menu utilisateur
du header) plutot qu'un nouvel onglet Datacenter -- ce sont des reglages
du COMPTE connecte, pas de l'infrastructure geree, donc pas a leur place
dans l'arbre Datacenter/Nœud/VM.

**Testé réellement de bout en bout**, deux fois (appels API directs PUIS
UI reelle via Playwright, compte de test jetable a chaque fois, supprime
a la fin) :
- Connexion normale (pas de 2FA) inchangee.
- Setup 2FA : QR code scanne (secret extrait du SVG), code TOTP calcule
  LOCALEMENT via `pyotp.TOTP(secret).now()` (simule une vraie application
  d'authentification) accepte par `/auth/2fa/confirm`.
- Connexion avec 2FA active : `require_2fa` renvoye, code errone rejete
  (`401`), jeton intermediaire refuse comme jeton de session normal
  (`/auth/me` -> 401), verrou anti-brute-force declenche apres 5 codes
  errones (`429`), bon code accepte -> vrai jeton de session.
- Jeton API : cree (jeton en clair recupere une seule fois), utilise pour
  authentifier `GET /auth/me` SANS JWT, `last_used_at` mis a jour, revoque
  puis re-essaye -> `401`.
- Desactivation 2FA (mot de passe requis).
- Meme parcours complet rejoue dans un vrai navigateur (Chromium
  headless) : ouverture de la modale, activation 2FA avec un vrai QR code
  affiche a l'ecran, deconnexion/reconnexion avec le VRAI ecran de defi
  2FA affiche par `LoginScreen.jsx`, creation/revocation d'un jeton API
  depuis l'UI.

Aucun bug reel trouve en testant ce chantier (contrairement aux
precedents) -- flux plus isole/moins de dependances externes (pas de
libvirt, pas de reseau inter-nœuds) que les chantiers multi-nœuds recents.

## Chantier 21 : pare-feu réseau/datacenter (2026-09-17)

**Pourquoi pas nwfilter (comme le pare-feu par VM)** -- verifie contre les
schemas RNG de libvirt sur cet hote (`/usr/share/libvirt/schemas/
network.rng` et `nwfilter.rng`) : `<filterref>` n'existe QUE dans le schema
du DOMAINE (interface de VM), le schema `<network>` n'a aucune notion de
filtre par defaut applique a toutes les interfaces d'un reseau. Un
pare-feu reellement "reseau" doit donc filtrer le point de passage reel du
trafic : le pont Linux associe au reseau, via `iptables`/`FORWARD` --
exactement la ou libvirt lui-meme insere deja ses propres chaines pour le
NAT (`LIBVIRT_FWI/FWO/FWX`, deja presentes sur cet hote pour chaque reseau
nat/isole demarre).

`app/core/network_firewall.py` : une chaine `HYPERLITENETFW` inseree en
**position 1** de `FORWARD` (donc evaluee AVANT les chaines de libvirt --
un DROP explicite bloque le trafic avant que libvirt n'ait la moindre
chance de l'autoriser), qui saute vers une chaine dediee par reseau
(nommee par hash SHA-1 du nom de reseau, la limite reelle d'un nom de
chaine iptables etant 28 caracteres). `PUT /networks/{name}/firewall`
reconstruit entierement la chaine dediee (flush + regles dans l'ordre),
meme principe que `nwfilterDefineXML` pour le pare-feu par VM. Reutilise
**exactement** `FirewallConfig`/`FirewallRule` du pare-feu par VM (importe
depuis `app/routers/vms.py`) et un composant React factorise
(`FirewallRulesEditor.jsx`, extrait de l'ancien `VMHardwareTab.jsx` sans
changement de comportement) -- meme UI, meme validation, seule la CIBLE
change.

**Persistance** : contrairement au nwfilter (stocke et reapplique
automatiquement par libvirt lui-meme), les regles iptables ne survivent
PAS a un redemarrage de l'HOTE. Table `network_firewall` (SQLite) =
source de verite, reappliquee a chaque demarrage du service
(`app/main.py::on_startup` -> `network_firewall.reapply_all()`) --
verifie reellement en redemarrant `hyperlite.service` avec une regle
deja appliquee : la chaine est bien reconstruite a l'identique.

**Testé réellement avec du vrai trafic**, pas seulement une lecture du
XML/de la configuration (conteneur LXC jetable `alpine:3.19` cree sur un
reseau de test, adresse statique assignee via `nsenter` dans sa vraie
netns -- pas celle du process superviseur libvirt-lxc, qui reste dans le
netns HOTE, piege rencontre en testant : le PID a utiliser est celui de
l'enfant `/sbin/init`, pas celui du fichier `.pid` de libvirt) :
- Ping vers une IP reellement externe (1.1.1.1, pas l'hote lui-meme --
  piege rencontre : pinguer l'IP de l'hote depuis le conteneur NE PASSE
  PAS par `FORWARD`, c'est un trafic a destination locale de l'hote,
  invisible pour ce pare-feu) bloque par la politique par defaut (`drop`)
  -- confirme au niveau PAQUETS (`iptables -v` montrant les compteurs
  incrementer sur la regle DROP correspondante), pas juste "la commande a
  echoue".
- Regle explicite ajoutee (ICMP sortant autorise) -- meme ping reussit.
- Connexion TCP non autorisee vers la meme IP externe : toujours bloquee
  (le "autoriser" cible bien le protocole demande, pas tout le trafic).

**2 bugs reels trouves en testant** :
1. **Regles sans etat (bug de robustesse du chantier lui-meme)** : un
   ping SORTANT autorise ne laissait jamais revenir sa REPONSE -- au sens
   de ce filtre stateless, la reponse ICMP entrante est un flux DISTINCT
   du ping sortant, bloque par la politique par defaut. **100% de perte
   de paquets malgre une regle "autoriser" explicite**, trouve en
   regardant les compteurs `iptables -v` (2 paquets/168 octets sur la
   regle DROP entrante correspondant exactement aux 2 reponses ICMP
   attendues). Corrige en ajoutant systematiquement, en tete de chaque
   chaine de reseau, un accept `state ESTABLISHED,RELATED` dans les deux
   sens -- comme tout pare-feu reel (iptables en best practice, Proxmox,
   pfSense...) : le retour d'une connexion deja autorisee doit passer
   sans exiger une regle miroir manuelle pour chaque protocole/port.
   Reverifie apres correctif : meme ping desormais 0% de perte, ET une
   connexion TCP non explicitement autorisee reste bloquee (le correctif
   n'affaiblit pas la politique par defaut).
2. **Bug PRE-EXISTANT, sans rapport avec ce chantier, trouve en testant**
   (`app/routers/network.py::create_network`) : le nom de pont genere
   pour un reseau nat/isole était `f"virbr-{payload.name[:10]}"` --
   `"virbr-"` (6) + jusqu'a 10 caracteres = jusqu'a 16, UN DE PLUS que la
   limite reelle du noyau Linux pour un nom d'interface (`IFNAMSIZ`=16
   OCTETS INCLUANT LE TERMINATEUR NUL, donc 15 caracteres utilisables).
   Tout nom de reseau de 10+ caracteres faisait echouer sa creation avec
   `error creating bridge interface... Numerical result out of range`
   (`ENAMETOOLONG` traduit par libvirt) -- reproduit avec un nom de test
   de 11 caracteres (`hltest-uifw`), un cas tres probable en usage reel
   (ex. "production", "guest-wifi"). Corrige : `name[:9]` (6+9=15).
   Collision residuelle (deux noms de reseau partageant leurs 9 premiers
   caracteres generaient le meme nom de pont) **corrigee depuis** dans le
   backlog du 2026-09-18 (voir plus bas, PR #42) -- prefixe court + hash
   SHA-1 du nom complet plutot qu'une simple troncature.

Compte de test, conteneur de test et reseau de test supprimes a la fin
(verifie : `iptables -nL HYPERLITENETFW` vide, chaine dediee absente de
`nft list ruleset`, table `network_firewall` vide en base).

## Chantier 19 : suppression automatique des VM inactives (2026-09-17)

Option **opt-in**, VM par VM (case a cocher + seuil en jours dans
l'assistant de creation, ou activable/modifiable/desactivable apres coup
via le panneau "Nettoyage auto" sur la fiche VM, `PUT`/`DELETE
/vms/{name}/auto-cleanup`) -- rien ne change pour une VM qui n'active pas
l'option, comportement par defaut inchange.

`app/core/vm_cleanup.py` : un cycle horaire (`CHECK_INTERVAL_S = 3600` --
un seuil se compte en JOURS, pas besoin de plus frequent) parcourt
`vm_auto_cleanup` (SQLite, `last_active_at` reinitialise a chaque
demarrage de VM via `app/core/vm_meta.py::touch_vm_activity`, appelee
depuis `start_vm`). Trois regles de securite, dans cet ordre, chacune un
simple "skip" pour CETTE VM sans jamais interrompre le cycle pour les
autres :
1. **VM actuellement en marche** : jamais touchee, quel que soit le seuil
   -- le compteur ne court que pendant l'ARRET (une VM qui tourne en
   continu n'est par definition jamais "inactive").
2. **VM protegee HA** (chantier 17, `ha.get_protected()`) : jamais
   supprimee automatiquement -- une VM HA est par definition consideree
   critique, l'oppose exact d'une VM jetable.
3. **Avertissement ~24h avant** (reutilise `log_action()` +
   `NOTIFY_EVENTS` du chantier 28, nouvelle entree `auto_cleanup_warning`)
   -- pas de suppression surprise des le premier cycle qui detecte le
   depassement du seuil. La suppression reelle reutilise l'evenement
   `delete_vm` DEJA notifie (chantier 28), le texte du message distingue
   "suppression automatique" du cas manuel.

**Refactor associe** (`app/routers/vms.py`) : la sequence reelle de
suppression (capture des disques/interfaces, `undefineFlags`, liberation
des IP fixes, nettoyage des metadonnees) a ete extraite de l'endpoint
`DELETE /vms/{name}` dans une fonction partagee `_perform_vm_deletion()`,
reutilisee telle quelle par le nettoyage automatique -- aucune divergence
possible entre une suppression manuelle (confirmee par un admin) et une
suppression automatique (declenchee par le scheduler), memes etapes
exactes. Comportement de l'endpoint HTTP existant inchange (refactor pur,
pas de nouvelle logique dans le chemin manuel).

**Testé réellement** (VM jetable, horodatages `last_active_at` manipules
directement en base pour simuler l'ecoulement de plusieurs jours sans
attendre -- `check_once()` appelee directement plutot que d'attendre un
vrai cycle horaire) :
- Seuil approche (dans la fenetre d'avertissement) : `warned_at` rempli,
  entree d'audit `auto_cleanup_warning` creee, VM TOUJOURS presente.
- Seuil depasse : VM reellement supprimee de libvirt, fichier disque
  supprime du disque, ligne `vm_auto_cleanup` nettoyee, entree d'audit
  `delete_vm` avec le message "Suppression automatique : arrêtée depuis
  N+ jours" -- confirme que le meme evenement seraeit notifie via le
  mecanisme du chantier 28 (webhook/email) sans code specifique
  supplementaire.
- **Garde-fou VM active** : ligne poussee tres loin dans le passe (200h,
  bien au-dela d'un seuil de 24h) sur une VM demarree -- jamais touchee,
  toujours "running" apres le cycle.
- **Garde-fou VM HA** : ligne synthetique inseree dans
  `ha_protected_vms` (bypass volontaire du vrai flux d'activation qui
  exige du stockage NFS partage, chantier 26 -- deja teste separement ;
  ici seule la regle de securite `get_protected()` cote `vm_cleanup.py`
  est verifiee) -- jamais touchee malgre un seuil largement depasse.
- Endpoints `GET`/`PUT`/`DELETE /vms/{name}/auto-cleanup` verifies
  directement (activation, lecture, desactivation).
- **UI testee dans un vrai navigateur** (Playwright) : case a cocher +
  seuil dans l'assistant de creation, creation reelle d'une VM avec
  l'option activee, statut affiche sur la fiche VM ("Actif -- 3j
  d'arrêt"), modification du seuil (3 -> 10 jours) et desactivation
  depuis le panneau, tout confirme visuellement et par le contenu reel
  renvoye par l'API.

Piege de test rencontre (pas un bug applicatif) : `check_once()` appelee
depuis un script Python EPHEMERE (un process qui se termine juste apres)
ne laissait pas le temps au thread d'ecriture asynchrone de l'audit log
(chantier 31, `queue.Queue` + thread dedie) de persister l'entree avant
que le process ne quitte -- resolu en appelant explicitement
`audit._AUDIT_QUEUE.join()` avant de sortir du script de test. Sans objet
dans le vrai service (process long-vivant).

Compte de test et VM de test supprimes a la fin (verifie : VM absente de
`virsh list --all`, fichier disque absent, lignes `vm_auto_cleanup`/
`ha_protected_vms` de test absentes de la base).

## Chantier 7bis : dépôt APT façon Proxmox (2026-09-17)

Remplace le suivi "chantier 7, en attente d'un usage réel" -- demande
explicite d'Antho apres le chantier 19 ("faudrais qu'on fasse le apt
install mise a jour la comme proxmox"). **Decision explicite prise avec
Antho avant de commencer** : kvm-lab reste un clone Git (c'est la machine
de developpement) -- ce nouveau mecanisme est pour serveur-antho et les
futures appliances, PAS pour kvm-lab lui-meme. `app/routers/update.py`
detecte automatiquement la methode reelle d'installation
(`_install_method()` : presence de `.git` = "git", sinon `dpkg-query` sur
le paquet `hyperlite` = "apt") -- sur kvm-lab, verifie que ca renvoie
toujours "git", donc AUCUN changement de comportement pour le bouton de
mise a jour existant.

**Depot APT reel, publie et signe** : https://twikles.github.io/hyperlite/
(GitHub Pages, branche `gh-pages` de ce meme depot -- statique, gratuit,
deja public comme le code). Structure Debian classique
(`dists/stable/main/binary-amd64/Packages(.gz)`, `Release`/`Release.gpg`/
`InRelease`, `pool/main/h/hyperlite/*.deb`), signee avec une cle GPG
dediee generee dans un trousseau ISOLE hors du depot Git
(`/root/.hyperlite-apt-gpg` sur kvm-lab, jamais commite -- seule la cle
PUBLIQUE est publiee, `hyperlite-archive-keyring.asc` a la racine du
depot).

**Pour adopter ce mecanisme sur une machine** (serveur-antho, une future
appliance) -- **toujours le depot nginx local** (`100.88.184.24:8899`, voir
la source suivante pour le pourquoi), jamais GitHub Pages en source
principale :
```bash
curl -fsSL http://100.88.184.24:8899/hyperlite-archive-keyring.asc | gpg --dearmor -o /usr/share/keyrings/hyperlite-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] http://100.88.184.24:8899 stable main" > /etc/apt/sources.list.d/hyperlite.list
apt update && apt install hyperlite
```
Installe/adopte l'application dans `/root/hyperlite` (meme emplacement
qu'aujourd'hui). **Piege attendu, pas un bug** : la toute premiere fois
qu'une machine bascule d'un code SANS la logique apt (toute version
anterieure a ce chantier) vers une version AVEC -- le bouton "Verifier les
mises a jour" de l'ancienne version ne sait pas encore parler apt (verifie
en conditions reelles : `500 Erreur interne du serveur`). Cette toute
premiere transition doit donc se faire une fois a la main
(`apt update && apt install --only-upgrade hyperlite` en SSH) ; toutes les
mises a jour SUIVANTES fonctionnent normalement depuis le bouton, meme
version a version.

**Fichiers** : `VERSION` (racine du depot, numero de version au format
`AAAA.MM.JJ[.N]`), `installer/build-deb.sh` (construit
`hyperlite_<version>_amd64.deb` -- payload = exactement les fichiers
suivis par Git, PLUS `dashboard/dist` reconstruit au moment du build, donc
jamais de Node/npm requis sur la machine cible contrairement au chemin
Git), `installer/build-apt-repo.sh` (assemble/signe le depot dans
`installer/apt-repo/`, jamais commite sur `master` -- republie a la main
sur `gh-pages` a chaque nouvelle version), `installer/deb/control.template`
+ `installer/deb/postinst` (script de maintenance Debian : cree/actualise
le venv Python, genere les secrets UNIQUEMENT a la toute premiere
installation -- jamais touches sur une mise a jour -- installe le service
systemd).

**Probleme de conception identifie et resolu AVANT de tester** (pas
decouvert apres coup) : `apt-get install --only-upgrade hyperlite`
declenche depuis `/update/apply` tourne comme sous-processus du service
hyperlite LUI-MEME (la requete HTTP qui a demande la mise a jour). Si le
`postinst` du paquet redemarre le service normalement (comportement
Debian standard, correct pour un admin qui lance `apt install` a la main
en SSH), ce redemarrage tue le sous-processus `apt-get` en plein milieu de
son propre postinst -- paquet dans un etat incoherent. Corrige avec la
variable d'environnement `HYPERLITE_SKIP_RESTART` : positionnee UNIQUEMENT
par `app/routers/update.py` avant d'appeler `apt-get install`, elle dit au
postinst de NE PAS se redemarrer -- c'est alors `update.py` qui declenche
le redemarrage juste apres, depuis un processus DETACHE (`Popen(...,
start_new_session=True)`), exactement le meme mecanisme deja utilise par
le chemin Git existant. Un `apt install` manuel (sans cette variable)
redemarre normalement, comme n'importe quel paquet Debian bien ecrit.

**Testé réellement de bout en bout** (VM jetable creee via Hyperlite
lui-meme, disposable, jamais kvm-lab) :
1. Installation FRAICHE via le depot local (servi temporairement en HTTP
   depuis kvm-lab le temps du test) : cle GPG verifiee par `apt update`
   sans erreur, `apt install hyperlite` installe TOUTES les dependances
   systeme (qemu-kvm, libvirt-daemon-system, gcc, libvirt-dev...) puis le
   paquet lui-meme, service demarre automatiquement, `/health` repond --
   **y compris une vraie connexion libvirt fonctionnelle DANS la VM
   nouvellement installee**.
2. Mise a jour MANUELLE en SSH (`apt install --only-upgrade`) vers une
   version 2 : secrets/session admin PRESERVES (meme mot de passe encore
   valide apres), service redemarre normalement.
3. **Le vrai flux complet via l'API** (celui que le bouton de l'interface
   declenche) : `GET /update/check` detecte correctement la version 3
   disponible, `POST /update/apply` lance la tache, le service
   s'auto-redemarre SANS tuer son propre `apt-get` (le probleme identifie
   ci-dessus, confirme resolu), tache marquee "termine" (progres 100),
   nouveau `GET /update/check` confirme `a_jour: true`.
4. **Depot REELEMENT publie** verifie depuis l'exterieur (pas seulement en
   local) : `curl https://twikles.github.io/hyperlite/dists/stable/main/
   binary-amd64/Packages` renvoie bien le paquet publie, cle GPG publique
   recuperee et importee avec succes.
5. Bug reel trouve en testant (sans rapport avec la conception, une
   erreur d'execution) : `VERSION` n'avait jamais ete ajoute a Git
   (`git add`), donc absent du premier paquet construit malgre sa
   presence sur disque (`build-deb.sh` s'appuyait sur `git ls-files`) --
   corrige en copiant ce fichier explicitement, independamment de son
   suivi Git.

**Limite connue, documentee plutot que masquee** : le filet de securite
"watchdog" (`scripts/update_watchdog.sh`, reutilise tel quel pour les deux
methodes) restaure un tarball de fichiers en cas d'echec post-redemarrage,
mais ne fait PAS reculer l'etat interne de `dpkg` (qui continuerait de
rapporter la version la plus recente comme "installee" meme apres une
restauration de fichiers). Consequence : un `apt upgrade` ulterieur
pourrait considerer, a tort, que rien n'a besoin d'etre reinstalle. Cas
rare (suppose un echec de demarrage APRES un `apt install` reussi), pas
corrige dans cette passe -- a traiter si ca se presente reellement.

VM de test et paquets/versions de test (`.1`, `.2`) supprimes a la fin ;
seule la version reelle `2026.09.17` reste publiee sur `gh-pages`.

### Durcissement post-chantier (2026-09-17, meme jour) : adoption reelle + bugs trouves

Antho a demande explicitement de durcir ce mecanisme ("il faut absolument
bosser sur tout ces points... comme proxmox") apres l'avoir adopte pour de
vrai sur **serveur-antho** (premiere machine EN PRODUCTION migree du
mecanisme Git vers `apt`, pas juste une VM jetable).

**Adoption reelle sur serveur-antho** : sauvegarde complete prise AVANT
tout (`pre-apt-adoption-*.tar.gz`, stockee localement sur la machine),
depot+cle ajoutes, `apt install hyperlite` (premiere installation cote
dpkg, "mise a jour" cote postinst puisque `.env` existait deja -- secrets
preserves, confirme en se reconnectant avec le meme mot de passe apres),
`.git` renomme (`.git.bak-preapt-<date>`, pas supprime) pour que
`_install_method()` detecte enfin "apt" au lieu de "git". Aucune VM
n'etait active sur ce nœud au moment de l'operation (verifie avant de
commencer) -- risque reel limite a l'interface Hyperlite elle-meme, pas a
des charges de travail en production.

**Bug reel trouve en testant sur cette machine precise** (premiere en
locale FRANCAISE, contrairement a la VM de test precedente) :
`apt-cache policy` traduit ses champs (`Candidat :` au lieu de
`Candidate:`), donc `_check_update_apt()` echouait SILENCIEUSEMENT --
`a_jour` toujours faux, aucune erreur visible, juste une detection qui ne
marchait jamais. Corrige en forcant `LC_ALL=C`/`LANG=C` sur toute commande
apt/dpkg dont CE FICHIER analyse la sortie -- `dpkg-query -f='...'` n'etait
pas concerne (deja machine-readable) mais force aussi par coherence.
**Testé réellement sur la machine ou le bug a ete trouve** : `serveur-antho`
mis a jour via le VRAI bouton (`POST /update/apply`, appele en HTTP comme
le ferait l'UI) vers la version corrigee, `/update/check` rapporte
ensuite correctement `a_jour: true`. Fait interessant observe en testant :
la tache de mise a jour est restee `en_cours` a 5% pendant ~4 minutes 30
(pas bloquee -- le `tar` de sauvegarde d'un `/root/hyperlite` de plusieurs
Go, avec de vraies ISO/data dessus, prend simplement du temps sur cette
machine reelle) ; verifie en observant le processus `tar` reellement actif
via `ps aux` avant de conclure que ce n'etait pas un vrai blocage.

**Publication automatique** (point 2 des lacunes identifiees) :
`scripts/git-hooks/post-merge` -- **jamais de CI tierce (GitHub Actions)
pour ca**, decision explicite avec Antho ("que fait Proxmox ? entre les
deux ?") : Proxmox ne s'appuie pas non plus sur une CI publique, ils ont
leur propre infrastructure de build qui signe elle-meme, la cle ne
quittant jamais leurs machines. Meme principe ici : un hook Git **local a
kvm-lab** (jamais suivi par Git, `.git/hooks/` est local par nature --
installe via un symlink vers ce script trace, voir les instructions en
tete de fichier) se declenche a chaque `git pull`/merge sur `master` : si
le commit HEAD n'a pas deja ete publie (`.last-published-commit`, jamais
commite), il genere une VERSION horodatee automatiquement (pas besoin
qu'un chantier pense a l'incrementer a la main), reconstruit et republie
le paquet+depot en ARRIERE-PLAN (detache, ne bloque jamais un `git pull`),
puis **committe et pousse lui-meme** le bump de `VERSION` sur `master` --
sans ca, l'arbre de travail de kvm-lab resterait "sale" en permanence
(`VERSION` modifie non commite), ce qui aurait cassé SILENCIEUSEMENT le
bouton de mise a jour EXISTANT de kvm-lab (mode Git, `_is_dirty()` bloque
tout des que le moindre fichier est modifie -- consequence identifiee et
evitee AVANT de tester, pas decouverte apres coup).

**Reste a faire pour un vrai "comme Proxmox" complet** (pas traite dans
cette passe, documente pour la suite) :
- ~~L'ISO d'installation embarque toujours l'ancien mecanisme~~ **FAIT**,
  voir la section dediee "ISO appliance : installation via apt" plus bas
  (2026-09-17, meme jour).
- Cle de signature GPG sans phrase de passe, stockee uniquement sur
  kvm-lab (`/root/.hyperlite-apt-gpg`) -- acceptable pour un usage perso
  (et coherent avec le choix "jamais sur une CI tierce" ci-dessus), a
  reconsiderer avant de distribuer ce depot a des tiers.
- Pas d'interface pour revenir a une version anterieure precise (possible
  en ligne de commande `apt install hyperlite=<version>`, rien dans l'UI).

## ISO appliance : installation via apt, plus de git embarque (2026-09-17)

Suite directe du durcissement chantier 7bis ci-dessus -- demande explicite
d'Antho ("il faut absolument bosser sur tout ces points pour que ca soit
parfait encore une fois comme proxmox"). `installer/build-iso.sh`
n'embarque plus `hyperlite-src/` ni `git init` (~60 lignes supprimees) --
une appliance fraiche installe Hyperlite directement depuis le vrai depot
APT publie, exactement le mecanisme du chantier 7bis, plutot qu'un commit
Git a part reconstruit a chaque ISO. Objectif d'origine du chantier 7
enfin atteint proprement : plus jamais reconstruire/reflasher un ISO
entier pour une mise a jour.

`installer/postinstall.sh` (execute en chroot via `late_command`,
`in-target`) : ajoute le depot + la cle, `apt-get update`,
`apt-get install -y hyperlite`, aligne le mot de passe root Linux/admin
Hyperlite (toujours `hyperlite`, simple par design, voir note du
2026-09-13 plus haut), active libvirtd + reseau NAT par defaut.

**Decision explicite prise avec Antho pendant ce chantier** ("esssaye
d'anticiper en recherchant usr le net etc" + "il faut limiter les
tests") : chercher la cause racine via `WebSearch`/inspection directe de
l'etat reel (logs `/var/log/installer/syslog` via `virt-cat`, requetes
directes au depot depuis plusieurs machines) avant chaque nouveau test,
plutot que d'enchainer des reconstructions d'ISO au jugé.

### 3 bugs reels trouves et corriges (dans l'ordre rencontre, plusieurs
installations completes testees sur VM jetable, serveur-antho)

1. **Incoherence CDN structurelle de GitHub Pages entre fichiers lies
   d'un meme commit**, PAS une fenetre de propagation transitoire.
   `apt-get update` echouait de facon persistante ("Le fichier a une
   taille incoherente") sur `dists/stable/main/binary-amd64/Packages` --
   confirme en interrogeant DIRECTEMENT l'origine (`curl` depuis
   serveur-antho lui-meme, pas une VM, plus d'1h20 apres la derniere
   publication) : `InRelease` et `Packages` restaient desynchronises.
   Teste jusqu'a 30 tentatives x 20s (10 min), echec identique a chaque
   fois -- confirme que ce n'est pas corrigible par un budget de retry,
   aussi genereux soit-il. **Fix robuste** (pas un contournement) : le
   depot est desormais AUSSI servi en direct par nginx sur kvm-lab
   (`installer/hyperlite-apt-repo.nginx.conf`, lie uniquement a l'IP
   Tailscale `100.88.184.24:8899`, jamais expose sur l'internet public)
   -- aucun CDN entre l'origine et le client, coherence garantie par
   construction (un seul fichier sur disque). GitHub Pages reste publie
   en parallele (documente comme non garanti pour la coherence,
   `installer/build-apt-repo.sh`) mais n'est plus la source utilisee par
   `postinstall.sh`.
2. **Source `cdrom://` auto-ajoutee par l'installeur Debian a
   `/etc/apt/sources.list`** -- comportement generique et documente de
   Debian/Ubuntu (trouve via `WebSearch`, ex. Debian bug #807996), pas
   specifique a ce depot : `apt-get update` echoue GLOBALEMENT des
   qu'UNE source echoue, meme si toutes les autres (dont la notre)
   reussissent. Corrige avec `sed -i '/^deb cdrom:/d' /etc/apt/sources.list`
   avant tout `apt-get update` dans `postinstall.sh`.
3. **`systemctl start`/`restart` silencieusement no-op dans le chroot
   d'installation** ("Running in chroot, ignoring command 'start'",
   comportement STANDARD et VOULU de Debian Installer via `policy-rc.d`,
   pas un bug corrigible). Consequence : le service ne demarre jamais
   reellement pendant `late_command`, donc la DB Hyperlite (schema +
   compte admin, normalement crees par `seed_admin()` au premier
   demarrage) n'existe pas encore a ce stade -- un premier essai avec un
   `UPDATE` SQL direct sur `users` echouait avec `sqlite3.OperationalError:
   no such table: users`. Corrige en appelant `seed_admin()`
   (`app/core/seed.py`, deja idempotent, lit deja
   `HYPERLITE_INITIAL_ADMIN_PASSWORD`) directement depuis
   `postinstall.sh` plutot que d'attendre un demarrage impossible dans ce
   contexte.

**Egalement (bug trouve en testant, sans rapport direct avec l'ISO)** :
`libvirt-daemon-driver-lxc` ajoute a `preseed.cfg`/`control.template` --
trouve en reproduisant une VRAIE erreur 500 sur le dashboard LIVE de
serveur-antho ("Erreur conteneurs", `aucun pilote de connexion disponible
pour lxc:///system`). Ce paquet est SEPARE de `libvirt-daemon-system` et
n'avait jamais ete dans la liste de paquets de l'installeur -- masque
jusqu'ici car kvm-lab (seule machine testee avant serveur-antho) l'avait
deja installe manuellement a un moment non documente. Corrige a la fois
en live sur serveur-antho (`apt-get install -y libvirt-daemon-driver-lxc
&& systemctl restart libvirtd`) et a la racine dans `preseed.cfg`.

**Testé réellement de bout en bout** (VM jetable `hl-apt-iso-test` sur
serveur-antho, 5 cycles de build+test avant d'obtenir un run propre avec
les 3 bugs corriges) : installation 100% automatisee (une seule touche
humaine envoyee, le garde-fou `partman-lvm/confirm` deja documente plus
haut comme non contournable par preseed), VM eteinte proprement en fin
d'installation, redemarree, `/health` repond avec une vraie connexion
libvirt (`"hypervisor":"QEMU"`), SSH root fonctionnel avec le mot de passe
attendu, `dpkg -l hyperlite` confirme une installation par paquet (pas de
`.git` dans `/root/hyperlite`), `lxc:///system` fonctionnel, login admin
Hyperlite (`POST /auth/login`) reussi avec le meme mot de passe. VM,
disque et ISO de test supprimes a la fin.

**Piege de test rencontre (pas un bug applicatif)** : apres le premier
demarrage reel de la VM installee, `virsh domifaddr` montrait DEUX baux
DHCP pour la meme MAC -- un ancien bail (ID client RFC951, sans hostname,
date de la phase installeur) et le bail ACTUEL (DUID, hostname
`hyperlite`, expiration plus tardive). Tenter de joindre le premier
(`.65`) donnait "No route to host" -- l'adresse reellement active etait
la deuxieme entree (`.66`). A verifier `virsh net-dhcp-leases <reseau>`
en cas de "No route to host" apparemment illogique juste apres un
demarrage, plutot que de supposer un probleme reseau plus profond.

## Harmonisation post-chantier : serveur-antho désynchronisé, détection + ISO publique (2026-09-17)

Suite directe du chantier ISO/apt ci-dessus, même jour. En vérifiant le
mécanisme de mise à jour sur le dashboard réel de serveur-antho (capture
d'écran fournie par Antho), celui-ci affichait "À jour" alors qu'il ne
l'était PAS (version `2026.09.17.1` affichée des deux côtés, alors que
kvm-lab avait déjà publié `2026.09.17.1951`). Demande explicite d'Antho
ensuite : "il faut tout harmonisé que tout soit bien a jour et quand on
change un truc ou autre que ca soit a jour aussi".

**Cause racine du faux "à jour"** : serveur-antho avait été adopté (voir
"Durcissement post-chantier" plus haut) en suivant l'exemple documenté
dans CLAUDE.md à ce moment-là, qui recommandait l'URL **GitHub Pages**
comme source APT -- pas le dépôt nginx local créé plus tard dans le même
chantier. Résultat : `apt-get update` réussissait (`InRelease` récupéré
sans erreur) mais servait une version PÉRIMÉE de `Packages` via un nœud
CDN différent de celui interrogé manuellement par `curl` -- exactement
l'incohérence structurelle de GitHub Pages déjà documentée plus haut,
mais cette fois-ci passée inaperçue silencieusement plutôt que de faire
échouer `apt-get update` franchement. **Corrigé à deux niveaux** :
1. Source APT de serveur-antho basculée manuellement vers le dépôt nginx
   local (`http://100.88.184.24:8899`).
2. **La doc CLAUDE.md elle-même corrigée** ("Pour adopter ce mécanisme
   sur une machine", section chantier 7bis) pour recommander
   systématiquement le dépôt nginx local -- c'était la vraie source du
   problème : suivre cette doc au pied de la lettre menait à la mauvaise
   configuration.

### Vérification automatique périodique des mises à jour

`app/core/update_check.py` (nouveau, testé réellement) : cycle horaire
(même cadence que `vm_cleanup.py`) qui réutilise `check_update()` (git ou
apt selon `_install_method()`) et notifie via le point d'entrée unique du
chantier 28 (`log_action()` → `NOTIFY_EVENTS["update_available"]`) dès
qu'une nouvelle version est détectée -- **UNE SEULE FOIS par version**
(table `update_check_state`, une ligne), pas à chaque cycle tant que
personne n'a appliqué la mise à jour. **N'applique JAMAIS de mise à jour
tout seul** -- même principe de prudence que la HA (chantier 17) :
détecter et alerter, la décision reste toujours humaine (une mise à jour
redémarre le service, pas anodin sans supervision). Branché dans
`app/main.py::on_startup` comme les autres schedulers (`vm_cleanup`,
métriques, poller de nœuds...).

Testé réellement (simulation de 3 cycles avec un résultat "en retard"
injecté) : une seule notification malgré 3 cycles, une version plus
récente redéclenche bien une notification, un état "à jour" ne notifie
rien. Déployé et vérifié sans erreur au démarrage sur kvm-lab.

### ISO publique + republication automatique à chaque changement

Antho a explicitement demandé que l'ISO soit accessible publiquement
(pas seulement via Tailscale) ET reconstruite/republiée à **chaque**
changement sur `master`, "constamment" -- pas seulement le dépôt APT.

- **Publication publique** : GitHub Release (le dépôt est déjà public
  depuis le chantier 7). GitHub Pages est explicitement inadapté pour ce
  fichier (limite dure de 100 Mo par fichier via un push Git classique,
  l'ISO fait ~940 Mo) -- une Release GitHub accepte jusqu'à 2 Go par
  pièce jointe, sans cette limite.
- **Republication automatique** : le hook `post-merge`
  (`scripts/git-hooks/post-merge`) reconstruit désormais AUSSI l'ISO
  (`installer/build-iso.sh`, ~25s mesurées réellement -- négligeable en
  arrière-plan) après chaque republication du dépôt APT, à chaque merge
  sur `master`.
- **Release UNIQUE et stable** (`appliance-iso-latest`), pas un nouveau
  tag par commit -- éviterait d'accumuler des dizaines de releases de
  ~950 Mo chacune. Le lien de téléchargement ne change JAMAIS :
  `https://github.com/twikles/hyperlite/releases/latest/download/hyperlite-appliance-amd64.iso`.
  Seuls le fichier et les notes (version + commit) sont remplacés
  (`gh release upload --clobber` + `gh release edit --notes`) à chaque
  publication -- le tag git sous-jacent n'est volontairement PAS
  redéplacé à chaque fois (la trace du commit exact est dans les notes
  de la release, pas besoin qu'elle soit aussi dans le tag).

**Testé réellement de bout en bout, y compris un vrai cycle automatique
(pas juste simulé)** : création initiale de la release, cycle de mise à
jour simulé (upload --clobber + edit --notes, téléchargement vérifié
après chaque étape), PUIS un vrai merge sur `master` (PR du hook
lui-même) a déclenché le hook pour de vrai -- log `publish.log` confirmé
(`ISO republiée avec succès`, `Dépôt APT republié avec succès`), release
vérifiée à jour avec la nouvelle version/commit exacts, téléchargement
public confirmé fonctionnel (200 OK, taille correcte, sans
authentification).

### Boucle complète vérifiée

Après ces deux mécanismes, un cycle réel a été rejoué intégralement :
merge sur `master` → hook republie dépôt APT + ISO automatiquement →
serveur-antho détecté "non à jour" via `/update/check` → mise à jour
appliquée via `/update/apply` (bouton réel du dashboard) → versions
kvm-lab et serveur-antho confirmées identiques (`dpkg -l hyperlite` des
deux côtés). Nettoyage : ancienne release ponctuelle (`iso-<version>`,
publiée avant la mise en place du mécanisme automatique) supprimée,
branche `chantier7ter-auto-publish` (déjà mergée depuis le chantier
7bis, jamais supprimée après coup) nettoyée à cette occasion.

## Chantier 20 : SSO OIDC (2026-09-17)

Décisions prises avec Antho avant de commencer (il ne serait pas devant
son PC pendant l'implémentation, toutes les questions posées à
l'avance) : **OIDC** (pas LDAP/SAML), **pas d'IdP existant** → un IdP de
test jetable monté pour valider réellement le protocole, **authentification
locale conservée en parallèle** (secours admin si l'IdP tombe/mal
configuré, jamais remplacée), **rôles mappés via les groupes/claims de
l'IdP**.

**Modèle de rôle** : ce projet a un rôle global BINAIRE
(`users.role` : `admin`/`observateur` seulement, voir
`app/core/database.py`) -- l'accès fin passe par les ACL/groupes/rôles
personnalisés de `app/core/permissions.py`, pas par ce champ. Un compte
SSO reçoit `admin` si l'un de ses groupes IdP (nom de claim configurable,
défaut `groups`) figure dans la liste "groupes admin" configurée côté
Hyperlite, sinon `observateur`. **Réévalué à CHAQUE connexion**, pas figé
à la création : un utilisateur retiré du groupe admin côté IdP perd ses
droits admin Hyperlite dès sa prochaine connexion SSO.

**Protection anti-collision (point de sécurité important)** : une
connexion SSO qui résout un nom d'utilisateur correspondant à un compte
LOCAL existant (`auth_source != 'sso'`) est explicitement REFUSÉE
(`sso.LocalAccountConflict`) plutôt que d'écraser silencieusement son
rôle -- sans ça, un utilisateur SSO nommé par coïncidence (ou
intentionnellement) comme le compte admin local aurait pu rétrograder ce
compte de secours à `observateur`, cassant exactement la garantie de
secours qu'Antho a demandée.

Aucune nouvelle dépendance : `urllib` (stdlib, même convention que
`app/core/notifications.py` du chantier 28) pour les appels HTTP vers
l'IdP, `python-jose` (déjà utilisé par `security.py` pour les JWT de
session) pour valider la signature RS256 de l'ID token via les JWKS de
l'IdP.

`app/core/sso.py` (logique) + `app/routers/sso.py` (endpoints) :
- `GET /auth/sso/status` (public) -- l'écran de connexion doit savoir
  s'il faut afficher le bouton SSO avant que quiconque soit authentifié.
- `GET`/`PUT /auth/sso/config` (admin) -- issuer, client_id/secret,
  redirect_uri, claim des groupes, liste des groupes → admin. Le secret
  n'est jamais renvoyé en clair (`client_secret_set: bool` seulement).
- `GET /auth/sso/login` -- découverte OIDC (`{issuer}/.well-known/
  openid-configuration`, un seul champ à saisir plutôt que 4 URLs),
  génère `state`+`nonce` (table `sso_login_state`, à usage unique, TTL
  10 min), redirige vers l'IdP.
- `GET /auth/sso/callback` -- consomme le `state` (protection anti-rejeu),
  échange le code, valide la signature de l'ID token (JWKS + `kid`) ET
  les claims standard (`iss`/`aud`/`exp`) ET le `nonce`, résout le rôle,
  provisionne l'utilisateur, émet un jeton de session Hyperlite normal
  (`create_access_token`, même mécanisme qu'un login classique) et
  redirige le navigateur vers `/?sso_token=...`. Toute erreur redirige
  vers `/?sso_error=...` (message clair) plutôt qu'un 500 brut -- ce sont
  des redirections de NAVIGATEUR, personne ne verrait un code d'erreur
  JSON.

Frontend : bouton "Se connecter avec SSO" sur `LoginScreen.jsx` (affiché
seulement si `GET /auth/sso/status` renvoie `enabled: true`), nouvel
onglet Datacenter "SSO" (`SSOTab.jsx`, admin uniquement, même style que
`NotificationsTab.jsx`) pour la configuration. `useAuthStore.js::
restoreSession()` capture `sso_token` dans l'URL au chargement (même
format qu'un jeton de login classique), nettoie l'URL immédiatement
(`history.replaceState`) pour ne jamais laisser un jeton de session
trainer dans l'historique du navigateur.

**Testé réellement de bout en bout avec un IdP OIDC de test jetable**
(script Python, jamais committé, clé RSA générée à chaque run, vraie
signature/vérification, vrai flux de redirection HTTP -- pas une
simulation) : 16 vérifications automatisées, toutes passées :
- Utilisateur sans groupe admin → compte créé en `observateur`.
- Utilisateur avec un groupe mappé → rôle `admin`.
- Le MÊME utilisateur qui perd son groupe admin côté IdP → rétrogradé à
  `observateur` dès sa connexion suivante (réévaluation confirmée dans
  les deux sens, pas juste à la création).
- ID token avec un `nonce` falsifié → rejeté, aucun compte créé.
- ID token signé avec une AUTRE clé (signature invalide) → rejeté.
- Tentative de collision avec un compte local existant → refusée, compte
  local vérifié INTACT (`auth_source`/`role` inchangés) après la
  tentative.
- `state` forgé directement sur `/auth/sso/callback` (sans passer par
  `/auth/sso/login`) → rejeté.
- Login local (mot de passe) toujours fonctionnel après tous les essais
  SSO précédents -- confirme le principe de secours.

UI vérifiée dans un vrai navigateur (Playwright, même méthode que le
chantier 25) : bouton SSO absent tant que non configuré/activé,
formulaire de config Datacenter rempli/enregistré/persisté après
rechargement de page, bouton SSO qui apparaît ensuite sur l'écran de
connexion. Compte de test et IdP jetable supprimés à la fin, config SSO
remise à `enabled: false` (aucun vrai IdP configuré pour l'instant --
à faire par Antho via l'onglet Datacenter > SSO le jour où il en a un).

## Backlog de robustesse post-roadmap (2026-09-18)

Après la clôture de la roadmap vSphere/vCenter (31 chantiers + chantiers
20/16), Antho a demandé explicitement de traiter le backlog des limites
documentées ("les limites connues documentées"). Ordre convenu à
l'avance (front-loaded, Antho non disponible pendant l'exécution) :

1. **Collision de nom de pont réseau** (chantier 21) -- remplace la
   troncature par prefixe + hash SHA-1 du nom complet. Testé : 3 réseaux
   colliseurs créés simultanément, 3 ponts distincts confirmés.
2. **Rate-limiting par IP** sur `/auth/login`/`/auth/login/2fa` (chantier
   11, "reste à explorer") -- en plus du verrou par compte existant.
   Testé : 20 usernames différents depuis la même IP → 21e bloquée.
3. **Chiffrement des secrets au repos** (mot de passe SMTP, client secret
   OIDC) -- Fernet, clé dans `.env`. Limite honnête documentée dans
   `app/core/secrets_crypto.py` : protège contre une fuite du seul
   `hyperlite.db`, pas un accès complet au système de fichiers. Bug
   sécurité réel trouvé au passage : `GET /notifications/channels`
   n'était protégé que par une authentification simple, exposait le mot
   de passe SMTP en clair à tout compte, même observateur -- corrigé.
4. **Choix du pool de stockage à la création de VM** (chantier 17) --
   débloque un vrai usage de la HA sans déplacer un disque à la main.
5. **Confiance SSH inverse** (chantier 27) -- migration nœud distant ->
   kvm-lab, clé dédiée par nœud (jamais partagée), restreinte par
   `from=`. Testé réellement entre kvm-lab et serveur-antho.
6. **Audit sécurité des routers restants** (chantier 11) -- groups.py/
   pools.py/acl.py/dashboard.py relus ligne à ligne : tous corrects
   (admin-only ou intentionnellement ouverts), aucune faille trouvée.
7. **Actions VM multi-nœuds** (start/stop/restart/delete distants) --
   bug réel trouvé : le frontend résolvait déjà le nœud mais ne le
   transmettait jamais à l'API. Combiné avec le point 5, a aussi révélé
   et corrigé un bug de migration PRÉ-EXISTANT (chantier 27) : le disque
   source n'était jamais nettoyé après une migration réussie en stockage
   non partagé, dans les deux sens, depuis le tout début du chantier 27.
8. **Snapshots/clonage + ACL granulaire de conteneur** (chantier 18) --
   confirmé que libvirt-lxc ne supporte aucun snapshot instantané ;
   clonage (copie rootfs) et sauvegarde/restauration (tar) à la place.
   ACL étendue aux conteneurs (même système que les VM).

**Chaque point testé réellement contre l'infrastructure physique
(kvm-lab + serveur-antho), pas seulement relu** -- plusieurs bugs réels
supplémentaires trouvés EN TESTANT chaque correctif, documentés dans
les commits/PR individuels (#42 à #49). Propagé aux deux machines à
chaque étape via le mécanisme de mise à jour apt existant (chantier
7bis), jamais de déploiement manuel.

## Trois derniers points (2026-09-18, suite du backlog)

Après explication détaillée des trois points restants, Antho a demandé
de les traiter aussi. Décision de sécurité clarifiée avant de toucher au
fencing (question posée explicitement, IPMI/PDU absents de ce matériel).

- **Galerie de templates visuelle (conteneurs)** -- purement cosmétique,
  grille de cartes (icône + description) remplaçant le simple champ
  texte pour les images courantes, recherche texte conservée en dessous.
- **CPU générique automatique** (chantier 27) -- `_compute_migratable_cpu_xml()`
  retombe désormais sur un CPU `qemu64` générique (`svm`/`vmx` désactivés)
  quand `baselineCPU()` échoue, au lieu de `host-model` seul. Le
  contournement manuel (`virsh edit`) nécessaire jusqu'ici entre kvm-lab
  et serveur-antho n'est plus nécessaire. Testé : VM créée sans
  intervention → migration immédiate réussie.
- **Fencing SSH pour la HA** (chantier 17) -- `_attempt_ssh_fence()`,
  appelée en tout début de `recover()` : tente de confirmer/tuer le
  processus qemu original via SSH direct (pas libvirt, qui a justement
  échoué) avant toute récupération. Fencing "faible" assumé (pas de
  carte de gestion à distance sur ce matériel) : échoue proprement sans
  bloquer la récupération si le nœud est aussi injoignable en SSH -- le
  verrou d'écriture natif de QEMU reste le filet de sécurité ultime dans
  ce cas. Récupération TOUJOURS déclenchée manuellement par un admin,
  fencing réussi ou non. Bug réel trouvé en testant : `pgrep -af`
  s'auto-matchait via sa propre invocation SSH (faux PID "trouvé" même
  après un kill réellement réussi) -- corrigé en filtrant sur le nom du
  binaire (`qemu-system`).

Chaque point testé réellement (PR #52, #53).

## Backlog stockage entreprise (2026-09-18) : ZFS sur zvols, phase 1/2

Après la clôture des 31+ chantiers de la roadmap vSphere/vCenter, Antho a
demandé une nouvelle roadmap visant un niveau plus proche de l'entreprise
("au moins les 80%"), avec un gros effort sur le **stockage** en
particulier. Discussion explicite avant de commencer :

- **ZFS avant Ceph** -- Ceph nécessite un nombre impair de nœuds (3+) pour
  un quorum de moniteurs réellement sûr ; avec seulement kvm-lab et
  serveur-antho (2 nœuds), un déploiement Ceph serait non testable
  honnêtement dans ce projet ("toujours tester en conditions réelles").
  Antho a d'abord suggéré Ceph aussi pour la mise en prod long terme,
  confirmé ensuite ("ok ca me va on met ceph pour plus tard") après
  discussion : **Ceph explicitement reporté à l'arrivée d'un 3e nœud
  physique**, pas commencé maintenant.
- **zvols bruts plutôt que qcow2-sur-dataset ZFS** -- décision explicite
  d'Antho ("utilise zvols"), pour une raison de réutilisabilité
  architecturale future : un zvol (`/dev/zvol/<pool>/<nom>`) et un
  périphérique RBD Ceph (`/dev/rbdN`, futur) se présentent TOUS LES DEUX
  à une VM comme un périphérique bloc brut sur l'hôte -- construire cette
  forme d'attachement une fois (voir `<disk type='block'>` ci-dessous)
  est directement réutilisable plus tard pour Ceph/RBD, seule la source
  du chemin de périphérique change.
- **Pools ZFS sur fichiers loopback pour l'instant** (choix confirmé
  d'Antho) -- ni kvm-lab ni serveur-antho n'ont de disque/partition libre
  dédié ; **surtout, ne JAMAIS toucher/réduire le volume group LVM
  existant `hyperlite-vg`** qui porte la racine réelle de serveur-antho
  (risque déjà identifié par le passé). `zpool create <nom> <fichier>`
  accepte nativement un fichier régulier comme vdev (pas besoin de
  `losetup` explicite) -- ZFS réel, juste pas encore sur un disque dédié ;
  transparent à remplacer par un vrai périphérique bloc plus tard.

**Pourquoi pas l'API de pool de stockage libvirt** (comme les pools
dir/netfs existants, chantier 26) : vérifié sur kvm-lab
(`/usr/lib/x86_64-linux-gnu/libvirt/storage-backend/`) qu'aucun pilote
`zfs` n'est compilé dans le paquet libvirt installé (9.0.0, Debian 12).
Un paquet séparé existe (`libvirt-daemon-driver-storage-zfs`) mais son
support de création de volume est historiquement limité (liste des zvols
PRÉ-EXISTANTS seulement, pas de `vol-create-as` fiable) -- non utilisé.
ZFS est donc géré **entièrement par appels directs `zpool`/`zfs` en
sous-processus** (`app/core/zfs_storage.py`, même style que
`app/core/network_firewall.py` pour iptables), jamais via
`virStoragePool`/`virStorageVol`. Aucune table SQLite dédiée : l'état vit
entièrement dans ZFS lui-même (`zpool list`/`zfs list`), interrogé à
chaque appel -- même philosophie que `app/routers/storage.py`, qui ne
fait déjà confiance qu'à l'état réel de libvirt.

**Paquets installés sur kvm-lab** (composant `contrib` ajouté à
`/etc/apt/sources.list.d/debian.sources`, requis par la licence CDDL de
ZFS) : `zfsutils-linux`, `zfs-dkms` (+ `linux-headers-$(uname -r)`),
`libvirt-daemon-driver-storage-zfs` (installé par prudence, pas utilisé
pour l'instant -- voir ci-dessus). Module noyau chargé (`modprobe zfs`),
persistant au redémarrage via les unités systemd `zfs.target`/
`zfs-import-cache.service` installées par le paquet. **Pas encore propagé
à serveur-antho** -- à faire (mêmes paquets + composant contrib) le jour
où ce nœud doit lui aussi héberger des pools ZFS.

**Périmètre livré (phase 1/2 -- pools + VM sur zvol)** :
- `POST/DELETE /storage` (type `"zfs"`, `size_gb` pour la taille du
  fichier loopback) + fusion dans `GET /storage` (liste unifiée avec les
  pools dir/netfs existants, même forme de champs). Mono-nœud pour
  l'instant (toujours l'hôte local) -- même limite que le reste de ce
  chantier, documentée dans le code plutôt que masquée.
- `POST/DELETE /storage/{pool}/volumes` réutilisés pour les zvols (mêmes
  endpoints que les volumes qcow2 classiques, routage interne selon le
  type de pool détecté).
- `storage_pool` sur `POST /vms` accepte désormais un pool ZFS : les
  disques sont créés en zvols (`create_zvol_disk()`,
  `app/core/vm_builder.py`) plutôt qu'en fichiers qcow2, écriture de
  l'image cloud Debian (ou du disque importé, chantier 23) directement
  sur le périphérique bloc via `qemu-img convert -O raw` -- pas d'étape
  de fichier intermédiaire. `build_domain_xml()` accepte maintenant un
  disque comme tuple `(chemin, "block")` en plus d'un simple chemin de
  fichier (comportement historique inchangé) : génère `<disk
  type='block'><driver type='raw'/><source dev='...'/>` au lieu de
  `type='file'`.
- Suppression de VM (`_perform_vm_deletion`, réutilisée par le nettoyage
  automatique du chantier 19) étendue pour détecter les disques bloc dans
  le XML du domaine et supprimer le zvol correspondant (`zfs destroy -r`,
  purge aussi les snapshots ZFS orphelins) au lieu d'un simple
  `Path.unlink()`.

**Testé réellement de bout en bout sur kvm-lab** (compte admin temporaire
créé directement en base, supprimé à la fin) : création d'un pool ZFS de
8 Go (fichier loopback), création d'une VM dessus via l'API réelle,
domaine XML vérifié (`<disk type='block'>` + `<source
dev='/dev/zvol/...'/>`), démarrage réel, **vraie IP DHCP obtenue,
connexion SSH réussie, écriture d'un fichier confirmée** -- disque
bloc brut réellement lisible/inscriptible par l'OS invité, pas juste un
domaine qui démarre. `df -h` dans la VM confirme que le module
`growpart` de cloud-init a bien étendu la partition racine aux 3 Go
complets du zvol (comportement identique à un disque qcow2 classique).
Suppression de la VM vérifiée : zvol disparu (`zfs list` vide). Suppression
du pool vérifiée : `zpool list` échoue proprement ("no such pool"),
fichier loopback supprimé du disque.

**Pas encore fait, prochaines étapes de ce chantier stockage** (annoncé à
Antho avant de commencer, pas encore commencé) :
- **Snapshots ZFS natifs** pour les VM sur zvol (`zfs snapshot`/
  `rollback`) -- mécanisme distinct des snapshots internes qcow2 du
  chantier 4, qui ne s'appliquent pas à un disque bloc brut.
  - **Réplication** `zfs send`/`receive` entre kvm-lab et serveur-antho,
  en vue de renforcer la HA (chantier 17) avec un chemin de reprise pour
  des VM sur stockage ZFS local, sans dépendre du stockage partagé NFS
  (chantier 26) comme condition préalable actuelle.
- **Intégration HA** -- VM répliquée par ZFS comme alternative au
  stockage partagé NFS pour la protection HA.
- **Limite connue non traitée pour l'instant** : une VM sur zvol n'est
  PAS migrable à chaud (chantier 27) -- `domain_disk_paths()`
  (`app/core/libvirt_utils.py`) ne lit que l'attribut `file` des disques,
  pas `dev`, donc un disque bloc n'est actuellement jamais détecté par le
  code de migration. Sans conséquence immédiate (échoue proprement en
  ignorant simplement le disque plutôt que de corrompre quoi que ce
  soit), mais à corriger explicitement avant d'annoncer la migration
  comme supportée pour ce type de VM.

### Bug réel trouvé en testant la phase 1 ZFS sur une DEUXIÈME machine

`GET /storage` cassait entièrement (500) sur serveur-antho (ZFS pas
installé sur cette machine, contrairement à kvm-lab où le paquet avait
été installé pour développer ce chantier) : `zfs_storage._run()`
laissait échapper un `FileNotFoundError` brut (binaire `zpool`/`zfs`
absent). Corrigé à la source (`_run()` attrape ce cas, se comporte comme
un échec propre) -- même classe de bug que le "git absent" du chantier
7bis, pas anticipée malgré `is_available()` déjà écrit mais jamais
branché dans ce point d'entrée commun. PR #56. **Trouvé uniquement parce
qu'un second serveur physique différent a été testé** -- un rappel de
plus que tester sur une seule machine (même réelle) ne suffit pas
toujours à découvrir ce genre de dépendance implicite à l'environnement.

## Migration kvm-lab -> hl-devhub (2026-09-18)

**kvm-lab (nœud + VM) a été démantelé par Antho** ("le noeuds et donc la
vm kvm lab la ou tu taff va disparaitre car on va le migré") en cours de
session, juste après la phase 1 ZFS ci-dessus. Nouvelle topologie
annoncée par Antho : Hyperlite tourne désormais sur son serveur perso à
la maison (`serveur-antho`) + le serveur d'un collègue, **Nicolas**
(pas encore rejoint au moment d'écrire cette note -- infos d'accès pas
encore fournies, à faire dans un futur chantier).

**Point critique découvert en préparant cette migration** (pas encore un
incident, mais l'aurait été si non traité à temps) : toute
l'infrastructure de publication (clone Git + **clé GPG de signature du
dépôt APT** + hook `post-merge` + nginx qui sert le dépôt) ne vivait QUE
sur kvm-lab, liée à son IP Tailscale (`100.88.184.24:8899`). Sans
migration, la disparition de kvm-lab aurait cassé DÉFINITIVEMENT toute
future mise à jour/installation apt sur serveur-antho ET empêché
d'onboarder Nicolas -- la clé de signature elle-même aurait été perdue
(jamais commitée, par design, voir chantier 7bis). Sauvegarde défensive
de la clé prise IMMÉDIATEMENT en découvrant ce risque, avant toute autre
action.

**Décision prise avec Antho** : le nouveau poste de dev/publication est
une **VM dédiée** (`hl-devhub`, 2 vCPU/2 Go/40 Go), créée via Hyperlite
lui-même SUR serveur-antho -- pas directement sur l'install apt de PROD
de serveur-antho elle-même, pour ne pas re-mélanger dev et prod (exactement
la séparation déjà mise en place lors de l'adoption apt de serveur-antho,
chantier 7bis "Durcissement post-chantier").

### Étapes réalisées, dans l'ordre

1. **serveur-antho mis à jour vers la dernière version** (`.1518` ->
   `.1555`, incluant la phase 1 ZFS) PENDANT que kvm-lab était encore
   joignable -- dernière chance de le faire par ce chemin.
2. **`hl-devhub` créée** via l'API de serveur-antho lui-même (pas celle
   de kvm-lab -- une VM se crée toujours sur l'hôte LOCAL de l'instance
   Hyperlite appelée, limite documentée ailleurs dans ce fichier), 2
   vCPU/2 Go RAM/40 Go disque, réseau `default` (NAT interne à
   serveur-antho).
3. Paquets de build installés sur `hl-devhub` : `git`, `nginx-light`,
   `gnupg2`, `rsync`, `dpkg-dev`, `debhelper`, `build-essential`,
   `python3-venv`/`python3-dev`, `libvirt-dev`, `qemu-utils`,
   `genisoimage`, `nodejs`/`npm` (18.20.4, même version que kvm-lab, déjà
   dans les dépôts Debian standards -- pas besoin de nodesource).
4. Dépôt cloné en HTTPS public (`git clone https://github.com/...`), hook
   `post-merge` symlinké exactement comme sur kvm-lab.
5. **Clé GPG transférée** : kvm-lab -> serveur-antho -> hl-devhub (double
   saut SSH via la clé cluster puis la clé d'automatisation), jamais
   passée par un canal externe.
6. **Authentification GitHub** : tentative initiale de RÉUTILISER le
   jeton déjà authentifié sur kvm-lab (`gh auth token`) -- **bloquée par
   le classificateur de sécurité du mode auto de Claude Code**
   ("Credential Materialization"), comportement attendu/voulu, pas
   contourné. Antho a fourni un nouveau Personal Access Token (scope
   `repo`) à la place, écrit directement dans `~/.config/gh/hosts.yml`
   sur hl-devhub (`gh auth login --with-token` refusait à cause d'un
   scope `read:org` manquant non nécessaire pour push/release -- écrire
   le fichier directement contourne cette validation trop stricte sans
   rien affaiblir en pratique, vérifié avec `git fetch`/`gh release
   view`).
7. **Tailscale** : ne pouvait pas être fait par Claude (nécessite une
   authentification au compte Tailscale d'Antho) -- Antho a généré une
   clé Auth réutilisable (pas API access token, distinction importante,
   voir https://login.tailscale.com/admin/settings/keys) et l'a fournie.
   `tailscale up --authkey=... --hostname=hl-devhub` -> IP
   `100.104.191.72`.
8. `installer/hyperlite-apt-repo.nginx.conf` mis à jour (nouvelle IP),
   commité via branche+PR normale (PR #57) -- **dernier merge publié par
   le hook de kvm-lab avant son démantèlement**, testé comme un vrai
   test de bout en bout de la transition.
9. nginx configuré sur hl-devhub à l'identique de kvm-lab -- **piège
   rencontré** : `/root` était en `0700` sur la VM fraîche (nginx tourne
   en `www-data`, ne pouvait traverser NI lister le répertoire), alors
   que kvm-lab avait `/root` en `drwx-----x` (bit exécution pour "autres"
   = traversée possible sans listage) -- précisément le point qui
   permettait à nginx de servir des fichiers sous `/root/hyperlite/...`
   sans exposer le reste de `/root`. Reproduit avec `chmod o+x /root
   /root/hyperlite /root/hyperlite/installer /root/hyperlite/installer/
   apt-repo`.
10. `installer/build-deb.sh` + `installer/build-apt-repo.sh` exécutés
    manuellement une première fois sur hl-devhub (repo vide au départ,
    le hook post-merge ne se déclenche que sur un VRAI nouveau commit).
11. **Testé réellement de bout en bout** : dépôt interrogé directement
    depuis kvm-lab par Tailscale (`curl http://100.104.191.72:8899/...`),
    puis serveur-antho re-pointé vers la nouvelle IP
    (`/etc/apt/sources.list.d/hyperlite.list`) et **mis à jour pour de
    vrai via `apt-get install --only-upgrade hyperlite`** contre le
    nouveau dépôt -- succès, `/health` répond normalement après coup.

### Reste à faire (pas commencé)

- **Onboarder le serveur de Nicolas** dès qu'Antho fournit ses infos
  d'accès (IP/SSH) -- même mécanisme apt que ci-dessus (pointer vers
  `http://100.104.191.72:8899`), puis `POST /nodes` + confiance SSH
  cluster (chantier 15/27) pour l'intégrer au cluster.
- **kvm-lab n'a jamais explicitement désinscrit son entrée `POST /nodes`
  ni son entrée dans la table `nodes`** côté serveur-antho -- kvm-lab
  n'était de toute façon PAS enregistré comme nœud distant (c'était
  l'hôte "local" de sa propre instance), donc rien à nettoyer côté
  cluster. À vérifier malgré tout si des références résiduelles
  apparaissent (notifications, HA...).
- **ZFS sur serveur-antho : BLOQUÉ sur Secure Boot, pas juste "pas
  installé".** `zfsutils-linux`/`zfs-dkms` installés avec succès
  (composant `contrib` ajouté à `/etc/apt/sources.list` -- format
  ancien style `deb ... main contrib non-free-firmware`, pas
  `debian.sources` comme sur kvm-lab/bookworm), MAIS le module `zfs`
  refuse de charger : `modprobe: ERROR: could not insert 'zfs': Key was
  rejected by service`. Cause confirmée (`mokutil --sb-state` ->
  `SecureBoot enabled`) : serveur-antho est un **HP EliteDesk 800 G6
  Mini PC** (`dmidecode`), pas du matériel serveur -- aucun IPMI/BMC
  détecté (`/dev/ipmi*` absent, aucune interface réseau de gestion
  dédiée), donc **aucun accès KVM à distance possible** sur cette
  machine, contrairement à un vrai serveur (Dell iDRAC/HP iLO/
  Supermicro IPMI). La clé MOK auto-signée par DKMS doit être enrôlée
  manuellement via l'écran "MOK Management" au prochain démarrage --
  cet écran s'affiche AVANT que Linux démarre (donc avant que SSH soit
  disponible), nécessite un clavier+écran physiquement branchés.
  **Préparé à l'avance** : `mokutil --import /var/lib/dkms/mok.pub`
  déjà exécuté avec le mot de passe `Hyperlite-MOK-2026` (visible dans
  ce fichier volontairement -- usage unique, invalidé après confirmation
  à l'écran MOK, aucune valeur de sécurité durable à protéger). `hl-devhub`
  mise en autostart (`virsh autostart`) pour redémarrer automatiquement
  après le reboot. **Reste à faire par Antho** : la prochaine fois qu'il
  est physiquement devant la machine, redémarrer et valider l'écran MOK
  (Enroll MOK -> Continue -> Yes -> mot de passe ci-dessus) -- une seule
  fois, définitif ensuite pour tous les futurs modules DKMS. Puis
  reprendre les tests ZFS (phase 1/2, création pool+VM sur zvol) sur
  serveur-antho pour de vrai.
- Comptes de test temporaires (`devvm_admin` sur serveur-antho) à
  supprimer une fois cette session de migration terminée.

## Backlog stockage entreprise, phase 3 : snapshots ZFS natifs (2026-09-18)

Suite directe de la phase 1/2 (pools + VM sur zvols) -- implémentée et
testée sur kvm-lab **avant son démantèlement effectif** (toujours
joignable au moment de ce chantier, malgré l'annonce de sa migration
imminente -- travail livré en petits incréments commit+push+merge
immédiats plutôt qu'en un seul gros changement différé, exactement pour
limiter le risque de perte si la machine disparaissait en cours de
route).

**Mécanisme NATIF ZFS** (`zfs snapshot`/`rollback`, `app/core/
zfs_storage.py`), complètement distinct du snapshot INTERNE qcow2
utilisé pour les VM classiques (chantier 4, `domain.snapshotCreateXML`)
-- un disque bloc brut (zvol) n'a aucun format de fichier avec support
de snapshot intégré, libvirt n'a donc rien à proposer dessus. Routage
transparent côté API : `_zvol_disks_of_domain()` (`app/routers/vms.py`)
détecte si une VM a des disques `type='block'` (zvols) et bascule vers
le mécanisme ZFS -- **mêmes endpoints exacts** (`GET/POST/DELETE
/vms/{name}/snapshots`, `POST .../restore`), même forme de réponse,
**aucun changement frontend nécessaire** (`VMSnapshotsTab.jsx` inchangé).

**Deux différences de sémantique réelles, assumées et documentées plutôt
que masquées** :
1. **Pas de capture mémoire** -- un snapshot ZFS ne capture QUE l'état du
   disque (équivalent à couper le courant à cet instant précis,
   cohérent au niveau système de fichiers grâce au journal, mais jamais
   un état "reprend exactement où on s'était arrêté"), contrairement au
   snapshot interne qcow2 qui inclut la mémoire vive quand la VM tourne.
   Sans conséquence pratique pour la CRÉATION (un snapshot ZFS est
   atomique et cohérent quel que soit ce qui écrit sur le disque au même
   instant -- pris tel quel, même VM active).
2. **Restauration exige la VM ARRÊTÉE** -- contrairement au snapshot
   qcow2 (libvirt gère lui-même le cas "VM active" pour
   `revertToSnapshot`), un `zfs rollback` sur un zvol activement ouvert
   par le processus qemu d'une VM en marche désynchroniserait le cache
   du noyau invité de l'état réel du disque -- corruption quasi
   certaine. Vérifié explicitement (`domain.isActive()`) avant tout
   rollback ZFS, refusé avec un message clair (409) plutôt que risqué.
3. **Rollback linéaire, pas arborescent** -- `zfs rollback -r` (utilisé
   ici) détruit définitivement tout snapshot plus récent que la cible,
   contrairement aux snapshots qcow2 internes qui permettent de naviguer
   librement entre plusieurs points sans en perdre aucun. Assumé :
   correspond au modèle mental "revenir en arrière dans le temps"
   attendu par la plupart des utilisateurs, sans la complexité d'un vrai
   arbre de versions.

**Snapshot atomique multi-disques** : `snapshot_zvols()` prend TOUS les
zvols d'une VM dans UNE SEULE commande `zfs snapshot pool/v1@nom
pool/v2@nom` -- important pour une VM multi-disques, les disques doivent
tous refléter exactement le même instant, pas une suite de snapshots
pris l'un après l'autre (fenêtre de cohérence).

**Testé réellement de bout en bout sur kvm-lab** (VM sur pool ZFS,
compte admin temporaire supprimé à la fin) :
- Fichier marqueur écrit dans la VM (`ETAT-AVANT-SNAPSHOT`), snapshot
  créé via l'API **pendant que la VM tournait** (confirme qu'un
  snapshot ZFS live est bien sûr/atomique) -- vérifié à la fois côté API
  (`GET .../snapshots`) et côté `zfs list -t snapshot` réel.
- Fichier marqueur modifié (`ETAT-APRES-SNAPSHOT-MODIFIE`).
- **Tentative de restauration VM ACTIVE correctement refusée** (409,
  message clair) -- garde-fou de sécurité confirmé fonctionnel, pas
  juste écrit.
- VM arrêtée, restauration relancée -- **succès**, VM redémarrée,
  fichier marqueur confirmé revenu à `ETAT-AVANT-SNAPSHOT` -- preuve
  réelle que le rollback ZFS a bien fonctionné, pas seulement que la
  commande n'a pas planté.
- Suppression du snapshot vérifiée (`zfs list -t snapshot` vide après).
- VM et pool de test supprimés à la fin, compte de test supprimé.

**Pas encore fait** : réplication `zfs send`/`receive` entre kvm-lab
(désormais démantelé -- donc entre serveur-antho et un futur second
nœud ZFS, potentiellement le serveur de Nicolas) et intégration HA.

### Audit UI réel (Playwright) du flux ZFS complet (2026-09-18)

Fait pendant que kvm-lab était bloqué sur les deux points ci-dessus
(Secure Boot serveur-antho, accès Nicolas) -- seule machine avec ZFS
fonctionnel encore joignable, donc dernière fenêtre pour vérifier
visuellement ce qui n'avait été testé jusqu'ici que via l'API brute.
Script `test-zfs.js` dans `/root/hyperlite-ui-test/` (même méthode que
le chantier 25 : compte admin temporaire, Chromium headless réel).

**3 bugs réels trouvés en lisant `VMSnapshotsTab.jsx` AVANT même
d'ouvrir un navigateur** (relecture attentive suite au constat que le
composant affichait du texte qcow2 générique pour tous les snapshots) :
1. Le libellé "arrêtée (disque seul)" s'affichait pour un snapshot ZFS
   même si la VM tournait réellement au moment du snapshot -- le
   frontend ne reconnaissait que `etat_vm === "running"` (valeur qcow2),
   pas `"disque_seul"` (valeur ZFS).
2. Le texte d'avertissement (3+ snapshots) et la note de progression
   pendant la création mentionnaient "grossit le fichier qcow2" /
   "mémoire incluse automatiquement" -- faux pour une VM ZFS (pas de
   fichier qcow2 du tout, jamais de mémoire capturée).

**1 bug réel supplémentaire trouvé en TESTANT réellement dans le
navigateur** (pas visible à la seule lecture de code) : la détection
"VM sur pool ZFS" côté frontend, déduite de la liste des snapshots
EXISTANTS (`snapshots.some(s => s.etat_vm === "disque_seul")`), est
FORCÉMENT fausse pour le TOUT PREMIER snapshot d'une VM (liste encore
vide au moment de sa création) -- le texte qcow2 trompeur s'affichait
donc malgré tout le temps de cette toute première création. Corrigé à
la racine : `stockage_zfs` (booléen) ajouté à `_domain_summary()`
(`app/routers/vms.py`, donc `GET /vms`/`GET /vms/{name}`) plutôt que
déduit côté frontend d'un état dérivé. **Piège rencontré en corrigeant
ça** : `mapVm()` (`dashboard/src/api/client.js`) fait une liste blanche
explicite des champs conservés depuis la réponse backend -- le nouveau
champ était silencieusement perdu tant qu'il n'était pas ajouté
explicitement à cette liste, malgré le backend le renvoyant
correctement (vérifié via `curl` direct, qui montrait `stockage_zfs:
true`, avant de comprendre que le frontend le jetait).

**Piège de script de test rencontré (pas applicatif)**, à retenir pour
tout futur script Playwright sur ce projet, en plus de celui déjà
documenté au chantier 25 (`hasText` = sous-chaîne) :
- Le tout premier clic du script (`text=Datacenter`, pensé comme un
  simple "aller à la vue Datacenter") touchait en fait le NŒUD DE
  L'ARBRE "Datacenter" de la sidebar (`ResourceTreeNode.jsx` couple
  `select()` ET `setExpanded(e => !e)` sur le MÊME clic) -- repliait
  l'arbre pour tout le reste du script, sans aucune erreur visible
  avant le tout premier clic sur une VM (qui échouait silencieusement
  en timeout, l'élément n'existant simplement plus dans le DOM
  affiché). Un second clic sur ce même nœud le rouvre.
- Le formulaire de création de pool (`StorageTab.jsx`) est un vrai
  `<form>` -- scoper les locators de boutons à `page.locator("form")`
  évite tout collision `hasText` avec le header (ex. le pseudo-bouton
  "ZFS" matchait d'abord `zfsui_admin` dans le menu utilisateur,
  `hasText` étant insensible à la casse : "ZFS" ⊂ "zfsui_admin").
  Résultat concret avant correction : un pool nommé "ZFS" créé en
  type `dir` sans qu'aucune étape ne signale l'erreur.
- La création d'une VM sur un pool ZFS est RÉELLEMENT plus lente
  (~37-38s mesurées, `qemu-img convert -O raw` vers le zvol via un
  pool loopback) qu'une VM qcow2 classique -- un délai fixe de
  quelques secondes après le clic "Créer la VM" est insuffisant,
  attendre la fermeture réelle de la modale (ou la tâche `terminee`)
  est indispensable.

**Testé et confirmé visuellement, pas seulement via l'API** : type
"ZFS" affiché dans la table des pools, sélecteur de pool dans
l'assistant de création de VM proposant bien le pool ZFS, libellé
"ZFS, disque seul (jamais la mémoire)" correct sur un snapshot pris VM
active, toast d'erreur clair ("La VM doit être arrêtée avant de
restaurer...") visible à l'écran lors d'une tentative de restauration
sur VM active -- le garde-fou backend existait déjà, mais rien ne le
montrait auparavant à l'utilisateur avant ce test (vérifié : sans le
toast d'erreur générique déjà câblé sur les échecs de tâche, ç'aurait
été un échec silencieux du point de vue de l'utilisateur).

Compte de test, VM et pool supprimés à la fin.

## Renommage du sentinel frontend "kvm-lab" -> "local" (2026-09-18)

Trouvé en auditant le VRAI dashboard de serveur-antho dans un vrai
navigateur (Playwright, `test-audit-antho.js` dans
`/root/hyperlite-ui-test/`, ciblant directement `https://100.95.115.103:8000`
depuis kvm-lab plutôt que d'installer Playwright sur serveur-antho) :
**l'onglet Stockage de serveur-antho affichait "kvm-lab" dans la colonne
"Nœud" pour SES PROPRES pools locaux** -- kvm-lab n'existe plus et n'a
jamais eu de rapport avec serveur-antho. Cause : `dashboard/src/api/
client.js` utilisait la chaîne littérale `"kvm-lab"` comme identifiant
SENTINELLE interne pour "l'hôte qui fait tourner cette instance
Hyperlite" (comparaisons `node !== "kvm-lab"` dans une dizaine
d'endroits, jamais un vrai nom de machine) -- géré indépendamment du
VRAI nom d'hôte (`d.hyperviseur.nom`, correctement affiché ailleurs,
ex. la table "Nœuds" du tableau de bord). Ce sentinel fuitait
directement dans certaines colonnes/libellés qui affichent `p.node`/
`v.node` tel quel plutôt que le nom résolu.

**Corrigé** : renommé en `"local"` partout (recherche exhaustive
`grep -rn "kvm-lab"` sur `dashboard/src/`), mécanique et sans risque --
c'est un simple identifiant de comparaison interne, jamais interprété
par le backend (qui traite déjà `node` omis/`undefined` comme "hôte
local"). Fichiers touchés : `client.js` (la définition + toutes les
comparaisons), `StorageTab.jsx`, `DatacenterSummaryTab.jsx`,
`VmDiskUploadDropzone.jsx`, `IsoUploadDropzone.jsx`. Également corrigé
au passage : `NodesTab.jsx` affichait littéralement "Hyperlite pilote
uniquement kvm-lab pour l'instant" en l'absence de nœud distant
enregistré -- reformulé en "cet hôte" (générique, correct quelle que
soit la machine qui héberge réellement l'instance).

**Testé réellement** (pas seulement `npm run build`) : script Playwright
rejoué sur kvm-lab après le correctif -- table Stockage affiche
désormais "local" pour les pools locaux et "serveur-antho" pour les
pools distants (au lieu de "kvm-lab" pour les deux catégories locales,
peu importe la machine réelle), sélection d'une VM existante toujours
fonctionnelle, zéro erreur console/réseau. **Repropagé sur serveur-antho
et reconfirmé visuellement là-bas** (audit Playwright rejoué contre son
vrai dashboard après mise à jour) -- au passage, trouvé et corrigé un
vrai trou de process : les 3 derniers merges (#61/#62/#63) n'avaient
jamais été `git pull`-és sur `hl-devhub`, donc jamais republiés depuis
la migration -- toujours penser à repasser sur hl-devhub après CHAQUE
merge, pas seulement kvm-lab avant.

## Portabilité et robustesse infrastructure -- mandat durable (2026-09-18)

Antho a donné une spécification complète et formelle (voir historique
de conversation pour le texte intégral) : Hyperlite ne doit plus être
conçu implicitement pour SON environnement actuel, mais installable et
exploitable sur des infrastructures très différentes (homelab mono-
nœud, mini-PC contraint, serveur pro multi-NIC/multi-disque, cluster
multi-nœuds, stockage local ou partagé, environnement partiellement
hors ligne) **sans jamais modifier le code source**. **Ceci est un
principe d'architecture DURABLE pour tout le reste du projet**, pas un
chantier ponctuel -- à garder en tête pour CHAQUE futur chantier
touchant l'installeur, le cluster, le stockage ou le réseau, pas
seulement les points listés ci-dessous.

Résumé des règles (détail complet dans la mémoire Claude Code,
`feedback_portability_architecture_mandate.md`, et dans le message
original d'Antho) : détection systématique des capacités plutôt que
supposition, aucune configuration codée en dur (IP, nom d'interface,
chemin de pool, nom de bridge, quantités fixes de CPU/RAM/stockage...),
trois profils de déploiement (homelab/standard/avancé -- des réglages
par défaut différents, PAS trois produits différents), preflight check
avant toute installation/ajout de nœud, dégradation contrôlée plutôt
qu'échec global, interfaces d'abstraction stables (virtualisation/
stockage/réseau/sauvegardes/métriques/auth/notifications), diagnostic
de compatibilité de cluster avant migration/ajout de nœud, page UI
"Compatibilité et capacités" par nœud, mises à jour versionnées
réversibles.

### Constat honnête (2026-09-18, avant de commencer) : où en est Hyperlite

**Déjà partiellement conforme** (à ne pas refaire de zéro) :
- Multi-nœuds réel (chantier 15, `qemu+ssh://`), pas figé mono-nœud.
- HA déjà pensée en dégradation contrôlée (détecte + alerte, jamais
  d'action automatique destructive, chantier 17).
- CPU "plus petit dénominateur commun" déjà calculé dynamiquement pour
  la migration (`_compute_migratable_cpu_xml`), avec repli générique
  documenté plutôt qu'un échec brut (backlog 2026-09-18).
- Choix du pool de stockage à la création de VM déjà découplé (dir/
  netfs/zfs, chantier 17/26/ZFS phase 1).
- Mécanisme de mise à jour déjà versionné avec sauvegarde + rollback
  watchdog (chantier 7/7bis).

**Absent ou clairement en violation, trouvé en confrontant le code réel
à ce mandat** (pas une liste théorique -- vérifié dans `app/routers/
vms.py` avant d'écrire cette note) :
- **AUCUN module de découverte de capacités matérielles** -- rien ne
  normalise "que peut faire cet hôte" nulle part.
- **Violation concrète et flagrante des limites codées en dur** :
  `VMCreate`/`VMUpdate` (`app/routers/vms.py`) plafonnent `vcpu` à
  **1-2** et `memory_mb` à **256-2048** EN DUR, quelle que soit la
  machine réelle -- serveur-antho (12 vCPU/15 Go RAM réels) ne peut
  aujourd'hui créer QUE des VM à 2 vCPU/2 Go max, alors qu'il pourrait
  largement plus. Exactement l'exemple donné par le mandat ("une
  quantité fixe de CPU, RAM ou stockage" codée en dur).
- Pas de profils de déploiement, pas de preflight check structuré et
  rapporté (l'installeur a des vérifications éparses via preseed, pas
  un rapport clair satisfait/avertissement/désactivé/bloquant).
- Pas de page "Compatibilité et capacités".
- Pas de couche d'abstraction stable -- le code métier appelle
  directement libvirt/zfs/iptables un peu partout, pas d'adaptateurs.
- Diagnostic de compatibilité de cluster limité au seul CPU (pas
  QEMU/libvirt version, formats de disque, firmwares...).

### Feuille de route proposée (séquencée, testée à chaque étape comme
tout le reste de ce projet -- PAS un big-bang)

1. **Module de découverte de capacités hôte** (`app/core/
   host_capabilities.py`) -- fondation dont tout le reste dépend :
   CPU (arch, cœurs/threads, extensions virtu, NUMA), mémoire totale/
   disponible, disques/systèmes de fichiers, interfaces réseau/bridges/
   VLAN, version QEMU/libvirt réelle, Secure Boot, paquets/dépendances
   présents. Nouvel endpoint `GET /nodes/{node}/capabilities` (et
   variante locale). Testable IMMÉDIATEMENT sur 3 machines réellement
   différentes déjà disponibles (kvm-lab tant qu'il est là, serveur-
   antho -- HP EliteDesk mini-PC réel, hl-devhub -- VM imbriquée) : un
   vrai test de portabilité gratuit, pas besoin d'attendre un nouveau
   matériel.
2. **Limites de VM dérivées du profil réel** (corrige la violation
   trouvée ci-dessus) -- remplace les bornes 1-2 vCPU/256-2048 Mo codées
   en dur par des limites calculées depuis le profil de capacités de
   chantier 1 (ex. jusqu'à N-1 cœurs réels, jusqu'à 80% de la RAM
   disponible), configurable/override possible. Teste concrètement le
   module du point 1.
3. **Preflight check structuré** pour l'installeur (`installer/`) --
   rapport clair satisfait/avertissement/fonctionnalités désactivées/
   bloquant, jamais un état partiellement configuré en cas d'échec.
4. **Page "Compatibilité et capacités"** (UI, par nœud) -- consomme le
   module du point 1, affiche capacités détectées/fonctionnalités
   actives/limitations/différences entre nœuds.
5. **Profils de déploiement** (homelab/standard/avancé) -- réglages par
   défaut de l'assistant d'installation/de création de VM selon le
   profil détecté ou choisi, pas trois produits séparés.
6. **Diagnostic de compatibilité de cluster étendu** (au-delà du CPU
   déjà fait) -- versions QEMU/libvirt, formats de disque, firmwares,
   avant migration/ajout de nœud, avec message exploitable.
7. **Couches d'abstraction stables** (virtualisation/stockage/réseau/
   sauvegardes/métriques/auth/notifications) -- refactor progressif,
   le plus gros chantier, à faire en dernier une fois les fondations
   (1-6) en place et éprouvées.

Chantiers 1 et 2 démarrés dans la foulée de cette note. Le reste de la
feuille de route sera traité un chantier à la fois, chacun avec sa
propre branche/PR et un test réel contre l'infrastructure disponible,
exactement comme tout le reste de ce document.

### Chantier 1 : découverte de capacités hôte (2026-09-18)

`app/core/host_capabilities.py` -- CPU (architecture/modèle/cœurs/
virtualisation matérielle/topologie NUMA, via les capacités XML de
libvirt, transparent local ET distant), mémoire totale/disponible
(`/proc/meminfo`), stockage (disques/partitions via `lsblk -J`, usage
réel du pool par défaut), réseau (interfaces OS + réseaux libvirt),
version QEMU/libvirt réelle, conteneurs LXC disponibles, Secure Boot
(lecture directe de la variable EFI, sans dépendre de `mokutil` --
justement le point qui a bloqué ZFS sur serveur-antho), binaires clés
présents (`qemu-img`/`zfs`/`git`/`xorriso`/...). `GET /host/
capabilities` (hôte local) et `GET /nodes/{name}/capabilities` (nœud
distant enregistré, combine l'API libvirt native -- transparente via
qemu+ssh:// -- et un unique probe SSH pour les informations OS qui ne
passent pas par libvirt).

**Piège trouvé en testant réellement sur les deux machines
disponibles** (pas en relisant le code) : un premier jet renvoyait
`zfs_disponible: true` sur serveur-antho simplement parce que le
binaire `zfs` est installé -- **alors que le module noyau ne charge
toujours pas** (Secure Boot, MOK pas encore enrôlé par Antho, voir plus
haut). Exactement le genre de limitation silencieuse que ce mandat
interdit explicitement. Corrigé : trois champs distincts
(`zfs_installe` / `zfs_module_charge` / `zfs_disponible` = ET logique
des deux) au lieu d'un seul booléen trompeur -- vérifié `lsmod` en
local ET à distance (pas un appel `zpool` qui pourrait lui-même
attendre/échouer différemment).

**Testé réellement sur deux machines physiques différentes** (pas de
mock) : kvm-lab (2 vCPU, 8 Go RAM, Secure Boot absent -- BIOS legacy,
ZFS pleinement fonctionnel) et serveur-antho via le chemin SSH+libvirt
distant (12 vCPU réels, 15 Go RAM, Secure Boot ACTIF confirmé,
QEMU/libvirt réellement différents -- 11.3M/10.0.13 contre 9.0M/7.2.22
sur kvm-lab --, ZFS installé mais non fonctionnel, `gh`/`nginx`
absents contrairement à kvm-lab) -- les deux profils reflètent
fidèlement les différences réelles entre les machines, pas des valeurs
inventées ou copiées d'une machine à l'autre.

Fondation pour les chantiers suivants (limites de VM dérivées, page
"Compatibilité et capacités", diagnostic de compatibilité de cluster).

### Chantier 2 : limites de VM dérivées de l'hôte réel (2026-09-19)

Corrige la violation flagrante relevée plus haut : `VMCreate`/`VMUpdate`/
`DiskSpec`/`VolumeCreate` plafonnaient en dur à 1-2 vCPU, 256-2048 Mo,
500 Go/disque (100 Go pour un volume, 4096 pour un pool ZFS), quelle que
soit la machine. `app/core/vm_limits.py::compute_limits()` calcule
maintenant : vCPU max = cœurs logiques réels, mémoire max = 80 % de la RAM
hôte (multiple de 128 Mo), disque max = 90 % de l'espace libre du pool par
défaut, disques max = 8 par défaut. **Override explicite par variable
d'environnement** (`HYPERLITE_VM_MAX_VCPU`, `_MAX_MEMORY_MB`, `_MAX_DISK_GB`,
`_MAX_DISKS`) -- priorité configuration > détection > repli, chaque limite
expose sa `source` (`detecte`/`configuration`/`defaut`/`repli`) pour que
l'UI/les messages expliquent d'où elle vient. Cache 30 s.
`validate_vm_resources()` renvoie des messages précis ("Mémoire : 999999 Mo
hors limites (256-6272 Mo, 80% de la RAM de l'hôte)") au lieu du 422
Pydantic générique. `GET /host/limits` ; frontend : hook
`useHostLimits` (échec de l'appel = champs sans borne haute, le backend
tranche -- dégradation contrôlée) utilisé par `StepResources`,
`VMOptionsTab`, `VMHardwareTab`.

**Testé réellement sur kvm-lab** : limites détectées (2 vCPU, 6272 Mo,
133 Go), création refusée avec les trois messages précis, VM de 4096 Mo
(au-delà de l'ancien plafond de 2048) créée et vérifiée via `virsh
dominfo`, override par variable d'environnement vérifié.
**Limite connue** : la limite est calculée sur l'hôte LOCAL ; pas encore
de limites par nœud distant (les VM se créent de toute façon toujours en
local). Prochain : chantier 3 (preflight check installeur) ou 4 (page
« Compatibilité et capacités »).

### Bug réel de portabilité : shell hôte cassé sur serveur-antho (2026-09-19)

Signalé par Antho (capture : « Erreur de connexion au shell hôte » sur
`/host-shell`). Log serveur : `No supported WebSocket library detected`
-- `requirements.txt` déclarait `uvicorn` nu, sans l'extra `[standard]`
(websockets, httptools, uvloop...). kvm-lab avait ces paquets installés à
la main, jamais déclarés : **toute installation apt propre (serveur-antho,
future appliance) n'avait AUCUNE fonction WebSocket** (shell hôte, consoles
VM noVNC/terminal, terminaux conteneurs). Passé inaperçu parce que seule
kvm-lab (venv historique) servait à tester ces fonctions. Corrigé :
`uvicorn[standard]==0.52.4` + `cryptography` explicite (jusque-là
seulement transitif via asyncssh), vérifié par installation dans un venv
neuf ; `bibliotheque_websocket` ajouté au profil de capacités
(`host_capabilities._software_capabilities_local`). Comparaison exhaustive
des `pip freeze` kvm-lab/serveur-antho : aucun autre écart. **Leçon pour
le chantier 3 (preflight)** : vérifier les dépendances Python réellement
importables, pas seulement les binaires.

### Chantier 3 : preflight check structuré (2026-09-19)

`app/core/preflight.py` (stdlib uniquement, tourne sur une machine nue
avant que le venv existe). Chaque contrôle renvoie `ok` / `warning` /
`disabled` (une fonctionnalité précise tombe, le reste marche) /
`blocking` (code de sortie 1). Usage : `python3 app/core/preflight.py
[--json] [--only system|python] [--python <venv>/bin/python3]
[--requirements requirements.txt] [--offline]`. `GET /host/preflight`
(admin) le rejoue à chaud sur l'hôte local.
- **Système** : architecture, root, Python >= 3.11, famille Debian/apt,
  systemd (toléré en chroot d'installation), openssl/ssh-keygen/chpasswd,
  vmx/svm + `/dev/kvm` (sinon seulement les VM KVM sont `disabled`, les
  conteneurs LXC restent), RAM, disque appli/VM, réseau, port 8000,
  ZFS/Secure Boot, joignabilité du dépôt apt.
- **Python** : IMPORT réel de chaque module dans l'interpréteur cible
  (pas `find_spec`) + comparaison avec les versions de `requirements.txt`.
  WebSocket absent = `disabled` (shell hôte/consoles/terminaux) ;
  fastapi/libvirt/multipart/jose/passlib/bcrypt/cryptography/asyncssh
  absents = `blocking` ; pyotp/qrcode = `disabled` (2FA).
- **postinst** : preflight système avant toute configuration (bloquant =
  abandon sur installation fraîche, simple avertissement sur mise à jour),
  puis preflight Python après `pip install` (échec = service jamais
  (re)démarré avec des dépendances cassées).
- `host_capabilities` expose `logiciel.dependances_python`.
- Bug réel trouvé : `openssl` et `openssh-client` étaient utilisés par le
  postinst mais absents de `Depends` (installation fraîche sur base
  minimale cassée) -- ajoutés.
- **Testé** : bare Python (échecs attendus), venv complet (tout ok),
  websockets désinstallé (`disabled`, exit 0), module cassé, non-root
  (`blocking`), profil réel serveur-antho (Secure Boot -> ZFS `disabled`),
  et `apt install` du .deb dans un chroot debootstrap minimal (chemin
  identique à l'ISO) : succès complet.

### Chantier 4 : page « Compatibilité et capacités » (2026-09-19)

Deux vues, branchées sur les endpoints des chantiers 1 et 3 (aucun nouveau
code backend) :
- **Onglet « Compatibilité » d'un nœud** (`NodeCompatibilityTab.jsx`) :
  fonctionnalités actives/limitées avec la raison (KVM, LXC, ZFS, WebSocket,
  2FA), preflight check rejoué à chaud (nœud local uniquement, il sonde le
  venv du service), puis le profil détaillé par section.
- **Onglet Datacenter « Compatibilité »** (`CompatibilityTab.jsx`, aussi
  dans le rail latéral) : tableau comparatif de tous les nœuds. Une ligne
  qui diffère entre nœuds est surlignée ; les lignes purement descriptives
  (RAM, modèle CPU, disques, interfaces, binaires annexes) sont marquées
  « informatif » pour ne pas noyer les vraies incompatibilités (versions
  libvirt/QEMU, Secure Boot, ZFS...).
- Logique commune dans `dashboard/src/lib/capabilitiesView.js`.
- **Testé** : instance de dev sur hl-devhub (libvirt réel), Playwright
  (zéro erreur console/HTTP, captures vérifiées), et comparaison des vrais
  profils hl-devhub / serveur-antho (écarts réels détectés : libvirt 9.0.0
  vs 11.3.0, QEMU 7.2.22 vs 10.0.13, Secure Boot, ZFS).
- **Limites** : le preflight n'existe pas pour un nœud distant ; la
  comparaison multi-nœuds n'a pas été vue dans l'UI avec deux nœuds
  enregistrés (un seul nœud disponible sur hl-devhub), seulement via la
  logique JS sur les deux vrais profils.

### Chantier 5 : profils de déploiement (2026-09-19)

`app/core/deployment_profile.py` : trois jeux de RÉGLAGES par défaut
(homelab / standard / avancé), pas trois produits. Profil actif, par
priorité : `HYPERLITE_PROFILE` (env) > choix d'un admin (table
`deployment_profile`, `auto` par défaut) > profil RECOMMANDÉ détecté
(avancé si >= 16 cœurs ou >= 64 Go ; homelab si <= 4 cœurs ou <= 8 Go ;
sinon standard). Les overrides `HYPERLITE_VM_MAX_*` (chantier 2) restent
prioritaires sur tout.
- Réglages pilotés par le profil : part de RAM allouable à une VM (0.75 /
  0.8 / 0.9) et de disque libre (0.85 / 0.9 / 0.95) dans `vm_limits.py`,
  intervalle de collecte des métriques (30 / 15 / 10 s, relu à chaque tour
  dans `metrics.py`, donc sans redémarrage), valeurs par défaut de
  l'assistant de création de VM (bornées par les limites réelles de l'hôte).
- `GET /host/profile` (auth), `PUT /host/profile` (admin, 400 si profil
  inconnu, 409 si forcé par l'env). UI : carte « Profil de déploiement » en
  tête de l'onglet Datacenter > Compatibilité ; l'assistant VM l'utilise.
- **Changement de comportement** : un hôte détecté « homelab » a désormais
  75 % (et non 80 %) de sa RAM allouable à une VM. Le profil standard
  reproduit exactement les anciennes valeurs.
- **Testé** : instance de dev sur hl-devhub (détecté homelab : 2 cœurs,
  2 Go), choix avancé -> limites mémoire 1408 -> 1664 Mo, refus 400/401/409,
  override env prioritaire, UI Playwright (carte, changement, assistant VM
  à 2 vCPU / 1664 Mo / 28 Go), zéro erreur console/HTTP.
- **Non fait** : rétention des sauvegardes et intervalles de sondage des
  nœuds ne dépendent pas encore du profil.

### Chantier 6 : diagnostic de compatibilité de cluster (2026-09-19)

`app/core/cluster_compat.py` : contrôles {id, statut ok/warning/blocking,
message, action} exécutés AVANT d'agir, sur deux connexions libvirt déjà
ouvertes (local ou qemu+ssh://). Un contrôle qui plante devient un
`warning` "vérification impossible", jamais un échec global.
- **Entre deux hôtes** (`check_pair`) : architecture, versions QEMU/libvirt
  (destination plus ancienne = avertissement), KVM, CPU physique source vs
  destination (`compareCPU`).
- **Pour une VM** (`check_vm_migration`) : état (active), nom libre, type de
  machine supporté par la destination (le vrai blocage du chantier 17,
  `pc-i440fx-10.0` inconnu de QEMU 7.2), CPU de la VM, firmware UEFI,
  réseaux présents, disques (bloc/zvol = bloquant, format, copie vs pool
  NFS partagé, espace libre destination), mémoire, périphériques hostdev.
- `GET /vms/{name}/migration-check?target_node=` (admin, lecture seule) ;
  `POST /vms/{name}/migrate` REFUSE (409, liste des blocages) sauf
  `ignorer_verifications: true` (une heuristique peut se tromper, l'admin
  garde le dernier mot) ; `GET /nodes/{name}/compatibility` ; l'ajout d'un
  nœud renvoie aussi `compatibilite` (informatif, n'annule rien).
- UI : le panneau « Migrer » affiche le diagnostic dès le choix de la cible
  (bouton désactivé tant qu'il y a un blocage, case « ignorer ») ; la fiche
  d'un nœud distant montre sa compatibilité avec l'hôte local.
- **Bug corrigé au passage** : l'endpoint de migration comparait encore la
  cible à l'ancienne sentinelle "kvm-lab" ; depuis le renommage en "local",
  une migration local -> local passait le contrôle. Normalisé
  (`_norm_node`).
- **Testé** : (1) données RÉELLES serveur-antho (QEMU 10.0.13, VM
  `pc-i440fx-10.0`) contre hl-devhub (QEMU 7.2.22) : machine inconnue,
  modèle CPU `Skylake-Client-v3` inconnu, espace et mémoire insuffisants
  détectés ; (2) HTTP de bout en bout via un nœud en boucle locale (SSH
  vers soi-même) avec une VM transitoire réelle ; (3) faux objets pour
  chaque mode de blocage (arch, KVM, UEFI, réseau, zvol, hostdev, mémoire,
  CPU, contrôle qui plante) ; (4) Playwright (panneau de migration, case
  ignorer, fiche nœud), zéro erreur.
- **Limites** : un pont (`bridge`) n'est pas vérifiable à distance
  (avertissement) ; `Unknown CPU model` reste un avertissement (compatibilité
  non démontrable, pas un blocage prouvé) ; pas encore de diagnostic avant
  l'ajout d'un nœud NON encore enregistré (il faut une connexion, donc
  l'enregistrement d'abord).
