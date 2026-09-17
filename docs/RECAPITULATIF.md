# Hyperlite — Document récapitulatif

*Dernière mise à jour : 2026-09-17. Ce document est le chantier 16 (le
dernier) de la roadmap "rapprocher Hyperlite du niveau vSphere/vCenter"
lancée le 2026-09-13. Il donne une vue d'ensemble lisible du projet ;
pour l'historique détaillé de chaque décision, bug trouvé et test
effectué, voir [`CLAUDE.md`](../CLAUDE.md) à la racine du dépôt.*

## 1. Qu'est-ce qu'Hyperlite ?

Hyperlite est un hyperviseur web auto-hébergé : un backend FastAPI qui
pilote libvirt/QEMU-KVM, et un dashboard React/Vite qui reproduit
l'expérience d'un outil comme Proxmox VE ou VMware vSphere — sans
souscription, sans compte tiers, sur du matériel possédé.

**Positionnement** : conçu à l'origine comme un projet personnel
(un serveur, `kvm-lab`), étendu au fil de la roadmap 2026-09-13 pour
couvrir un vrai cas multi-sites (deux machines physiques reliées par
Tailscale : `kvm-lab` et `serveur-antho`) avec des fonctionnalités que
même certains outils commerciaux n'ont pas de série (ex. choix libre
entre image Docker Hub et debootstrap pour les conteneurs, gestion de
dépôt APT façon Proxmox construite maison).

## 2. Architecture

| Composant | Détail |
|---|---|
| Backend | FastAPI (Python), pilote libvirt via `libvirt-python`, SQLite (`hyperlite.db`, mode WAL) pour l'état applicatif |
| Frontend | React + Vite, servi en fichiers statiques buildés (`dashboard/dist`) par le même service que l'API |
| Service | `systemd` (`hyperlite.service`), HTTPS auto-signé sur le port 8000 |
| Hyperviseur | libvirt/QEMU-KVM (VM), pilote LXC natif de libvirt (conteneurs) |
| Auth | JWT de session (`app/core/security.py`), 2FA TOTP + jetons API en option, SSO OIDC en option — voir §7 |

