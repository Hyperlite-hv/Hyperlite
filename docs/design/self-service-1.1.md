# Design: self-service VMs for internal teams, 1.1

Status: **draft**, rewritten on 2026-10-20 after Antho's decisions of that day (section 2). No code before the draft
is accepted, nor before 1.0.0 is released.
Target: 1.1, after 1.0.0.

## 1. The goal

Internal teams create the VMs they need themselves, within limits an administrator sets, without asking an
administrator each time: what VMware sells separately as Aria Automation, reduced to its useful core. No external
customers, so no billing.

What a VM is made from does not change: Hyperlite prepares no operating system itself. A VM comes from

- **the Debian cloud image** or **a template** (a VM turned into a template), customized at first boot by
  cloud-init: name, account, password, SSH keys, as Proxmox VE does with its cloud-init templates;
- **an ISO**, installed by hand from the console by the person who asked for the VM.

## 2. Decisions (Antho)

- 2026-10-20: **no automatic creation of operating systems**. The unattended installation from an ISO (Kickstart,
  autoinstall) is removed before 1.0 (#503); the Windows unattended installation, the catalogue of cloud images
  prepared with the agent and the ISO recognized by its content, planned in the first draft, are dropped. Cloud-init
  stays, as on Proxmox VE. Templates stay.
- 2026-10-20: self-service is **kept for later**: this document, for 1.1.
- 2026-10-12, still valid: internal teams only, no billing; predefined sizes plus a custom size the administrator
  allows or not; a private network per team, possible and optional; approval optional, offer by offer.

## 3. Where we are (1.0)

- **Groups** gather users; **VM pools** gather VMs; an **assignment** gives a user or a group a role on a VM, a pool
  or a container, and a VM inherits the assignments of its pools. **Scoped roles** (Reader, Operator, Manager) and
  custom roles are made of privileges (power, console, snapshots, hardware, options…).
- **Creating** a VM is for administrators only (`POST /api/v1/vms`).
- **Templates** are made from a stopped VM and deployed with a new name and network; a template made from a cloud
  image VM gets a new cloud-init identity (hostname, instance-id), not a new account or password. A template lives on
  the node that made it.
- **Cloud-init after creation** already changes a cloud image VM's SSH keys and password at its next boot.

## 4. Building blocks

### 4.1 Templates usable everywhere, customized by cloud-init

Prerequisites of the offers, useful on their own:

- **Cloud-init on deployment**: deploying a template made from a cloud image VM asks for the same fields as the
  cloud image (account, password, SSH keys), written at first boot. A template made from a VM installed from an ISO
  deploys as today, with no customization (stated in the form and the API answer).
- **On every node**: a template can be copied to the other nodes, or kept on a shared pool (NFS, iSCSI), so an offer
  works on whichever node has room.

### 4.2 Sizes

The administrator defines sizes (Small: 2 vCPU, 4 GB, 40 GB; Medium…). An offer lists the sizes it allows, plus a
custom size within bounds if the administrator allows it.

### 4.3 Offers

An offer is published by an administrator: a source (the cloud image, a template, or an ISO to install by hand),
the sizes allowed, the network (a team's private network or a chosen one), the storage pool, and what the requester
gets on the VMs it creates (a role: Operator by default). Examples: "Debian server", "Web template", "Windows Server
(installed from the ISO)". Each offer says which groups may use it.

### 4.4 Teams and quotas

A team is an existing **group**. Each team has a **VM pool** of its own: the VMs its members create land there, and
the role the offer gives is assigned to the team on that pool, through the existing permission system. A member sees
and manages the team's VMs only, with that role's privileges.

Each team gets limits: number of VMs, vCPUs, memory, disk. A request over the quota is refused with the reason and
what is left. A usage summary per team (current use against its limits) replaces billing.

### 4.5 Requests and approval

A member picks an offer, a size, a name (and the account and password for a cloud-init source), and submits. Without
approval the VM is created at once, as a task like any other. With approval the request waits; the team's approvers
(a group named on the offer) are notified, approve or refuse with a reason from the dashboard, and the requester is
notified. A request does not hold quota until it is approved.

### 4.6 Lifetime

An offer can give its VMs a lifetime (30 days, for example): a reminder before the end, extendable by the owner within
a maximum the offer sets, and at the end the VM is stopped, then deleted after a grace period. It reuses the automatic
deletion of inactive VMs, which already warns before deleting.

### 4.7 A private network per team, optional

The administrator can give a team its own isolated or NAT network; the team's offers then use it.

## 5. Security

- A member never calls `POST /api/v1/vms`: the offer's endpoint creates the VM on its behalf, with only what the offer
  allows (source, size bounds, network, pool, role). The checks are on the server; the form only shows them.
- Quotas are checked and reserved in the same transaction as the request, so two requests at once cannot both pass
  the last place.
- Every request, approval, refusal, extension and expiry is in the audit log.
- The account password given for a cloud-init source is never stored (as for the cloud image today).

## 6. API

Additions only, under `/api/v1`, as the contract requires: `/api/v1/sizes`, `/api/v1/offers`,
`/api/v1/teams/{group}/quota` and its usage, `/api/v1/requests` (create, list, approve, refuse), and an `expires_at`
on the VMs that have a lifetime. New error codes, added to the documented list: `quota_exceeded`,
`approval_required`, `offer_not_allowed`.

## 7. Order of the work (each step one PR, tested on the lab)

1. Cloud-init on template deployment; templates on every node (4.1).
2. Sizes and offers, used by administrators first (4.2, 4.3).
3. Teams: their pool, their role, quotas and usage (4.4); requests without approval.
4. Approval and notifications (4.5).
5. Lifetime (4.6) and private networks per team (4.7).

Each step works on its own: after step 1, templates already deploy customized VMs on any node.

## 8. Questions to settle before step 1

1. Who approves: a group per offer (proposed), or every administrator?
2. A team's VMs at the end of their lifetime: stopped then deleted after a grace period (proposed), or only stopped?
3. May a member resize a VM later within the team's quota, or only through a new request?
4. Templates on every node: a copy per node (simple, uses disk), or required on a shared pool (no copy)?
