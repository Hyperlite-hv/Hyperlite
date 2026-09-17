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
| 16 | Document récapitulatif final (PDF/Markdown) | ⬜ pas commencé — à faire en dernier |
| 17 | HA basique (détection panne nœud + récupération manuelle) | ✅ dans `master` — **scope volontairement prudent, PAS de fencing/STONITH** (voir section dédiée plus bas) : détecte un nœud tombé et alerte, la récupération reste toujours déclenchée par un admin, jamais automatique. **Testé réellement de bout en bout** sur kvm-lab + serveur-antho (panne simulée, alerte confirmée, VM protégée récupérée avec succès sur l'autre nœud, disque partagé intact) |
| 18 | Conteneurs (LXC) et tout l'outillage associé | ✅ dans `master` — pilote LXC natif de libvirt (lxc:///system), image de base Debian 12 via debootstrap (cache, clonage rapide par conteneur) **ou image Docker Hub/registre OCI au choix** (`skopeo`+`umoci`, pas de démon Docker requis ; bootstrap post-pull systemd+openssh+sudo côté apt, openrc+openssh+sudo côté apk, création des nœuds `/dev` manquants — testé réellement sur `alpine:3.19` et `debian:12`), terminal web SSH (même clé d'automatisation que les VM), sudo NOPASSWD, onglet Datacenter dédié + champ de sélection d'image dans les deux formulaires de création. **systemd-networkd, pas ifupdown/isc-dhcp-client** (côté apt) : le profil AppArmor de libvirtd sur cet hôte bloque un signal vers dhclient, cassait `destroy`/suppression (trouvé et corrigé en testant). Pas encore fait : snapshots/clonage de conteneur, ACL granulaire (réservé admin pour l'instant), galerie de templates visuelle |
| 23 | Export/Import de VM depuis un fichier disque | ✅ dans `master` — bouton "Exporter le disque" (menu d'actions VM, disque système uniquement, chaud ou froid selon l'état, réutilise le mécanisme du chantier 13), onglet Datacenter > Exports (liste/télécharge/supprime, téléchargement par ticket à usage unique), option "Importer un disque existant" dans le formulaire de création de VM (upload + sélection). Bug réel trouvé et corrigé en testant : un disque importé garde le netplan MAC-épinglé de son tout premier démarrage (cloud-init) — nouvelle MAC = plus aucune interface ne correspond, réseau mort. Corrigé via un ISO de "reseed" cloud-init (nouvel instance-id, même mécanisme que le clonage chantier 5) qui force cloud-init à régénérer son réseau. Testé réellement de bout en bout (export à chaud + import + SSH fonctionnel) |
| 24 | Refonte tableau de bord + barre latérale façon Proxmox VE | ✅ dans `master` — rail de navigation (`SidebarRail.jsx`) ajouté à gauche de l'arbre Datacenter/Nœud/VM existant (purement additif, l'arbre reste les raccourcis VM), calqué sur les onglets Datacenter réels seulement (pas la liste complète de Proxmox). Nouvel onglet "Activité récente" (table `tasks` existante, pas encore exposée au niveau Datacenter). "Statut des VM" devient une vraie liste sur les états réels d'un domaine libvirt. **Pas de vérification visuelle possible depuis cette session (pas de navigateur connecté) — à confirmer par Antho** |
| 19 | Suppression automatique des VM inactives (option à la création, ex. 7 jours sans usage) | ✅ dans `master` — `app/core/vm_cleanup.py`, opt-in par VM (à la création ou après coup, `PUT /vms/{name}/auto-cleanup`). Le compteur ne court que pendant que la VM est ARRÊTÉE (jamais une VM en marche), jamais une VM protégée HA, avertissement ~24h avant suppression réelle (notifications, chantier 28). **Testé réellement** : cycle de vérification déclenché manuellement avec des horodatages simulés (au-delà/en-deçà du seuil) — avertissement, suppression réelle (VM + disque + entrées DB), et les deux garde-fous (VM active, VM HA) vérifiés un par un. UI (assistant de création + panneau sur la fiche VM) testée dans un vrai navigateur |
| 20 | SSO (LDAP/OIDC/SAML — à préciser) | ⬜ pas commencé — demandé le 2026-09-13. Aujourd'hui authentification locale uniquement (`app/core/security.py`, JWT) |
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
(pare-feu datacenter) ✅ → 19 (nettoyage VM inactives) ✅ →
**20 (SSO) ← prochain** → 16 (doc récap, en dernier). **Chantier 12 (kickstart) explicitement
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
   Collision residuelle non traitee : deux noms de reseau partageant
   leurs 9 premiers caracteres genereraient le meme nom de pont (echec
   "existe deja" a la creation du second) -- limite pre-existante,
   documentee plutot que corrigee (necessiterait un nom de pont derive
   par hash, hors scope de ce correctif ponctuel).

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
