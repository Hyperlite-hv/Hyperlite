# Design: the official Terraform / OpenTofu provider (#330)

Status: **accepted** (Antho, 2026-10-21, section 17), inspired by vSphere as the maintainer asked (section 4).

## 1. Why

A company that adopts a hypervisor describes its machines in code. vSphere has an official provider
(`hashicorp/vsphere`); Proxmox VE only has community ones (`bpg/proxmox`, `Telmate/proxmox`), which its users name
as a gap. API v1 was made stable for exactly this (`docs/design/api-v1.md`, "Why"): an official provider, released
with Hyperlite and tested against it, is the client that proves the contract.

## 2. Where we are (measured on `test`, 2026-10-21)

- **The contract**: 321 routes under `/api/v1`, described in `docs/api/openapi-v1.json`. Request bodies are typed
  there; most answers are not (`{}` schemas), so a client cannot be generated from it as it is.
- **Authentication**: personal API tokens (`hlt_` prefix, `app/core/api_tokens.py`), sent as `Authorization: Bearer`.
  A token has its user's rights; it may not create or revoke other tokens. Tokens of kind `api` expire when asked
  (`expires_days`, 90 by default, up to 3650).
- **The Go client** (`cli/`, module `github.com/Hyperlite-hv/Hyperlite/cli`): `call()` (JSON, `User-Agent`, bearer
  token, v1 errors, "server older than 1.0"), the TLS check (system trust store, then a pinned SHA-256 fingerprint),
  `normalizeURL`, `HYPERLITE_SERVER`/`HYPERLITE_TOKEN`. All of it is in `package main`: nothing can import it.
- **Long operations** answer in three ways (section 6): 23 routes answer `202 {"task_id": ...}`; others create a task
  but hold the request until the work is done (VM creation, clone, start, stop, deletion, disk upload); template
  deployment holds it with no task at all.
