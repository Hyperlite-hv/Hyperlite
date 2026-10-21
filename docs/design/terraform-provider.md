# Design: the official Terraform / OpenTofu provider (#330)

Status: **proposed**, waiting for the maintainer's decisions (section 17). No code before they are taken.

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
- **Long operations** answer in two ways (section 6): 23 routes answer `202 {"task_id": ...}`; others create a task
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

## 4. Options considered

| Question | Options | Proposed |
|---|---|---|
| Framework | SDKv2 (`terraform-plugin-sdk`); **Plugin Framework** (`terraform-plugin-framework`) | **Framework**: HashiCorp's recommended one for new providers, protocol 6, typed plan modifiers, nested attributes; `hashicorp/vsphere` is still SDKv2 for history only |
| Where the code lives | In this repository (`terraform/`); **its own repository** `Hyperlite-hv/terraform-provider-hyperlite` | **Own repository**: both registries read a GitHub repository named `terraform-provider-<name>` and its releases; its versions are not Hyperlite's |
| The HTTP client | Generated from `openapi-v1.json` (`oapi-codegen`); **the CLI's code moved into a package** | **A package**: `cli/hyperlite` (exported: `Client`, `Call`, the TLS check, the errors, the task wait), imported by `cli` and by the provider. Generation waits for typed answers in the OpenAPI document (section 9, row "all") |
| Name | `hyperlite-hv/hyperlite`; `hyperlite/hyperlite` | **`hyperlite-hv/hyperlite`**: the GitHub organisation is the registry namespace; nothing to register elsewhere |

## 5. The provider

```hcl
terraform {
  required_providers {
    hyperlite = { source = "hyperlite-hv/hyperlite", version = "~> 1.0" }
  }
}

provider "hyperlite" {
  endpoint    = "https://hyperlite.example.internal:8000" # HYPERLITE_SERVER
  api_token   = var.hyperlite_token                      # HYPERLITE_TOKEN, sensitive
  fingerprint = "AB12..."                                # optional: pinned certificate, as `hyperlite login` does
  # insecure = false                                     # refused with a fingerprint; only for a lab
  # task_timeout = "30m"                                 # default wait for a task (each resource has its own timeouts)
}
```

- **Same variables as the CLI** (`HYPERLITE_SERVER`, `HYPERLITE_TOKEN`), and the same TLS rule: the system trust
  store, then the pinned fingerprint. `insecure` exists because `vsphere` has `allow_unverified_ssl` and labs ask for it;
  it is refused together with `fingerprint` and logged as a warning.
- **Tokens only**, no user and password: no second factor to answer, no session to renew, revocable on its own. The
  documentation shows how to create one with the least rights (a custom role, section 10).
- **At configure**: `GET /health` (section 7), then `GET /auth/me`, so that a wrong token fails at once, with its
  message, before any plan.

### Resources and data sources

| Resource | Data source | API (all under `/api/v1`) |
|---|---|---|
| `hyperlite_vm` | `hyperlite_vm` | `/vms`, `/vms/{name}/…`, `/templates/{name}/deploy`, `/vms/{name}/clone` |
| `hyperlite_vm_disk` (a disk attached outside `hyperlite_vm`) | | `/vms/{name}/disks`, `/storage/{pool}/volumes` |
| | `hyperlite_template` | `/templates` |
| `hyperlite_template` (from a VM) | | `/templates/from-vm/{name}` |
| `hyperlite_network` | `hyperlite_network` | `/networks`, `/networks/{name}/…` |
| `hyperlite_network_reservation` | | `/networks/{name}/reservations` |
| `hyperlite_storage_pool` | `hyperlite_storage_pool` | `/storage` |
| `hyperlite_storage_volume` | | `/storage/{pool}/volumes` |
| `hyperlite_vm_backup_schedule` | | `/vms/{name}/backup-schedule` |
| `hyperlite_backup_job` (grouped) | | `/backup-groups` |
| `hyperlite_vm_snapshot` | | `/vms/{name}/snapshots` |
| `hyperlite_resource_pool` and `hyperlite_resource_pool_member` | `hyperlite_resource_pool` | `/pools` |
| `hyperlite_user` | `hyperlite_user` | `/auth/users` |
| `hyperlite_group` and `hyperlite_group_member` | `hyperlite_group` | `/groups` |
| `hyperlite_role` (custom role) | `hyperlite_role` | `/acl/custom-roles` |
| `hyperlite_acl` (an assignment) | | `/acl` |
| | `hyperlite_node`, `hyperlite_nodes` | `/nodes`, `/cluster` |
| | `hyperlite_iso` | `/isos`, `/isos/cluster` |

