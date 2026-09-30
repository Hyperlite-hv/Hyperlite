# Hyperlite user guide

This guide explains how to use Hyperlite from the web interface: what each page is for and how to do the common
tasks, from the first sign-in to backups, clusters and access control. For installation see
[deployment.md](deployment.md); for environment variables see [configuration.md](configuration.md); for the API see
[api.md](api.md).

Names in **bold** are the labels you see in the interface (English; the interface is also available in French from the
account menu).

## Contents

1. [First steps](#1-first-steps)
2. [Finding your way around](#2-finding-your-way-around)
3. [Virtual machines](#3-virtual-machines)
4. [Containers](#4-containers)
5. [Kubernetes clusters](#5-kubernetes-clusters)
6. [Storage](#6-storage)
7. [Networks](#7-networks)
8. [Library: ISO images, templates, imported disks](#8-library-iso-images-templates-imported-disks)
9. [Snapshots, backups and restores](#9-snapshots-backups-and-restores)
10. [Nodes and the cluster](#10-nodes-and-the-cluster)
11. [High availability](#11-high-availability)
12. [Tasks, audit log, notifications, metrics](#12-tasks-audit-log-notifications-metrics)
13. [Automation](#13-automation)
14. [Users, roles and permissions](#14-users-roles-and-permissions)
15. [Sign-in: passwords, 2FA, SSO, LDAP](#15-sign-in-passwords-2fa-sso-ldap)
16. [Your account and preferences](#16-your-account-and-preferences)
17. [Access from your workstation](#17-access-from-your-workstation)
18. [Updating Hyperlite](#18-updating-hyperlite)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. First steps

1. Open `https://<host>:8000` in a browser. The certificate is self-signed at first: accept the warning, or install a
   real certificate (see [HTTPS certificate](#https-certificate)).
2. Sign in as `admin` with the initial password (shown on the appliance's console banner, or in
   `/root/.hyperlite-initial-password` after an APT installation).
3. Choose a new password when asked: at least 12 characters, not the account name, not a common password or sequence.
4. Recommended next steps:
   - turn on two-factor authentication for `admin` (**Account → Account security**);
   - install a certificate your browsers trust (**Nodes → your node → System → HTTPS certificate**);
   - create accounts for the other people instead of sharing `admin` ([section 14](#14-users-roles-and-permissions));
   - check that the host has what you need (**Compatibility**).

The **Home** page then shows the state of the datacenter: nodes, VMs, containers, alerts, storage pools and recent
activity.

## 2. Finding your way around

- **Sidebar**: the sections (Infrastructure, Cluster, Protection, Library, Operations, Administration). Its toggle
  button hides it; it can be resized.
- **Top bar**: the breadcrumb, the search (**Ctrl+K**) to jump to any node, VM or page, the activity button (alerts and
  running tasks), and **Create** (virtual machine, container, storage pool, virtual network, user).
- **Object pages** (a node, a VM, a container) have a header with the state and the main actions, and tabs. A tab with
  several pages (VM **Hardware**) shows them in a menu on its left.
- **Right-click** any row of a list (VMs, nodes, containers, pools, networks, ISO images, snapshots, backups, users…)
  for the same actions as its **Actions** menu. **Copy link** gives a URL that opens the object directly.
- An **(i)** next to a setting explains what it does; hover it or reach it with the keyboard.
- A greyed action says why it is unavailable (not running, administrators only, local host only…).

Roles, in short: an **administrator** can do everything; an **observer** sees everything and can open VM consoles;
extra rights on chosen VMs, pools or containers come from assignments ([section 14](#14-users-roles-and-permissions)).

## 3. Virtual machines

### Create a VM

**Create → Virtual machine** opens an eight-step form: **Source, Identity, Placement, Compute, Storage, Network,
Advanced, Review**. The right-hand column sums up the choices.

- **Source**:
  - *Debian 12 cloud image*: preinstalled, ready in a minute with the user and password (and SSH keys) you set. The
    guest agent is installed at first boot.
  - *ISO*: Debian family, Ubuntu and RHEL family ISOs install unattended with your account; other ISOs (Windows
    included) boot for a manual installation through the console. An ISO stored on another node can be used.
  - *Import a disk*: boot from an existing qcow2, raw, vmdk, vdi or vhd disk (uploaded in the Library).
- **Placement**: the node and the storage pool (a shared NFS pool is needed for HA and migration without copying the
  disk).
- **Compute**: vCPU and memory. Values above the host's hardware only raise a warning.
- **Storage**: one or more disks; on an iSCSI pool, a VM takes a whole LUN (overwritten only after a confirmation).
- **Advanced**: firmware (BIOS, UEFI, UEFI with Secure Boot and TPM for Windows 11), disk controller, drivers ISO,
  automatic clean-up after a number of days stopped.

The creation runs as a task; the VM then appears in **Virtual Machines**. For Windows see [windows.md](windows.md).

### The VM list

**Virtual Machines** lists every VM of every node, problems first.

- Filter by state, node or tag, and search by name, IP, system, node or tag.
- **Group by**: node (a band per node with its load, foldable), tag, pool (administrators), or no grouping. A VM with
  several tags or pools shows under each.
- **Columns**: choose the optional columns (node, IP address, system, CPU · memory, uptime).
- **Saved views**: **Save the view** keeps the current filters, grouping, columns and sort under a name; pick it from
  **Saved views** to come back to it. Views and list settings are kept in your browser.
- **Bulk actions**: tick VMs (or the header box for every VM shown), then start, stop, force stop, restart, migrate or
  delete them together. The confirmation names the VMs concerned and those left as they are; failures are reported by
  name.
- Selecting a row opens a side panel with the last hour of CPU and memory, the address and quick actions.

### A VM's page

The header shows the state, system, node, IP and uptime, with **Start / Stop**, **Open the console** and **Actions**
(restart, force stop, snapshot, back up now, HA protection, clone, migrate, convert to template, export, automatic
clean-up, copy link, delete).

When a running VM has settings that only its next start applies (vCPU, memory, machine type, CPU, firmware, disks,
network cards, boot order, host devices), a **pending changes** box under the header lists them with the current and
next values. A reboot from inside the guest is not enough: stop the VM, then start it.

Tabs:

- **Summary**: configuration, protection (backups, snapshots, HA), recent activity, **Notes and tags** (free text for
  the team, and short lowercase tags used by the list's filter, grouping and backup jobs). The guest agent state is
  shown: with it, shutdown and reboot go through the guest and the IP comes from the guest.
- **Performance**: CPU, memory, disk and network history.
- **Snapshots** and **Backups**: see [section 9](#9-snapshots-backups-and-restores).
- **Hardware**:
  - *Hardware*: disks (add, grow live or stopped, move to another pool live or stopped, detach), CD-ROM, network
    cards, host devices (USB, PCI/GPU; see [passthrough.md](passthrough.md)).
  - *Advanced*: per disk the cache mode, discard (TRIM), I/O mode, I/O thread and IOPS / MB/s limits (limits apply at
    once, the rest at the next start; **What these settings do** explains each one); the **boot order** across disks and
    network cards; **memory ballooning** with a minimum; updating the **machine type** to the current version of its
    family (VM stopped).
  - *Options and limits*: **start at boot** (with an order and a pause before the next VM; run once per boot of the
    node), resources, CPU and I/O priorities, CPU affinity.
  - *Cloud-init* (cloud-image VMs): change the user's SSH keys and password; applied at the next boot, the password is
    never stored.
- **Network**: interfaces (network, VLAN tag), the VM's firewall.
- **Console**: see below.
- **Tasks**: the VM's own history, with each task's log.
- **Permissions** (administrators): who has rights on this VM, and adding or removing an assignment in place.

Pages that manage the local host's files only (Hardware, Network, Backups, Snapshots…) explain themselves for a VM on
another node: open that node's own Hyperlite for them.

### The console

**Console** connects by itself as soon as the VM runs:

- **Graphical console (VNC)**: the VM's screen in the browser. **Ctrl+Alt+Del**; **Keys** sends the combinations your
  own computer would catch (Ctrl+Alt+F1/F2/F7, Alt+Tab, Alt+F4, Windows key, Print Screen); **Type text** types what
  you enter as key presses, which works at a login prompt (the guest's keyboard layout must match yours for symbols);
  when the guest sends its clipboard (with an agent), it shows with a **Copy** button. **Full screen** and **Open in a
  new window** are there too.
- **SSH terminal** (administrators): a terminal on the VM through Hyperlite's key.
- **From your workstation**: SSH or remote desktop from your own tools ([section 17](#17-access-from-your-workstation)).

### Rename, clone, template, export, migrate

- **Rename…** (Actions menu, administrators) gives a stopped VM without snapshots a new name. Its settings, backups
  and their schedule, pools and permissions, HA protection, start at boot, notes, tags and metrics follow it; its
  cloud-init drive and UEFI variables are renamed with it, its disk files keep their names. The VMs of a Kubernetes
  cluster keep theirs.
- **Clone** makes an independent copy. **Convert to template** turns a stopped VM into a template to deploy from
  (Library).
- **Export** writes the VM's disk to **Exports** for download, to move it to another hypervisor.
- **Migrate** moves a VM to another node, live when it runs. A compatibility check comes first (CPU, versions, machine
  types, storage, networks). With local disks the disk is copied during the migration.

## 4. Containers

**Create → Container** offers two kinds:

- **LXC**: a small complete system (Debian base) with a built-in terminal.
- **Docker image**: any Docker Hub or OCI image (nginx, Redis…) run as a container with a fixed address; no Docker
  daemon is needed. An image is downloaded once. Images that do not start without some variables (PostgreSQL's
  `POSTGRES_PASSWORD`, MySQL's or MariaDB's root password, SQL Server's licence and SA password…) get a field for
  each, with a generated password to note down; their useful variables are one click away.

**Containers** lists them; a container's page has **Summary** (resources: memory live, CPUs at the next start; network
interfaces; DNS servers; start at boot; notes and tags), **Console** (terminal, or the program's output for an image),
**Backups** (cold backups and restore; the container must be stopped), **Tasks** and **Permissions**.

- **Root shell** (administrators, running container): a shell inside the container, like `docker exec -it … sh`,
  for Docker images too (bash when the image has it, else sh). The **Terminal** of an LXC container connects over SSH
  with its account.
- **Stop** asks the container's programs to stop (SIGTERM for a Docker image, as `docker stop` does); a container
  still running after 30 s (Docker) or 90 s (LXC) is stopped by force.
- **Rename** (stopped container): its directory, host name, address, backups and notes follow the new name.

## 5. Kubernetes clusters

**Kubernetes** creates k3s clusters on Hyperlite VMs: one server and workers, installed and joined for you (a few
minutes; follow it in **Tasks**). Download the **kubeconfig**, then `export KUBECONFIG=./<name>.kubeconfig && kubectl
get nodes`. The kubeconfig grants full administrator access to the cluster: keep it safe. Deleting a cluster deletes
its VMs and their disks.

## 6. Storage

**Storage** lists the pools of every node with their use and history. **Create → Storage pool**:

| Type | For | Notes |
|---|---|---|
| Directory | Local disks as files | The default pool exists already. |
| NFS | A share on a NAS | Shared between nodes: needed for HA and for migration without copying disks. Hyperlite checks that QEMU can use the share (permissions). |
| ZFS | VM disks as zvols with native snapshots | Local to one node (created on a file); no live migration. |
| iSCSI | LUNs of a storage array | Each VM takes a whole LUN; allow this host's initiator name on the array first. The LUNs stay on the array when the pool is removed. |

The form says when a client is missing on the host (NFS, ZFS module with Secure Boot, iSCSI initiator) and how to
install it. A pool's volumes can be created and deleted from its row (a disk used by a VM cannot be deleted). Removing
a directory or NFS pool never deletes its files.

**My preferences** chooses which pools the **Home** page follows.


### Shared storage on several nodes

An NFS share or an iSCSI target is the same storage for every node that reaches it. When creating one, **Nodes**
offers, as on Proxmox: **Every node** (the default with several nodes; a node registered later gets it too), **One
node**, or **Choose the nodes**. The pool is created on each of them under the same name and mount point, which live
migration and HA need; the result says, node by node, whether it was created, already there, or why it failed. Shared
pools are marked in the list; deleting one removes it from every node that has it.

## 7. Networks

**Network** lists the virtual networks. **Create → Virtual network**:

- **NAT**: VMs reach outside through the host; a DHCP server gives addresses.
- **Isolated**: VMs talk to each other only.
- **Bridge**: VMs sit on the host's network (an existing bridge, or a network card, bond or VLAN interface through
  macvtap).

A network's **Details**:

- **Subnet and DHCP** (NAT and isolated networks, administrators): change the mode, gateway, mask and DHCP range. The
  subnet may not overlap another network. A DHCP range change applies at once; a new subnet or mode is saved and taken
  by the network at its restart: **Restart the network** (it briefly disconnects its VMs).
- **Addresses**: **reservations** (a fixed address for a MAC: added by hand, or with **Reserve** from a current lease)
  and the current **DHCP leases** with their end.
- **Firewall**: rules for the whole network (each VM also has its own firewall in its **Network** tab).

VLAN tags are set per VM network card. Starting, stopping, autostart and deletion are in the row's buttons and menu (a
network still used by a VM cannot be deleted).

## 8. Library: ISO images, templates, imported disks

**ISO images and templates**:

- **ISO images**: upload an ISO, or share one with other nodes. VMs can install from an ISO of another node.
- **Templates**: deploy a new VM from a template (made with a VM's **Convert to template**).
- **Imported disks**: disks uploaded to create VMs from ([section 3](#create-a-vm)).

## 9. Snapshots, backups and restores

### Snapshots

A VM's **Snapshots** tab takes, restores and deletes snapshots (disk and state; native ZFS snapshots on ZFS pools).
**Snapshots** in the sidebar lists them for every VM. Snapshots stay on the same storage: they are not backups.

### Backups of one VM

A VM's **Backups** tab:

- **Back up now**: hot (the VM keeps running; with the guest agent the file systems are frozen for a consistent copy)
  or cold (VM stopped).
- **Schedule**: daily, weekly or monthly at a time (UTC), with a **retention**: the last N backups, plus optionally
  the newest backup of each of the last days, weeks and months (grandfather-father-son). The retention applies to every
  backup of the VM, manual ones included.
- For each backup: **Verify** (checksums and image checks; every backup is also verified automatically within a
  week), **In place** (restore over the VM, stopped first), **New VM** (restore under a new name), **Files** (see
  below), delete.

UEFI VMs get their firmware state (boot entries, Secure Boot keys, TPM) saved and restored with the disks.

### Backup jobs for many VMs

**Backups → Backup jobs** (administrators) puts many VMs on one schedule: **All VMs of this node**, **VMs with a
tag**, or **VMs of a pool**, some left out if you want. The VMs are chosen again at each run, so a VM created or tagged
later is included. A job has its own retention; a VM with its own schedule keeps its own retention. **Run now** starts
it at once; each VM's backup shows in **Tasks**.

The **Backups** page lists every backup and warns about VMs with no scheduled backup.

### Restoring files from a backup

**Files** on a backup opens it read-only: pick a filesystem, walk the folders, and download a file, or a folder as a
`.tar.gz`, without restoring the VM. The first opening takes a few seconds (up to a minute on a host without hardware
virtualization); the backup is never changed. This needs `libguestfs-tools` and `python3-guestfs` on the node
(`apt install libguestfs-tools python3-guestfs`).

## 10. Nodes and the cluster

**Nodes** lists the machines: the local one (running this Hyperlite) and the members added by SSH, with their load.
Add a node from this page (its SSH access and a connection test).

A node's page:

- **Summary**: resources, the VMs on it, configuration and alerts, notes and tags.
- **Performance**: history of the node.
- **System** (local node): hardware and software, the **HTTPS certificate**, and **DNS**, **Time** and **Remote
  syslog** (administrators).
- **Updates** (local node, administrators): see below.
- **Network**, **Storage**: the host's interfaces and disks.
- **Tasks**, **Compatibility** (what the host supports and why), **Shell** (a root shell, administrators).
- **Actions**: create a VM here, open the shell, **Rename…** (this node: its host name and `/etc/hosts` line; a
  registered node: the name Hyperlite shows, not its host name; its VMs' settings, notes and metrics follow), **maintenance mode**, **Reboot the node…**, **Shut down the node…**,
  refresh capabilities.

### Maintenance mode

**Enter maintenance** picks where the running VMs go and shows the plan (which VMs move, which stay and why) before
starting. The VMs are live-migrated one after another; a node in maintenance receives no new VM and is never a target.
**Leave maintenance** when the work is done.

### Reboot or shut down a node

**Reboot the node…** / **Shut down the node…** (local node, administrators) asks you to type the node's name. If VMs or
containers run, move them first (maintenance mode) or tick the box to shut them down cleanly: the node goes down only
if all of them stopped within 3 minutes. A node that is shut down can only be started again from its power button or
its management card (IPMI, iDRAC, iLO).

### Package updates

**Updates** lists the upgradable packages, marks the security ones, and says when the node needs a reboot to finish
an update. **Security only** narrows the list; **Upgrade** runs apt as a task (its output in the task's log). Running
VMs keep running; a QEMU update applies to each VM at its next start. Hyperlite's own package is updated from its own
page ([section 18](#18-updating-hyperlite)).

### DNS, time, remote syslog

- **DNS**: the servers and search domains, changed where the system reads them (systemd-resolved or a plain
  `/etc/resolv.conf`). When the file is written by DHCP or NetworkManager, the page says so: change it there.
- **Time**: time zone, network time (NTP) and its servers.
- **Remote syslog**: forward the system log to a server (UDP or TCP); needs `rsyslog`.

### HTTPS certificate

**System → HTTPS certificate** shows the certificate's names, issuer and expiry, and replaces it:

- **Import**: a PEM certificate (optionally followed by its chain) and its private key without passphrase. The pair is
  checked before it replaces the current one, which is kept.
- **Let's Encrypt**: the domain must point to this node and port 80 must reach it from the Internet during the
  request; needs `certbot`, which renews the certificate by itself. A **Test certificate** option uses Let's Encrypt's
  staging server.
- **Go back to the previous certificate**, or **Use a self-signed certificate**.

Hyperlite restarts to use the new certificate; the page reconnects after a few seconds.

## 11. High availability

A VM can be protected (**Actions → HA protection**) when all its disks are on shared storage (NFS). **High
availability** shows the protected VMs and their node. If a node goes down, an alert is raised and an administrator
recovers the VM on another node; recovery is never automatic, and a best-effort fence over SSH is attempted first.
Automatic HA runs as a **dry run**: it shows what it would do (with the per-node fencing settings: IPMI, Redfish, Intel
AMT or leases) without powering anything off. See [cluster-failover.md](cluster-failover.md) for taking over when the
controller is lost.

Hyperlite does not replicate storage between nodes; see [design/replication.md](design/replication.md) for why and what
covers the need.

## 12. Tasks, audit log, notifications, metrics

- **Tasks**: every long operation, with its status and progress (tabs All, Running, Failed). Selecting a task shows its
  log. A running task can be **cancelled** when it knows how to stop (migrations, drains, disk moves, backups, exports,
  automation runs); a task nobody runs any more (after a service restart) can be closed. Export to CSV.
- **Audit log**: who did what, on what, when, with filters and a complete CSV export. Old entries are removed after
  `HYPERLITE_AUDIT_RETENTION_DAYS` days (365 by default).
- **Notifications** (administrators): channels (webhook, e-mail over SMTP) that receive a message on important events
  (VM crashed, backup failed or corrupted, node offline…); a channel with no event chosen receives all of them.
- **Metrics** (administrators):
  - **Prometheus**: the `/metrics` endpoint and a ready scrape job. Authenticate with an API token
    ([section 16](#16-your-account-and-preferences)).
  - **Metric servers**: push every sample (every 15 s) to InfluxDB 2 (URL, organization, bucket, token) or Graphite
    (host, port, prefix). **Test** sends the latest samples at once and shows the error if any.

## 13. Automation

**Automation** runs jobs: ordered shell steps on the host or on VMs (installing a web server, configuring a load
balancer…). A dry run only previews; a real run shows the exact commands first. Runs appear in **Tasks** with their
output and can be cancelled.

## 14. Users, roles and permissions

**Users and roles** (administrators):

- **Users**: create accounts (**administrator** or **observer**), reset a password (the account's sessions and API
  tokens are signed out), change the role, delete. Accounts coming from SSO or LDAP are marked; their password is
  managed by the identity provider or the directory.
- **Groups**: gather users to give them rights together.
- **Roles**: the predefined scoped roles (**Reader**, **Operator**, **Manager**) and your own **custom roles** made of
  chosen privileges (power, console, snapshots, hardware, options…).
- **Pools**: sets of VMs to give rights on together.
- **Assignments**: give a user or a group a role on a VM, a pool or a container.

The **Permissions** tab of a VM or container shows and edits the assignments of that object, including those a VM
inherits from its pools.

## 15. Sign-in: passwords, 2FA, SSO, LDAP

- **Local accounts** sign in with their password. Repeated failures lock the account for a while (per account and
  address).
- **Two-factor authentication**: each user can add a TOTP code (an authenticator app) and security keys or passkeys
  (YubiKey, Windows Hello, Touch ID, a phone) from **Account security**. Keys need HTTPS through a host name, not an IP
  address.
- **Single sign-on (OIDC)**: **Authentication (SSO)** sets the identity provider (issuer, client, secret, groups claim,
  administrator groups). A **Sign in with SSO** button then shows on the sign-in page; the role follows the IdP groups.
  Local sign-in stays available.
- **LDAP / Active Directory** (same page): directory accounts sign in with the usual form.
  - Set the URL (`ldaps://` or `ldap://` with StartTLS), the search base, a read-only service account, the user filter
    (for Active Directory `(&(objectClass=user)(sAMAccountName={username}))`), the group attribute (`memberOf`), the
    **administrator groups** and optionally the **allowed groups**.
  - **Test with these settings** checks the service account and, with a user's name and password, says which role they
    would get, without creating the account.
  - A name that belongs to a local account keeps its local password: the directory never takes it over. Directory
    users get their role from their groups at each sign-in (administrator groups → administrator, others → observer,
    plus assignments).

## 16. Your account and preferences

The account menu (bottom of the sidebar):

- **Language** (English, Français) and **Theme** (dark, light, system).
- **Check for updates** (administrators).
- **Change my password** (local accounts).
- **Account security**: two-factor authentication, security keys, and **API tokens** for scripts and Prometheus
  (with an expiry; a token is shown once).
- **My preferences**: the terminals' font and size, and the storage pools the **Home** page follows. Kept in this
  browser.
- **Sign out**, which also ends the session on the server.

## 17. Access from your workstation

From a VM's **Console → From your workstation**:

- a VM on a bridged network is reached directly: the panel gives the `ssh user@address` command, or a `.rdp` file for
  Windows;
- otherwise, the `hyperlite` client (Windows, Linux, macOS, downloaded from the panel) opens SSH or remote desktop
  through a tunnel over the server's HTTPS port, after signing in through the web interface (SSO and 2FA apply). The
  `vm.tunnel` right is needed.

Remote desktop (RDP) also carries the clipboard, drives and USB devices for Windows VMs. See
[workstation-access.md](workstation-access.md).

## 18. Updating Hyperlite

**Account → Check for updates** (administrators) shows whether a new version is available and installs it. The update
is backed up first and rolled back automatically if the new version does not start. The node's other packages are
updated from the node's **Updates** tab.

## 19. Troubleshooting

| Symptom | What to check |
|---|---|
| The browser warns about the certificate | Normal with the self-signed certificate: install one ([HTTPS certificate](#https-certificate)). |
| A button is greyed | Its tooltip says why: VM state, role, or a local-host-only action on a remote node. |
| A VM will not migrate | Read the compatibility check in the migration dialog; ZFS disks and CPU pinning block live migration. |
| NFS pool errors | The pool's permission check says whether QEMU can write there; the server must allow root (no_root_squash) or the libvirt user. |
| A VM has no IP shown | Install the guest agent (Hyperlite Tools), or wait for its DHCP lease on a NAT network. |
| Changes do not apply to a running VM | The **pending changes** box lists them: stop, then start the VM. |
| **Files** on a backup fails | Install `libguestfs-tools` and `python3-guestfs` on the node. |
| Nothing works after a network change | Open **Tasks** and **Audit log**, and the service log: `journalctl -u hyperlite -n 100 --no-pager`. |

Problems and questions: open an issue on the project's GitHub repository; security issues follow
[SECURITY.md](../SECURITY.md).
