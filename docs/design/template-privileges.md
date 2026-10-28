# Template privileges: deploying and making templates without being an administrator

Today every route of `app/routers/templates.py` that changes something (`deploy`, `from-vm`, `rename`, `delete`)
takes `require_role("admin")`. A pipeline that builds images (Terraform makes a VM, Ansible configures it, the VM
becomes a template), or one that only deploys VMs from templates, then needs an administrator's token: if it leaks, it
is the whole cluster. Proxmox VE and vSphere give these steps their own privileges, assigned where the templates are.

## 1. How others do it

| | Proxmox VE | vSphere | Hyperlite today |
|---|---|---|---|
| Where a template lives | a VM with the template flag, in a pool like any VM | a VM marked as template, in a folder | `templates_store`, outside the pools (`vm_records.forget` drops the VM's pools when it is converted) |
| Deploy a template | `VM.Clone` on the template, `VM.Allocate` on the target pool, `Datastore.AllocateSpace`, `SDN.Use` | `VirtualMachine.Provisioning.DeployTemplate` on the template, `VirtualMachine.Inventory.CreateFromExisting` on the target folder, `Datastore.AllocateSpace`, `Network.Assign` | admin |
| Make a template from a VM | `VM.Allocate` on the VM (the template keeps its pool) | `VirtualMachine.Provisioning.CreateTemplateFromVM` / `MarkAsTemplate` | admin |
| Rename, delete a template | `VM.Config.Options`, `VM.Allocate` | `VirtualMachine.Inventory.Delete`, rename on the folder | admin |

## 2. Proposal

### 2.1 Templates are members of pools

As on Proxmox VE, a template belongs to pools, and an assignment on a pool covers its templates:

- **made from a VM**, a template goes in the VM's pools: converted (`keep_vm=false`), the VM's memberships move to the
  template; copied (`keep_vm=true`), the template joins the same pools and the VM keeps its own;
- an administrator adds a template to a pool or takes it out (`POST /pools/{id}/members` and
  `DELETE /pools/{id}/members/{name}`, with a `kind`: `vm` by default, so API v1 only grows);
- a renamed template keeps its pools; a deleted one leaves them;
- storage: a `kind` column in `pool_members` (`vm` or `template`), so a VM and a template of the same name never
  collide; hyperlite-cfs carries the same table.

### 2.2 Two privileges

| Privilege | Label | Assigned on | Allows |
|---|---|---|---|
| `template.deploy` | Deploy templates | the pool of the template | `POST /templates/{name}/deploy`, with `vm.allocate` on the target pool, `storage.allocate` on the target storage and `network.assign` on the network, as a VM's creation asks |
| `template.manage` | Make, rename and delete templates | a pool | `POST /templates/from-vm/{vm}` for a VM of that pool (a conversion also needs `vm.allocate` on the VM, since the VM is gone), `rename` and `delete` of a template of that pool |

Two privileges rather than vSphere's four: deploying is what most accounts need (a team that creates VMs), making,
renaming and deleting go together (the account that builds images). Proxmox VE does the same with `VM.Clone` and
`VM.Allocate`. A custom role takes them like any other; no predefined role gets them by default.

### 2.3 Routes

- `POST /templates/{name}/deploy` takes `resource_pool` and `storage_pool`, as `POST /vms` does: the VM is made in its
  pool in the same request (an account without rights outside its pool cannot add it afterwards). Without
  `resource_pool`, only an administrator may deploy, as today.
- `GET /templates`: an administrator and the global viewer see every template, as today; another account sees the
  templates of the pools where it holds `template.deploy` or `template.manage`. `GET /templates` adds `pools` to each
  template.
- The checks run before any work (`check_only`), and a refusal is a 403 that names the missing privilege and where, as
  `app/routers/vms/create.py` does.
- Linked clones keep their protection: a template with linked clones is not deleted or renamed, whoever asks.

### 2.4 Terraform provider

- `hyperlite_vm { clone { template } }` sends `resource_pool` and `storage_pool` to `deploy`;
- `hyperlite_template` reads `pools` (the VM's pools when it is made);
- the documentation's `hyperlite_role` example gains an image-pipeline role: `vm.allocate`, `vm.power`,
  `vm.hardware`, `storage.allocate`, `network.assign`, `template.deploy`, `template.manage`.

## 3. Steps

1. Pools hold templates (`kind`), memberships follow a conversion, a copy, a rename and a deletion; `GET /templates`
   says `pools`.
2. The two privileges, the checks on `deploy`, `from-vm`, `rename` and `delete`; `resource_pool` and `storage_pool` on
   `deploy`; the filtered listing; the role builder shows them.
3. The provider: `resource_pool` on a clone, `pools` on `hyperlite_template`, the documented pipeline role.
4. On real nodes: a non-administrator token with that role makes a VM in its pool, converts it into a template,
   deploys it, renames and deletes it; and is refused each step outside its pool.

## 4. Questions

1. Two privileges (deploy / manage), or four as vSphere (deploy / make / rename / delete)?
2. Should the predefined Manager role get `template.deploy`?