- **Identity**: a VM is known by its name on its node; it has a libvirt UUID (`uuid` in `GET /vms`) and can be renamed
  (`POST /vms/{name}/rename`). There is no numeric VM id yet (#517).
- **Cluster**: 14 routes of `vms/`, `network.py`, `storage.py` and `templates.py` take `?node=`; the others act on the
  node that answers (section 8). The task routes name the node `?on=`.
- **CI**: the e2e jobs already start a real backend on a GitHub runner with libvirt and KVM
  (`.github/workflows/ci.yml`, `e2e-shard`), and `lab.yml` a three-node lab of real VMs.

## 3. Goal

1. A provider `hyperlite` for Terraform and OpenTofu, published in both registries, signed, versioned on its own.
2. Resources for what a team describes in code: VMs (from a template or a cloud image, with cloud-init), their disks
   and interfaces, networks, storage pools, backup jobs, users, groups, roles and permissions; data sources to read
   what exists (templates first, as `vsphere_virtual_machine` is read to clone it).
3. Every change waits for the work to be done, or fails with the task's error: `apply` never ends on a half-made VM.
4. `terraform import` for everything that a resource manages.
5. Acceptance tests against a real Hyperlite in CI, on every pull request of the provider and every night against
   `test`.

What it does not do: create a cluster, join a node, update Hyperlite, install it (Ansible, #332, and the installer
do that). The node itself is an input, as `vsphere_host` is a data source, not something the provider builds.

## 4. Inspired by vSphere (Antho, 2026-10-21)

The maintainer's direction: **take inspiration from vSphere**, the reference for an official provider, without
copying it. What `hashicorp/vsphere`, its client library and vCenter do well is taken; the names, the objects and the
API stay Hyperlite's (`vcpu`, `memory_mb`, `node`, `size_gb`, as in API v1), so that the provider reads like the rest
of the product.

| Question | What vSphere does | What Hyperlite takes |
|---|---|---|
| Where the code lives | its own repository, `hashicorp/terraform-provider-vsphere`, versioned on its own | **`Hyperlite-hv/terraform-provider-hyperlite`** (both registries read a repository named `terraform-provider-<name>`), registry name **`hyperlite-hv/hyperlite`** |
| The client | `govmomi`, a Go library for the vSphere API, its types generated from the API's description; the CLI `govc` is built on it | **A Go library for API v1** in the CLI's module (`cli/hyperlite`): `Client`, the TLS check, the errors, the task wait, typed objects (generated from `openapi-v1.json` once its answers are typed, section 9; written by hand until then). `hyperlite-cli` is built on it |
| Framework | SDKv2 (the provider dates from 2017) | **Plugin Framework**, which HashiCorp asks new providers to use; nothing differs for a user |
| Signing | releases signed with the organisation's release key | **one release key of the organisation** for its providers, in a protected GitHub environment; not the APT key |
| Long operations | every call that takes time answers a `Task` at once; the provider waits for it and fails with its error | the same: every route that makes a task answers its id, the provider waits (section 6) |
| One address | the provider talks to vCenter, which sends the work to the right host | the provider talks to any member, which sends the work to the object's node (section 8) |
| Placement | a new VM goes in a resource pool; DRS picks the host unless one is named | `node` optional; absent, the cluster picks it (section 8) |
| Identity | a VM is its instance UUID; the name is an attribute | the same, with the libvirt UUID |
| Building a clone | a data source reads the template (system, firmware, disks) to size the clone; `clone { customize }` sets host name, addresses and resolvers | `data "hyperlite_template"` gives the same facts; a `customize` block written to the cloud-init drive |
| Power and changes | a VM is powered on after creation; a change that needs a power-off shuts the guest down, waits, forces it off if allowed, powers it on | the same rule, from what the server says needs a restart (`pending-changes`), with the delays as arguments |
| Waiting for the guest | the apply waits for the guest's network, so its address can feed an output | the same, from the guest agent or the DHCP lease |
| Rights | one privilege per step of a creation (create the VM, use the datastore, use the network) | the same steps as privileges (section 10) |

## 5. The provider

```hcl
terraform {
  required_providers {
    hyperlite = { source = "hyperlite-hv/hyperlite", version = "~> 1.0" }
  }
}

provider "hyperlite" {
  server      = "https://hyperlite.example.internal:8000" # HYPERLITE_SERVER
  api_token   = var.hyperlite_token                      # HYPERLITE_TOKEN, sensitive
  fingerprint = "AB12..."                                # optional: pinned certificate, as `hyperlite login` does
  # insecure  = false                                    # labs only; refused together with a fingerprint
}
```

- **Same variables as the CLI** (`HYPERLITE_SERVER`, `HYPERLITE_TOKEN`), and the same TLS rule: the system trust
  store, then the pinned fingerprint. `insecure` exists because labs ask for it (`vsphere`'s `allow_unverified_ssl`);
  it is logged as a warning.
- **Tokens, not a user and password**: where `vsphere` opens a session with a user and password, Hyperlite has tokens
  made for this (no second factor to answer, no session to renew, revocable on its own). The documentation shows how to
  create one with the least rights (section 10).
- **At configure**: `GET /health` (section 7), then `GET /auth/me`, so that a wrong token fails at once, with its
  message, before any plan.

### Resources and data sources

| Resource | Data source | API (all under `/api/v1`) |
|---|---|---|
| `hyperlite_vm` | `hyperlite_vm` | `/vms`, `/vms/{name}/…`, `/templates/{name}/deploy`, `/vms/{name}/clone` |
| `hyperlite_template` (from a VM) | `hyperlite_template` | `/templates`, `/templates/from-vm/{name}` |
| `hyperlite_vm_snapshot` | | `/vms/{name}/snapshots` |
| `hyperlite_vm_ha` | | `/ha/{vm}` |
| `hyperlite_vm_backup_schedule` | | `/vms/{name}/backup-schedule` |
| `hyperlite_backup_job` (grouped) | | `/backup-groups` |
| `hyperlite_network` | `hyperlite_network` | `/networks`, `/networks/{name}/…` |
| `hyperlite_network_reservation` | | `/networks/{name}/reservations` |
| `hyperlite_storage_pool` | `hyperlite_storage_pool` | `/storage` |
| `hyperlite_storage_volume` | | `/storage/{pool}/volumes` |
| `hyperlite_iso` (an upload) | `hyperlite_iso` | `/isos` |
| | `hyperlite_node` | `/nodes`, `/cluster` |
| `hyperlite_resource_pool` | `hyperlite_resource_pool` | `/pools` |
| `hyperlite_user`, `hyperlite_group`, `hyperlite_group_member` | `hyperlite_user`, `hyperlite_group` | `/auth/users`, `/groups` |
| `hyperlite_role` (custom role) | `hyperlite_role` | `/acl/custom-roles` |
| `hyperlite_acl` (every assignment on one object, as `vsphere_entity_permissions`) | | `/acl`, `/acl/object/{kind}/{name}` |

Tags and notes are attributes of the objects that have them (`/meta/{kind}/{name}`). After 1.0: containers,
replication and firewalls, then DRS rules when Hyperlite has them (#334).

### `hyperlite_vm`

```hcl
data "hyperlite_template" "debian" {
  name = "debian-12-base"
}

resource "hyperlite_vm" "web" {
  name          = "web-1"
  node          = "node2"           # optional: the cluster picks one (section 8)
  resource_pool = "web"             # optional for an administrator (section 10)
  vcpu          = 2
  memory_mb     = 4096

  clone {
    template = data.hyperlite_template.debian.name   # or `vm = "..."` to clone a VM

    customize {                     # written to the cloud-init drive, read by the guest at first boot
      hostname = "web-1"
      domain   = "example.internal"
      dns      = ["10.0.0.53"]
      gateway  = "10.0.20.1"
    }
  }

  disk {                            # in order: the first is the system disk
    size_gb      = data.hyperlite_template.debian.disks[0].size_gb   # grow only
    storage_pool = "zfs-fast"
  }

  network_interface {
    network  = "lan"
    vlan_tag = 20
    ipv4     = "10.0.20.11/24"      # static, through customize; absent: DHCP
  }

  cloud_init {
    user     = "debian"
    ssh_keys = [file("~/.ssh/id_ed25519.pub")]
    password = var.vm_password      # sensitive, write-only (never returned by the API)
  }

  tags = ["web", "prod"]

  shutdown_timeout = "3m"           # a clean shutdown before a change that needs one, then...
  force_stop       = true           # ...a hard stop when it does not end
  wait_for_ip      = "5m"           # "0s" not to wait
}
```

- **Identity**: the Terraform id is the VM's **UUID**, as in `vsphere`; `name` is an attribute. A rename made in the
  dashboard is a change of `name`, put back by the next `apply` (`POST /rename`), not a VM gone and a new one. With
  #517, the VMID becomes an attribute too.
- **Read**: the VM is found by UUID in `GET /vms?cluster=true` (one call, from the cluster's status), then read in one
  call on its node (section 9). A VM migrated elsewhere is found there; `node` then shows a change, put back by a
  migration only when it is set.
- **Create**: the clone (template deployment or VM clone), or a new VM from the cloud image or an ISO (`iso`,
  `import_disk`, as `POST /vms`); then the settings the creation does not take; then **started**, as `vsphere` powers
  a VM on. A failure after the VM exists marks it *tainted*: the next `apply` deletes and recreates it.
- **Customisation**: `vsphere` writes `customize` into the guest through VMware Tools; Hyperlite writes it into the
  VM's cloud-init drive, which a cloud image reads at first boot (Proxmox does the same). A Windows guest needs
  cloudbase-init in its image; without a cloud-init drive (a VM installed from an ISO), `customize` is refused at plan.
- **Changes that need a restart**: read from the server (`GET /vms/{name}/pending-changes`), not guessed. The provider
  shuts the guest down, waits `shutdown_timeout`, stops it hard if `force_stop`, applies, starts it, as `vsphere`
  does. **Replace**: the firmware, the clone source, a smaller disk.
- **Delete**: the same shutdown, then `DELETE /vms/{name}?confirm=true`.
- **Waiting for the guest**: `wait_for_ip` waits for an address (guest agent or DHCP lease, `ip` of the VM), exposed as
  `ip_address` for outputs and provisioners.
- **Moving**: a new `node` migrates the VM (`POST /migrate`); a disk's new `storage_pool` moves the disk
  (`/disks/{dev}/move`), as vMotion and Storage vMotion do in `vsphere`.

## 6. Long operations

What the API does today:

- **`202 {"task_id"}`** (23 routes): snapshots and their restore, disk move, migration, backup, backup restore and
  verification, ISO copy, export, the runs of jobs, a node's maintenance, updates.
- **Held until done, with a task alongside** (the answer is the result, the `task_id` is not returned): VM creation,
  clone, start, stop, restart and deletion, the disk upload, a container's creation and clone.
- **Held until done, no task at all**: template deployment and template creation (each a full copy of the disk).

A held request is a problem for any client, not only Terraform: a clone of a 200 GB disk outlives every proxy and load
balancer timeout (the CLI's client gives up at 30 s), and a request cut that way leaves a task that runs on with nobody
to read its end. Proxmox answers every such call with its task id at once (UPID, `POST /nodes/{node}/qemu/{vmid}/clone`
→ `UPID:...`) and `vsphere` returns a `Task` object for every `*_Task` method; both providers then poll it
(`bpg/proxmox`: `WaitForTask`; `vsphere`: `task.WaitForResult`).

**Common rule for API v1** (decided, section 17): every route that makes a task answers `202 {"task_id", "status": "running", ...}`
at once, plus what the caller needs to find the result (`name`, `node`). This is not an additive change for a route
that answers `201` today (the dashboard and the e2e tests read the result from the answer): it is done **per route
with an opt-in** `?wait=false` first (additive, v1 allows it), which the provider always sends; the default changes in
v2. Section 9 lists the routes.

The provider waits with one function (`cli/hyperlite`, `WaitTask`): `GET /tasks/{id}` every second, then every 5 s
after a minute, until `succeeded`, `failed` or `cancelled`; `failed` turns `error` and the last lines of
`GET /tasks/{id}/log` into the Terraform diagnostic. A timeout cancels the task (`POST /tasks/{id}/cancel`) when it
is `cancellable`, and says so. A task of another node is followed with `?on=` (section 8).

## 7. API version

`GET /health` answers `api` (1) to everyone and `hyperlite_version` to a signed-in caller.

- `api` other than 1: refused at configure ("this provider speaks API v1; this server speaks API n").
- `hyperlite_version` lower than the version where a resource's routes appeared: that resource fails at plan with
  the version it needs ("`hyperlite_backup_job` needs Hyperlite 1.2 or later; this server runs 1.1.1"), never with a
  404 in the middle of an apply. Each resource declares its minimum version in one table of the provider.
- A cluster during a rolling update can have members on two versions: the check uses the node that a resource
  targets (`GET /nodes/{name}/summary`, which gives the version, section 9).

The provider's version is its own (`1.0.0` for the first release), and its documentation has a table "provider
version → oldest Hyperlite version", as `vsphere`'s "vSphere 7.0 or later".

## 8. Clusters: the `node` argument

The rule today is not one: `GET /vms/{name}`, `DELETE`, start, rename and the console take `?node=`; create, clone,
stop, restart, the update (`PATCH`), disks, interfaces, cloud-init, snapshots and templates act only on the node that
answers. And `node` means two things: forwarded to that member's own API (`cluster_view.forward_to`, the list and the
backups), or reached from here through libvirt over SSH (`open_conn(node)`, the others). The tasks call it `?on=`.

Proxmox has one rule: everything about a guest is under `/nodes/{node}/…`, and `pveproxy` passes a request for another
node to that node's `pveproxy`. A client talks to any node; the node that owns the guest does the work, with its own
locks and tasks.

**Decided**, as one change for the API (not the provider alone):

- Every route on a VM, a template, a network, a storage pool and their sub-routes takes `?node=`, through
  `cluster_view.forward_to("node")`: the member does the work itself, its task id comes back (and is followed with
  `?on=<node>`). Absent, `local` or this node: here, as today. `stop`, `restart` and `PATCH` lose the asymmetry with
  `start` and `DELETE`.
- `open_conn(node)` stays for nodes registered over SSH only (not members, no Hyperlite on them).
- `?on=` of the tasks is also accepted as `?node=` (additive), so a client uses one name.

vSphere does the same with one address: the provider talks to vCenter, vCenter sends the work to the host. Hyperlite
has no vCenter; any member plays its part.

**Placement.** vSphere puts a new VM in a resource pool and lets DRS pick the host unless `host_system_id` is given.
Hyperlite, when `node` is absent: `POST /vms?node=auto` (additive) picks among the members that are online, out of
maintenance and see the storage pool and the network, the one with the most free memory; DRS (#334) replaces this rule
when it lands. The node picked is read back into the state.

In the provider, `node` is optional on each resource that lives on a node (VM, template, network, local pool).
Changing it on a VM is a **migration** (`POST /vms/{name}/migrate`), as `vsphere` moves a VM when `host_system_id`
changes; on a network or a local pool it is a replacement. A shared pool (NFS, iSCSI on several nodes) takes
`nodes`.

## 9. What the API needs (resource by resource)

| Resource | Routes today (`app/routers/`) | Missing for the provider | Kind |
|---|---|---|---|
| all | `GET /health` (`app/main.py`) | The version of each member: `hyperlite_version` in `GET /nodes/{name}/summary` | additive |
| all | errors `{"detail", "code"}` | Nothing: the provider tests `code` (`not_found` → gone from the state, `changed_elsewhere`, `read_only`, `locked` → retried) | |
| all | `?node=` on 14 routes, `?on=` on tasks | `?node=` on every route of section 8, forwarded to the member; `?node=auto` on creation and clone (placement) | additive |
| all | answers mostly untyped in `openapi-v1.json` | `response_model` on the routes the provider reads, so the document describes them and the contract check covers them | additive |
| `hyperlite_vm` | `POST /vms` (`vms/create.py`), held until done | `?wait=false` → `202 {"task_id", "name"}`; `ssh_keys` at creation (today only at the next boot, `PUT /cloud-init`); `vlan_tag` of the first interface | additive |
| `hyperlite_vm` (clone) | `POST /vms/{name}/clone` (`vms/clone.py`), held; `POST /templates/{name}/deploy` (`templates.py`), held, no task | `?wait=false` and a task for both; `storage_pool` of the copy (today the source's pool) | additive |
| `hyperlite_vm` (customisation) | cloud-init at creation: `user`, `password`; hostname = VM name (`core/vm_builder.py`) | what `customize` holds (inspired by `vsphere`'s): a host name and domain other than the VM's name, static addresses per interface, a gateway, resolvers; and a user-data snippet appended to Hyperlite's own (`user_data`) | additive |
| `hyperlite_vm` | `GET /vms/{name}`, `/disks`, `/network`, `/boot`, `/cloud-init`, `GET /meta/vm/{name}` | one read with all of them (`GET /vms/{name}?full=true`): six calls per VM per plan is slow at 100 VMs | additive |
| `hyperlite_vm` | `PATCH /vms/{name}` (`vcpu`, `memory_mb`, `os_label`), local only | `?node=`; nothing else | additive |
| `hyperlite_vm` | `POST /vms/{name}/start`, `/stop`, held, local for stop | `?wait=false`; `stop` takes `force` and a `timeout_s` of the clean shutdown (the provider's `shutdown_timeout`, `force_stop`) | additive |
| `hyperlite_vm` | `POST /vms/{name}/migrate` (`vms/migration.py`), 202 | nothing | |
| `hyperlite_vm` | `POST /vms` requires `admin`; clone requires `vm.clone` | the privileges of section 10, so that a token for Terraform is not an administrator's | additive |
| `hyperlite_vm` (disks) | `POST /vms/{name}/disks` (`DiskAttach`: an existing volume), `/resize`, `DELETE` (`vms/devices.py`) | create and attach in one call (`size_gb`, `storage_pool`), as the creation form does | additive |
| `hyperlite_template` (data) | `GET /templates` (`name`, `vcpu`, `memory_mb`, `vm_source`) | what a clone is sized from, as `vsphere`'s data source gives: the system, `firmware`, `disks` (`size_gb`, bus, pool), the interfaces | additive |
| `hyperlite_template` | `POST /templates/from-vm/{name}` (`keep_vm`), held, no task | `?wait=false` and a task | additive |
| `hyperlite_network` | `POST`, `GET`, `PATCH`, `DELETE /networks/{name}`, `/rename`, `/autostart`, `/firewall` (`network.py`) | nothing for the first version (a `PATCH` that needs a restart says it: `needs_restart`) | |
| `hyperlite_network_reservation` | `POST`, `DELETE /networks/{name}/reservations/{mac}`, read in `GET /networks/{name}` (`ipam`) | nothing | |
| `hyperlite_storage_pool` | `POST /storage`, `DELETE`, `/rename` (`storage.py`) | `GET /storage/{name}` (today only the list); no change after creation except the name, so every other attribute replaces | additive |
| `hyperlite_storage_volume` | `GET`, `POST`, `DELETE /storage/{pool}/volumes` | `GET /storage/{pool}/volumes/{name}` | additive |
| `hyperlite_vm_backup_schedule` | `GET`, `PUT`, `DELETE /vms/{name}/backup-schedule` (`backups.py`) | nothing | |
| `hyperlite_backup_job` | `GET`, `POST /backup-groups`, `PUT`, `DELETE /backup-groups/{id}` (`backup_groups.py`) | `GET /backup-groups/{id}` | additive |
| `hyperlite_vm_snapshot` | `GET`, `POST`, `DELETE /vms/{name}/snapshots` (`vms/snapshots.py`), 202 | nothing | |
| `hyperlite_vm_ha` | `POST /ha/{vm}/enable`, `DELETE /ha/{vm}`, `GET /ha` (`ha.py`) | `GET /ha/{vm}` | additive |
| `hyperlite_iso` | `POST /isos` (upload), `DELETE /isos/{filename}` (`isos.py`) | `?node=` (an image is per node) | additive |
| `hyperlite_resource_pool` | `GET`, `POST /pools`, `PATCH` (name), `DELETE`, members (`pools.py`) | `description` in `PATCH` | additive |
| `hyperlite_user` | `GET`, `POST /auth/users`, `PATCH` (`role`, `password`), `DELETE` (`auth.py`) | `GET /auth/users/{username}`; `enabled`, and whether the account must set its password at first sign-in | additive |
| `hyperlite_group` | `GET`, `POST /groups`, `PATCH` (name), `DELETE`, members (`groups.py`) | nothing | |
| `hyperlite_role` | `GET`, `POST /acl/custom-roles`, `PATCH` (name only), `DELETE` (`acl.py`) | `privileges` in `PATCH` (today a change means delete and recreate, which drops its assignments) | additive |
| `hyperlite_acl` | `GET`, `POST /acl`, `DELETE /acl/{id}`, `GET /acl/object/{type}/{id}` | nothing: the resource is every assignment of one object, read with `/acl/object`, an idea of `vsphere_entity_permissions` | |

Every row is additive: none needs a v2. Each comes as its own pull request into `test`, with its tests, before the
provider resource that uses it; the provider's first release needs the rows of `all`, `hyperlite_vm`, `hyperlite_vm`
(clone), `hyperlite_template` (data) and the `GET` of one object.

## 10. Rights

The `vsphere` documentation lists the privileges a provider's account needs, and vSphere has one for each step of a
VM's creation (create the VM in a folder and resource pool, use space on a datastore, connect a network). A pipeline
is given exactly those.

Hyperlite today: creating a VM needs the global `admin` role, and no privilege says on which storage pool or network a
user may put a VM. The same idea, as additive privileges (`app/core/permissions.py`), close to Proxmox's
`VM.Allocate`, `Datastore.AllocateSpace` and `SDN.Use`:

| Privilege | Allows | Checked on |
|---|---|---|
| `vm.allocate` | creating and deleting VMs | the resource pool the VM goes in (`resource_pool`) |
| `storage.allocate` | putting a disk on a storage pool | each storage pool a disk goes on |
| `network.assign` | connecting an interface to a network | each network an interface joins |

The provider's documentation then gives a role "Terraform" (`vm.allocate`, `vm.hardware`, `vm.power`, `vm.resize`,
`vm.options`, `vm.snapshot`, `vm.clone`) assigned on a resource pool, with `storage.allocate` and `network.assign` on
what it may use: the pipeline creates VMs in its pool, on its storage and networks, only. A user without `admin` gives
`resource_pool`. Networks, storage pools, users and roles stay administrator resources.

## 11. Compared with `hashicorp/vsphere`, point by point

| Point | `hashicorp/vsphere` | Hyperlite provider |
|---|---|---|
| Authentication | user and password (a vCenter session), `allow_unverified_ssl` | an API token; the CLI's TLS rule with a pinned fingerprint; `insecure` for labs |
| Identity of a VM | the instance UUID; the name and folder are attributes | the libvirt UUID; the name is an attribute; the VMID when #517 lands |
| Where a VM runs | `resource_pool_id` (required), `host_system_id` (else DRS), `datastore_id` | `resource_pool` (required but for an administrator), `node` (else the placement rule, then DRS), `storage_pool` per disk |
| Clone | `clone { template_uuid, linked_clone, timeout }` | `clone { template \| vm }`; linked clones when #423 lands |
| Reading a template | data source `vsphere_virtual_machine`: `guest_id`, `firmware`, `scsi_type`, `disks[].size`, `network_interface_types` | data source `hyperlite_template`: `os`, `firmware`, `disks[].size_gb`, `vcpu`, `memory_mb`, `ssh_user` (section 9) |
| Customisation | `clone.customize { linux_options, network_interface, ipv4_gateway, dns_server_list, windows_options }` through VMware Tools | `clone.customize { hostname, domain, gateway, dns }` and `network_interface.ipv4`, through the cloud-init drive |
| cloud-init | `extra_config` (`guestinfo.userdata`) or `vapp.properties` | a `cloud_init` block (`user`, `ssh_keys`, `password`, `user_data`) |
| Disks | `disk { label, size, unit_number, thin_provisioned, datastore_id }`; grow only | `disk { size_gb, storage_pool, bus, cache, discard }` in order; grow only |
| Interfaces | `network_interface { network_id, adapter_type, mac_address }`; the VLAN is the port group's | `network_interface { network, vlan_tag, mac, ipv4 }` |
| Power | on after creation | started after creation |
| Changes needing a power-off | shutdown, `shutdown_wait_timeout`, `force_power_off`, power on | the same rule (`shutdown_timeout`, `force_stop`), what needs it read from the server |
| Long operations | each call is a `Task`, waited with `task.WaitForResult` | each call answers a `task_id` (section 6), waited with `WaitTask`, cancelled on timeout |
| Waiting for the guest | `wait_for_guest_net_timeout`, `wait_for_guest_ip_timeout` | `wait_for_ip` |
| Import | `terraform import vsphere_virtual_machine.vm /dc/vm/folder/name` | `terraform import hyperlite_vm.web node2/web-1` (or the UUID) |
| Moving a VM | a new `host_system_id` (vMotion) or `datastore_id` (Storage vMotion) | a new `node` (migration) or `storage_pool` (disk move) |
| Permissions | `vsphere_role`, `vsphere_entity_permissions`, one privilege per creation step | `hyperlite_role`, `hyperlite_acl` (all of one object's assignments), the privileges of section 10 |
| HA | `vsphere_ha_vm_override` | `hyperlite_vm_ha` |
| Backups | none (other products) | `hyperlite_vm_backup_schedule`, `hyperlite_backup_job` |

And from Proxmox's community providers, what to keep: `bpg/proxmox` waits for every UPID and fails with the task's
log; `Telmate/proxmox`, which did not at first, had VMs half-made at the end of an `apply` for years.

## 12. `terraform import`

Each resource takes the id a person can see in the dashboard:

| Resource | Import id |
|---|---|
| `hyperlite_vm` | `<node>/<name>`, `<name>` (the node that answers), or the UUID |
| `hyperlite_network`, `hyperlite_storage_pool` | `<node>/<name>` or `<name>` |
| `hyperlite_storage_volume` | `<node>/<pool>/<volume>` |
| `hyperlite_vm_snapshot` | `<node>/<vm>/<snapshot>` |
| `hyperlite_user`, `hyperlite_group` | the name |
| `hyperlite_acl` | `<kind>/<object>` (`vm/web-1`, `pool/web`) |
| `hyperlite_role`, `hyperlite_backup_job`, `hyperlite_resource_pool` | the id shown in the dashboard |

OpenTofu and Terraform 1.5+ `import` blocks work the same. The provider also implements `hyperlite_vm`'s import from
an id so that `terraform plan -generate-config-out` writes the configuration of an existing VM.

## 13. Tests

- **Unit**: the schema, the plan modifiers, the import id parsing, the task wait against a fake server (`httptest`).
- **Acceptance** (`TF_ACC=1`, `terraform-plugin-testing`): each resource created, read, changed in place, imported,
  replaced and destroyed, with `CheckDestroy` asking the API that it is gone. Against a real Hyperlite on a GitHub
  runner, set up as the e2e jobs do (libvirt, KVM, the backend of the checkout); VMs from a small cloud image, names
  prefixed `tfacc-`.
- **Matrix**: Terraform (oldest supported and latest) and OpenTofu (latest); Hyperlite `test` every night and the
  latest stable release on each pull request of the provider.
- **Cluster**: the three-node lab of `lab.yml` for `node`, the migration and a member that is down, nightly.
- **Manual**, before the first release: a real two-node cluster in the lab, never production.

## 14. Publication

- **GoReleaser** builds the archives (`linux`, `darwin`, `windows`, `freebsd`; `amd64`, `arm64`), the `SHA256SUMS`
  signed with the organisation's release key (section 4), and `terraform-registry-manifest.json` (protocol 6.0), from a tag `v1.0.0` of the provider's
  repository, in a protected GitHub environment holding the key, as `publish.yml` holds the APT key (a different key).
- **Terraform registry**: the organisation signs in to registry.terraform.io with GitHub, adds the public GPG key and
  the repository; each release is read from GitHub.
- **OpenTofu registry**: a pull request to `opentofu/registry` adds the provider and the same public key.
- **Documentation**: `docs/` of the provider repository in the registry's format, generated with `tfplugindocs`
  from the schema and `examples/`.
- **Versioning**: semantic, its own; a breaking change of the provider's schema is a major version. The provider is
  released when a Hyperlite release adds what it uses, never before.

## 15. Steps (each a pull request, its tests and a check on a real Hyperlite)

| Step | Repository | Content |
|---|---|---|
| 1 | Hyperlite | `cli/hyperlite`: the client as a package (`Client`, `Call`, TLS, errors, `WaitTask`); `cli` uses it; module tags `cli/v…` |
| 2 | Hyperlite | API, section 9 row "all": `?node=` forwarded on every route, `?node=` for the tasks, versions in the node summary |
| 3 | Hyperlite | API: `?wait=false` and tasks for create, clone, deploy, start, stop; placement (`?node=auto`); the customisation at creation (host name, addresses, resolvers, `ssh_keys`, `user_data`); `GET` of one object where missing; `response_model` on what the provider reads |
| 4 | provider | Skeleton: provider block, `/health` check, data sources `hyperlite_node(s)`, `hyperlite_template`, `hyperlite_network`; GoReleaser, signed pre-release in a private test |
| 5 | provider | `hyperlite_vm` (create, clone, customisation, cloud-init, disks, interfaces, power-off rule, guest wait, import), the acceptance job |
| 6 | provider | networks, storage pools and volumes, ISO images, snapshots, HA, backup schedules and jobs |
| 7 | Hyperlite + provider | the privileges of section 10; `hyperlite_user`, `hyperlite_group`, `hyperlite_role`, `hyperlite_acl`, `hyperlite_resource_pool` |
| 8 | provider | `v1.0.0` in both registries, with the documentation and a guide "from vSphere" |

## 16. Risks

- **Two repositories to keep in step**: the nightly job runs the provider's acceptance tests against `test`, so a
  change of the API that breaks it is seen the next morning, before a release.
- **Rate of reads**: a plan reads every VM; one call per VM (section 9, `?full=true`) and the cluster list from the
  cluster's status keep 100 VMs under a few seconds. Measured at step 5 on the lab.
- **Write-only values** (`password`, cloud-init password, CHAP password): never returned, so a change made elsewhere is
  not seen; documented, as every provider does, and marked `sensitive`.
- **Names as keys**: a VM renamed elsewhere and another created with its old name: the UUID keeps the provider on the
  right one.

## 17. Decisions (Antho, 2026-10-21)

1. **Inspired by vSphere**, not a copy of it: section 4.
2. **The privileges of section 10**, all three: `vm.allocate`, `storage.allocate`, `network.assign`.
3. **Placement**: without a node, the cluster picks the member with the most free memory among those that can host the
   VM (section 8), until DRS (#334).
4. **Long operations**: `?wait=false` in v1, answering `202 {"task_id"}`; the default changes in v2 (section 6).
5. **The document is accepted**: step 1 (the client library) starts.
