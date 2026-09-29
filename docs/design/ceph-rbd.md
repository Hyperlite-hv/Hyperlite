# Design: VM disks on an existing Ceph cluster (RBD)

Status: **proposal, waiting for the maintainer's decision.** No code is written before it is accepted.

Decided already: Hyperlite **connects to an existing Ceph cluster**; it does not install or manage Ceph (no monitors,
no OSDs). iSCSI with shared LVM and multipath are postponed until there is an array to test on.

## 1. Where we are

- VM disks are files (directory and NFS pools), ZFS zvols, or whole iSCSI LUNs. Only NFS is shared between nodes, so
  live migration without copying the disk, and HA, need NFS.
- `build_domain_xml` already takes block disks (`<disk type='block'>`) and its comment foresees "a Ceph RBD tomorrow".

## 2. What Ceph RBD brings

A Ceph pool is shared by every node, replicated (usually three copies) and without a single server to lose. QEMU
opens RBD images directly through librbd (`<disk type='network'><source protocol='rbd'>`), with no mount on the host.
Snapshots and clones of an image are native and instant.

## 3. Design

### 3.1 Connecting

- A new pool type **`rbd`**, a libvirt storage pool (`<pool type='rbd'>`) that lists and creates images:
  - monitors (host names or addresses, with ports), the Ceph pool name, a Ceph user (`client.hyperlite`);
  - the user's key goes into a libvirt **secret** (`<secret ephemeral='no' private='yes'>`, usage `ceph`), defined on
    every node, never stored by Hyperlite in clear text and never returned by the API.
- The admin creates the Ceph user with the minimum rights, documented:
  `ceph auth get-or-create client.hyperlite mon 'profile rbd' osd 'profile rbd pool=vms'`.
- A **Test** button checks that each node reaches the monitors and can list the pool (through libvirt, so it tests the
  exact path QEMU will use).
- Every node needs `librbd` in QEMU (`qemu-block-extra` on Debian), checked by the host capabilities and the preflight.

### 3.2 Disks

- VM creation, disk add and disk move accept an RBD pool. A disk is `<disk type='network' device='disk'>` with
  `<source protocol='rbd' name='vms/web-0'>`, the monitors as `<host>` elements, and `<auth username='hyperlite'>`
  pointing to the secret's UUID.
- Import of a cloud image or an uploaded disk: `qemu-img convert` straight into `rbd:vms/web-0` (qemu-img speaks RBD).
- Grow: `rbd resize` through libvirt's `volume.resize`, then `blockResize` live, as for other disks.
- Move a disk to or from RBD: `blockCopy` to a `network` destination, the same mirror-and-pivot as today.

### 3.3 What changes elsewhere

| Feature | With RBD |
|---|---|
| Live migration | No disk copy: RBD is shared, as NFS. `uses_shared_storage` learns the `rbd` type |
| HA | Allowed: RBD counts as shared storage. Ceph exclusive locks (`exclusive-lock` feature) add a guard against two writers, on top of the design's leases |
| Snapshots | Step 1 keeps libvirt's behaviour (external snapshots are not supported on RBD by libvirt for all operations): **snapshots of RBD disks are refused with a clear message** until step 3 uses native RBD snapshots |
| Backups | Step 1 refuses RBD disks like zvols today (`block_disk_error`); the backups design gains an RBD step (`rbd export`, and `rbd export-diff` for incrementals) |
| Clone / templates | `rbd clone` of a protected snapshot: instant clones (step 3) |

## 4. Risks

| Risk | Mitigation |
|---|---|
| Ceph unreachable from one node only | The Test runs on every node; the compatibility diagnostic blocks migration to a node that cannot reach the pool |
| Key leak | libvirt secret (private), minimal Ceph capabilities, never in Hyperlite's database |
| A snapshot or backup silently skipping RBD disks | Refused explicitly until supported (the same rule as zvols and LUNs today) |
| Ceph version or feature mismatches (old clients) | Minimum Ceph and librbd versions documented; the Test reports the cluster version |
| No Ceph to test on in CI | Unit tests on the XML and the flows; a real test on a small Ceph (three VMs with `cephadm`) before merging each step |

## 5. Steps (one pull request each)

1. **Connect and create disks**: `rbd` pool type with its secret on every node, Test, capability checks; create VMs and
   add disks on RBD; live migration and HA treat it as shared. Snapshots and backups of RBD disks refused clearly.
2. **Move and import**: disk move to and from RBD, cloud image and upload import into RBD, grow.
3. **Native RBD snapshots and clones**, then instant template clones.
4. **Backups of RBD disks** (full, then `export-diff` incrementals), in the backups design's order.

## 6. Questions for the maintainer

1. Is there a Ceph cluster to connect to, or one planned (and which version)? Proxmox's own Ceph can be used from
   Hyperlite as an external cluster.
2. If none exists yet: can three small machines or VMs host a test Ceph (with `cephadm`) for step 1?
3. Which pool name and Ceph user should the documentation show (for example `vms` and `client.hyperlite`)?