Containers (`/containers`), HA protection (`/ha`), replication and firewalls come in a second version: the same
patterns, once the VM resource has run in real use.

### `hyperlite_vm`

```hcl
data "hyperlite_template" "debian" {
  name = "debian-12-base"
  node = "node2"
}

resource "hyperlite_vm" "web" {
  name      = "web-1"
  node      = "node2"            # optional: the node that answers when absent
  vcpu      = 2
  memory_mb = 4096

  clone {
    template = data.hyperlite_template.debian.name   # or `vm = "..."` to clone a VM
  }

  disk {                          # in order: the first is the system disk
    size_gb      = 40             # grown when larger than the template's; shrinking is refused at plan
    storage_pool = "zfs-fast"
  }

  network_interface {
    network  = "lan"
    vlan_tag = 20
  }

  cloud_init {
    user     = "debian"
    ssh_keys = [file("~/.ssh/id_ed25519.pub")]
    password = var.vm_password   # sensitive, write-only (never returned by the API)
  }

  autostart  = true
  tags       = ["web", "prod"]
  power_state = "running"        # running | stopped

  timeouts { create = "20m" }
}
```

- **Identity**: the Terraform id is the VM's **UUID**; `name` is an attribute. A rename made in the dashboard is seen
  as a change of `name` and put back by the next `apply` (`POST /rename`), not as a VM gone and a new one created: what
  `vsphere` does with the instance UUID. With #517, the VMID becomes an attribute too.