**Machines réelles** (pas un déploiement théorique) :
- **`kvm-lab`** — la machine de développement, reste volontairement en
  clone Git (`git pull` déclenche un hook local qui republie
  automatiquement le paquet et l'ISO, voir §6). C'est là que tourne
  cette session de travail.
- **`serveur-antho`** — un second serveur physique, sur un réseau
  différent, relié à `kvm-lab` via Tailscale. Installé/migré vers le
  mécanisme `apt` (voir §6). Utilisé pour tous les tests multi-nœuds
  réels (migration à chaud, HA, stockage partagé NFS).

Les deux machines sont enregistrées comme nœuds du même cluster
Hyperlite (`qemu+ssh://`, clé SSH dédiée `data/ssh/hyperlite_cluster`),
consultable comme un seul datacenter depuis le dashboard.

## 3. Fonctionnalités (état au 2026-09-17)

### Gestion des VM
- Cycle de vie complet (créer/démarrer/arrêter/supprimer), snapshots,
  clonage, limites de ressources (cgroups CPU/RAM).
- Provisioning automatisé multi-OS (Debian/Ubuntu/RHEL — preseed/
  kickstart/autoinstall selon la famille), en cours d'extension à
  Kali/Alpine par un collègue sur une branche séparée.
- Export/import de VM par fichier disque (avec régénération réseau
  cloud-init automatique après import).
- **Suppression automatique des VM inactives** (opt-in, seuil en jours,
  avertissement avant suppression réelle, jamais une VM active ou
  protégée HA).

### Stockage
- Pools de stockage locaux et **réseau partagés (NFS)** — indispensable
  pour la HA et la migration à chaud entre deux hôtes.

### Réseau
- Réseaux virtuels (NAT/isolé/pont), VLAN.
- **Pare-feu par VM** (nwfilter libvirt) ET **pare-feu réseau/datacenter**
  (chaîne iptables dédiée au niveau du pont — deux granularités
  distinctes, la seconde ajoutée au chantier 21).

### Conteneurs
- LXC natif (pas de démon Docker requis), image de base au choix :
  debootstrap Debian **ou** n'importe quelle image Docker Hub/registre
  OCI (recherche par mot-clé incluse), terminal web SSH.

### Haute disponibilité, migration, sauvegarde
- **Migration à chaud** entre nœuds (`qemu+ssh://`, CPU "plus petit
  dénominateur commun" calculé à la création pour rester migrable).
- **HA basique** : détection de panne de nœud + alerte, récupération
  **toujours déclenchée par un humain** (pas de fencing/STONITH —
  choix de prudence assumé, documenté).
- Sauvegarde/restauration natives des VM, planification, politique de
  rétention (garder les N plus récentes, appliquée aussi bien aux
  sauvegardes manuelles que planifiées).

### Observabilité et automatisation
- Métriques continues (CPU/RAM/réseau/disque), historisées, exposables
  Prometheus.
- Journal d'audit, tâches horodatées avec suivi de progression.
- Moteur de jobs (onglet Automation) pour enchaîner des actions.
- Notifications sortantes (webhook générique compatible Discord/Slack/
  ntfy, ou email SMTP) déclenchées automatiquement sur les événements
  significatifs (panne nœud, alerte HA, création/suppression VM,
  migration, sauvegarde, mise à jour...) via un point d'entrée unique
  dans le code d'audit — pas un appel à ajouter à chaque endroit.

### Sécurité et comptes
- ACL granulaires par VM/pool, groupes d'utilisateurs, rôles
  personnalisés (catalogue de privilèges).
- Anti-brute-force sur la connexion.
- **2FA (TOTP)** en libre-service + **jetons API** dédiés à
  l'automatisation (scripts/Terraform), distincts du jeton de session.
- **SSO OIDC** (chantier 20, voir §7) — en plus de l'auth locale,
  jamais à sa place.
- Audit log écrit de façon asynchrone (file + thread dédié) pour ne
  jamais bloquer une requête HTTP, même sous forte charge concurrente.

### Interface
- Refonte visuelle "indigo console" façon Proxmox VE (rail de
  navigation, tableau de bord avec graphiques de tendance réels).
- Audité fonctionnellement avec Playwright (navigateur réel, pas
  seulement une relecture de code) à plusieurs reprises au fil des
  chantiers — méthode qui a trouvé des bugs réels invisibles à la
  simple lecture (menus qui restent ouverts, mauvais élément ciblé par
  un sélecteur trop permissif, etc.).

## 4. Ce qui reste volontairement hors scope

- **Fencing/STONITH** (HA) : redémarrer automatiquement une VM ailleurs
  sans confirmer que l'original est bien éteint risquerait une vraie
  corruption de données sur un disque partagé. Détection + alerte
  seulement, récupération toujours manuelle.
- **Migration d'un nœud distant vers `kvm-lab`** : la migration
  peer-to-peer exige une confiance SSH dans le sens inverse (nœud
  distant → `kvm-lab`), non mise en place. Bloqué explicitement côté
  API (message clair) plutôt que de tenter et échouer en silence.
- **Actions VM multi-nœuds** (`start`/`stop`/`delete` avec `node=`) :
  seule la visibilité (`GET`) est multi-nœuds aujourd'hui ; agir sur une
  VM distante demande de passer par ce nœud directement.
- **Rate-limiting par IP** (seulement par compte aujourd'hui).
- Snapshots/clonage de conteneur, ACL granulaire sur les conteneurs
  (réservé admin pour l'instant), galerie de templates visuelle.
- Chiffrement des secrets au repos (mots de passe SMTP, client secret
  OIDC...) : stockés en clair en base, même niveau de confiance que le
  reste de la configuration serveur — pas de coffre-fort de secrets
  dans ce projet à ce stade.

## 5. Bugs réels notables trouvés en testant (pas en relisant le code)

Ce projet a une discipline stricte de test en conditions réelles (VM
jetables, vrais nœuds physiques, vrai navigateur) plutôt que la seule
relecture. Quelques exemples qui illustrent pourquoi, en plus des tests
qui pointent chaque chantier dans `CLAUDE.md` :

- **Incohérence structurelle de GitHub Pages** : deux fichiers liés
  publiés dans le même commit peuvent être servis de façon incohérente
  par des nœuds CDN différents pendant plus d'une heure — invisible en
  local, trouvé en interrogeant le dépôt depuis plusieurs machines à la
  fois. Corrigé en servant le dépôt APT directement depuis `kvm-lab`
  (nginx, Tailscale uniquement, aucun CDN).
- **`systemctl start` silencieusement no-op dans le chroot de
  l'installeur Debian** (comportement standard mais non documenté
  intuitivement) : a cassé l'initialisation de la base de données lors
  du premier boot d'une appliance — corrigé en appelant directement la
  fonction Python de seed plutôt que de compter sur un démarrage de
  service impossible à cet instant précis.
- **Collision de nom de groupe de volumes LVM** entre le disque réel de
  l'hôte et celui d'une VM de test, toutes deux installées depuis le
  même gabarit preseed — near-miss détecté et évité avant toute
  corruption, en inspectant l'état réel (`lsblk`) avant d'agir plutôt
  qu'en supposant que l'opération était sûre.
- **`apt-cache policy` traduit ses champs** en locale française
  (`Candidat :` au lieu de `Candidate:`) — le parsing de la détection
  de mise à jour échouait silencieusement sur la première machine
  réellement en locale FR, jamais vu sur la machine de dev (en
  anglais). Corrigé en forçant `LC_ALL=C` sur toute commande dont la
  sortie est analysée par du code.

## 6. Déploiement et mise à jour

**Trois façons d'obtenir Hyperlite**, toutes alimentées par le même
dépôt APT signé :

1. **ISO d'installation automatisée** (Debian 13, preseed) —
   téléchargement public et toujours à jour :
   `https://github.com/twikles/hyperlite/releases/latest/download/hyperlite-appliance-amd64.iso`.
   Une seule confirmation humaine requise pendant l'installation (garde-fou
   volontaire de Debian Installer contre la perte de données, non
   contournable). Identifiants par défaut `root`/`hyperlite` (à changer).
2. **`apt install hyperlite`** sur une Debian existante déjà sur le
   réseau Tailscale du cluster (voir la commande exacte dans `CLAUDE.md`,
   section chantier 7bis).
3. **Clone Git** (`kvm-lab` uniquement — la machine de développement,
   pas destinée à un déploiement classique).

**Mise à jour** : bouton "Vérifier les mises à jour" dans le dashboard
(`GET /update/check` détecte automatiquement s'il faut parler `git` ou
`apt` selon la machine), sauvegarde complète prise avant toute
modification, restauration automatique en cas d'échec (watchdog). Ne
touche jamais aux VM déjà actives (même principe qu'un redémarrage de
vCenter n'affecte pas les VM sous ESXi).

**Publication automatique** (côté `kvm-lab`, jamais de CI tierce — la
clé de signature GPG ne quitte jamais cette machine, même principe que
l'infrastructure de build interne de Proxmox) : un hook Git local
reconstruit et republie le paquet, le dépôt APT et l'ISO à **chaque**
merge sur `master`, sans intervention manuelle. Un mécanisme de
vérification périodique (`app/core/update_check.py`) détecte aussi
automatiquement, côté chaque machine installée, quand une nouvelle
version est disponible et notifie l'admin (jamais d'application
automatique — la décision de mettre à jour reste toujours humaine).

## 7. Authentification

Trois mécanismes coexistent, jamais l'un au détriment d'un autre :

1. **Local** (mot de passe + JWT de session) — le mécanisme historique,
   toujours le secours ultime.
2. **2FA (TOTP)** — optionnel, en libre-service par utilisateur.
3. **SSO OIDC** — optionnel, activé et configuré par un admin (onglet
   Datacenter > SSO). Rôle global (`admin`/`observateur`) mappé depuis
   les groupes de l'IdP, réévalué à chaque connexion. Ne peut jamais
   écraser un compte local existant (protection anti-collision
   explicite).

Plus les **jetons API** (préfixe `hlt_`, hashés en base, jamais
récupérables en clair après création) pour l'automatisation
(Terraform, scripts, CI externe).

## 8. Pour aller plus loin

- Historique complet, chronologique, avec chaque bug trouvé et sa
  cause racine : [`CLAUDE.md`](../CLAUDE.md).
- Scripts d'installation et de publication : `installer/`.
- Tests UI réels (Playwright) menés au fil des chantiers :
  `/root/hyperlite-ui-test/` sur `kvm-lab` (hors dépôt Git, machine de
  développement uniquement).