- **Read**: the VM is found by UUID in `GET /vms?cluster=true` (one call, from the cluster's status); then its details
  on its node (`/vms/{name}`, `/disks`, `/network`, `/boot`, `/cloud-init`, `/meta`). A VM migrated to another node is
  found there: `node` then shows a change, put back by a migration only if `node` is set (section 8).
- **Create**: `clone` (template deployment or VM clone) or a new VM from the cloud image or an ISO (`iso`,
  `import_disk`, as `POST /vms`); then the settings the creation does not take, each with its own route (disks,
  interfaces, cloud-init, boot, tags), then start. A failure after the VM exists marks the resource *tainted*, as every
  provider does: the next `apply` deletes and recreates it.
- **Update in place**: CPU, memory (needs a stop when the VM is running and the change is not live: the provider
  says so at plan with `pending-changes` and only stops it with `allow_restart = true`, `vsphere`'s
  `reboot_required` rule), disks added or grown, interfaces, cloud-init, boot, tags and notes. **Replace**: the
  firmware, the source of a clone, a smaller disk.
- **Delete**: stop (`force_stop_after`, default 5 min of a clean shutdown, then a hard stop), then
  `DELETE /vms/{name}?confirm=true`.
- **Power**: `power_state`, as `bpg/proxmox`'s `started`; `vsphere` always powers on.
- **Wait for the guest**: `wait_for_ip = true` waits for an address (guest agent or DHCP lease, `ip` in the VM's
  summary), so that an output or a provisioner can use it; `vsphere`'s `wait_for_guest_net_timeout`.

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

**Common rule, proposed for API v1**: every route that makes a task answers `202 {"task_id", "status": "running", ...}`
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

**Proposed**, as one change for the API (not the provider alone):

- Every route on a VM, a template, a network, a storage pool and their sub-routes takes `?node=`, through
  `cluster_view.forward_to("node")`: the member does the work itself, its task id comes back (and is followed with
  `?on=<node>`). Absent, `local` or this node: here, as today. `stop`, `restart` and `PATCH` lose the asymmetry with
  `start` and `DELETE`.
- `open_conn(node)` stays for nodes registered over SSH only (not members, no Hyperlite on them).
- `?on=` of the tasks is also accepted as `?node=` (additive), so a client uses one name.

In the provider, `node` is optional on each resource that lives on a node (VM, template, network, local pool); absent,
it is the node that answers, read back into the state. Changing it on a VM is a **migration**
(`POST /vms/{name}/migrate`), not a replacement, as `vsphere` moves a VM when `host_system_id` changes;
on a network or a local pool it is a replacement. A shared pool (NFS, iSCSI on several nodes) takes `nodes`.

## 9. What the API needs (resource by resource)

| Resource | Routes today (`app/routers/`) | Missing for the provider | Kind |
|---|---|---|---|
| all | `GET /health` (`app/main.py`) | The version of each member: `hyperlite_version` in `GET /nodes/{name}/summary` | additive |
| all | errors `{"detail", "code"}` | Nothing: the provider tests `code` (`not_found` → gone from the state, `changed_elsewhere`, `read_only`, `locked` → retried) | |
| all | `?node=` on 14 routes, `?on=` on tasks | `?node=` on every route of section 8, forwarded to the member | additive |
| all | answers mostly untyped in `openapi-v1.json` | `response_model` on the routes the provider reads, so the document describes them and the contract check covers them | additive |
| `hyperlite_vm` | `POST /vms` (`vms/create.py`), held until done | `?wait=false` → `202 {"task_id", "name"}`; `ssh_keys` at creation (today only at the next boot, `PUT /cloud-init`); `vlan_tag` of the first interface | additive |
| `hyperlite_vm` (clone) | `POST /vms/{name}/clone` (`vms/clone.py`), held; `POST /templates/{name}/deploy` (`templates.py`), held, no task | `?wait=false` and a task for both; `storage_pool` of the copy (today the source's pool) | additive |
| `hyperlite_vm` (customisation) | cloud-init at creation: `user`, `password`; hostname = VM name (`core/vm_builder.py`) | a static address and DNS (`ip_config`: `address`, `gateway`, `dns`), a hostname other than the name, and a user-data snippet appended to Hyperlite's own (`user_data`), the three things `vsphere`'s `customize` and `bpg/proxmox`'s `initialization` hold | additive |
| `hyperlite_vm` | `GET /vms/{name}`, `/disks`, `/network`, `/boot`, `/cloud-init`, `GET /meta/vm/{name}` | one read with all of them (`GET /vms/{name}?full=true`): six calls per VM per plan is slow at 100 VMs | additive |
| `hyperlite_vm` | `PATCH /vms/{name}` (`vcpu`, `memory_mb`, `os_label`), local only | `?node=`; nothing else | additive |
| `hyperlite_vm` | `POST /vms/{name}/start`, `/stop`, held, local for stop | `?wait=false`; `stop` takes `force` and a `timeout_s` of the clean shutdown | additive |
| `hyperlite_vm` | `POST /vms/{name}/migrate` (`vms/migration.py`), 202 | nothing | |
| `hyperlite_vm` | `POST /vms` requires `admin`; clone requires `vm.clone` | a privilege `vm.allocate` (create and delete VMs) and `storage.allocate` on a storage pool, so that a token for Terraform is not an administrator's (Proxmox's `VM.Allocate`, `Datastore.AllocateSpace`) | additive |
| `hyperlite_vm_disk` | `POST /vms/{name}/disks` (`DiskAttach`: an existing volume), `/resize`, `DELETE` (`vms/devices.py`) | create and attach in one call (`size_gb`, `storage_pool`), as the creation form does | additive |
| `hyperlite_template` (data) | `GET /templates` (`name`, `vcpu`, `memory_mb`, `vm_source`) | the template's disks (size, pool), firmware and system, to size a clone from it as `vsphere`'s data source does | additive |
| `hyperlite_template` | `POST /templates/from-vm/{name}` (`keep_vm`), held, no task | `?wait=false` and a task | additive |
| `hyperlite_network` | `POST`, `GET`, `PATCH`, `DELETE /networks/{name}`, `/rename`, `/autostart`, `/firewall` (`network.py`) | nothing for the first version (a `PATCH` that needs a restart says it: `needs_restart`) | |
| `hyperlite_network_reservation` | `POST`, `DELETE /networks/{name}/reservations/{mac}`, read in `GET /networks/{name}` (`ipam`) | nothing | |
| `hyperlite_storage_pool` | `POST /storage`, `DELETE`, `/rename` (`storage.py`) | `GET /storage/{name}` (today only the list); no change after creation except the name, so every other attribute replaces | additive |
| `hyperlite_storage_volume` | `GET`, `POST`, `DELETE /storage/{pool}/volumes` | `GET /storage/{pool}/volumes/{name}` | additive |
| `hyperlite_vm_backup_schedule` | `GET`, `PUT`, `DELETE /vms/{name}/backup-schedule` (`backups.py`) | nothing | |
| `hyperlite_backup_job` | `GET`, `POST /backup-groups`, `PUT`, `DELETE /backup-groups/{id}` (`backup_groups.py`) | `GET /backup-groups/{id}` | additive |
| `hyperlite_vm_snapshot` | `GET`, `POST`, `DELETE /vms/{name}/snapshots` (`vms/snapshots.py`), 202 | nothing | |
| `hyperlite_resource_pool` | `GET`, `POST /pools`, `PATCH` (name), `DELETE`, members (`pools.py`) | `description` in `PATCH` | additive |
| `hyperlite_user` | `GET`, `POST /auth/users`, `PATCH` (`role`, `password`), `DELETE` (`auth.py`) | `GET /auth/users/{username}`; `enabled`, and whether the account must set its password at first sign-in | additive |
| `hyperlite_group` | `GET`, `POST /groups`, `PATCH` (name), `DELETE`, members (`groups.py`) | nothing | |
| `hyperlite_role` | `GET`, `POST /acl/custom-roles`, `PATCH` (name only), `DELETE` (`acl.py`) | `privileges` in `PATCH` (today a change means delete and recreate, which drops its assignments) | additive |
| `hyperlite_acl` | `GET`, `POST /acl`, `DELETE /acl/{id}` | `GET /acl/{id}` | additive |

Every row is additive: none needs a v2. Each comes as its own pull request into `test`, with its tests, before the
provider resource that uses it; the provider's first release needs the rows of `all`, `hyperlite_vm`, `hyperlite_vm`
(clone), `hyperlite_template` (data) and the `GET` of one object.

## 10. Rights

A Terraform token should have what it manages and no more. Today creating a VM needs the global `admin` role, so the
token of a pipeline is an administrator's, and no privilege says on which storage pool a user may put a disk. Proxmox
has both: `VM.Allocate` and `Datastore.AllocateSpace` on a storage. With `vm.allocate` and `storage.allocate`
(section 9), the documentation gives a role "Terraform" made of `vm.allocate`, `vm.hardware`, `vm.power`,
`vm.options`, `vm.clone`, assigned on a resource pool, and `storage.allocate` on the storage pools it may use: the
pipeline then creates VMs in its pool, on its storage, only. Networks, storage
pools, users and roles stay administrator resources.

## 11. Compared with `hashicorp/vsphere`, point by point

| Point | `hashicorp/vsphere` | Hyperlite provider (proposed) |
|---|---|---|
| Authentication | user and password (a vCenter session), `allow_unverified_ssl` | an API token, the CLI's TLS rule with a pinned fingerprint; `insecure` for labs only |
| Identity of a VM | the instance UUID; the name and folder are attributes | the libvirt UUID; the name is an attribute; the VMID when #517 lands |
| Where a VM runs | `resource_pool_id` (required), `host_system_id`, `datastore_id` | `node` (optional), `storage_pool` per disk; `resource_pool` is the rights pool (`/pools`), a different thing |
| Clone | `clone { template_uuid, linked_clone, timeout }` | `clone { template \| vm, storage_pool }`; linked clones when #423 lands |
| Reading a template | data source `vsphere_virtual_machine`: `guest_id`, `firmware`, `scsi_type`, `disks[].size`, `network_interface_types`, used to build the clone | data source `hyperlite_template`: `vcpu`, `memory_mb`, `firmware`, `disks` (needs section 9), `ssh_user` |
| Customisation | `clone.customize { linux_options { host_name, domain }, network_interface { ipv4_address }, ipv4_gateway, dns_server_list, windows_options }` through VMware Tools; or cloud-init through `extra_config` (`guestinfo.userdata`) | `cloud_init { user, ssh_keys, password, hostname, ip_config, dns, user_data }` on a cloud-init drive, as Proxmox; Windows (sysprep) is out of scope, as Proxmox's cloud-init for Windows needs cloudbase-init in the image |
| Disks | `disk { label, size, unit_number, thin_provisioned, datastore_id }`; grow only | `disk { size_gb, storage_pool, bus, cache, discard }` in order; grow only (`resize`), a smaller size replaces |
| Interfaces | `network_interface { network_id, adapter_type, mac_address }` | `network_interface { network, vlan_tag, mac }` |
| Changes needing a power-off | `reboot_required` computed by the provider, it powers off and on | read from the server (`GET /pending-changes`), stop only with `allow_restart = true` |
| Long operations | each vSphere call is a `Task`, waited with `task.WaitForResult` | each call answers a `task_id` (section 6), waited with `WaitTask`, cancelled on timeout |
| Waiting for the guest | `wait_for_guest_net_timeout`, `wait_for_guest_ip_timeout` | `wait_for_ip` with `timeouts` |
| Import | `terraform import vsphere_virtual_machine.vm /dc/vm/folder/name`, then disks read from the VM | `terraform import hyperlite_vm.web node2/web-1` (or the UUID); everything read back, `cloud_init.password` excepted (write-only) |
| Moving a VM | changing `host_system_id` or `datastore_id` migrates | changing `node` migrates; changing a disk's `storage_pool` moves the disk (`/disks/{dev}/move`) |
| Permissions | `vsphere_role`, `vsphere_entity_permissions` | `hyperlite_role`, `hyperlite_acl` (on a VM, a pool or the whole cluster) |
| Backups | none (VADP products) | `hyperlite_vm_backup_schedule`, `hyperlite_backup_job` |

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
| `hyperlite_role`, `hyperlite_acl`, `hyperlite_backup_job`, `hyperlite_resource_pool` | the id shown in the dashboard |

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
  signed with a GPG key, and `terraform-registry-manifest.json` (protocol 6.0), from a tag `v1.0.0` of the provider's
  repository, in a protected GitHub environment holding the key (as `publish.yml` holds the APT key).
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
| 3 | Hyperlite | API: `?wait=false` and tasks for create, clone, deploy, start, stop; cloud-init at creation (`ssh_keys`, `ip_config`, `hostname`, `user_data`); `GET` of one object where missing; `response_model` on what the provider reads |
| 4 | provider | Skeleton: provider block, `/health` check, data sources `hyperlite_node(s)`, `hyperlite_template`, `hyperlite_network`; GoReleaser, signed pre-release in a private test |
| 5 | provider | `hyperlite_vm` (create, clone, cloud-init, disks, interfaces, power, import), the acceptance job |
| 6 | provider | networks, storage pools and volumes, snapshots, backup schedules and jobs |
| 7 | Hyperlite + provider | `vm.allocate`, `storage.allocate` and `hyperlite_user`, `hyperlite_group`, `hyperlite_role`, `hyperlite_acl`, `hyperlite_resource_pool` |
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

## 17. Questions for the maintainer

1. **Its own repository** `Hyperlite-hv/terraform-provider-hyperlite`, registry name **`hyperlite-hv/hyperlite`**?
2. **Plugin Framework** (not SDKv2)?
3. **The client as a package in `cli/`** (`cli/hyperlite`, imported by the provider), rather than a client generated
   from the OpenAPI document?
4. **Long operations**: every route that makes a task answers `202 {"task_id"}` with `?wait=false` in v1 (additive),
   the default changing in v2 (section 6)?
5. **`?node=` forwarded to the member on every route** of a VM, template, network and pool, as Proxmox's `/nodes/{node}`
   (section 8), done in Hyperlite before the provider?
6. **`vm.allocate` and `storage.allocate`**, so that a Terraform token is not an administrator's (section 10)?
7. **Scope of 1.0**: the resources of section 5 without containers, HA, replication and firewalls, which come in 1.1?
8. **The GPG signing key**: a new key for the provider, held in a protected environment of its repository, or the
   APT repository's key?
