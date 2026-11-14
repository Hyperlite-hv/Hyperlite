# Governance: compliance, tags, dynamic pools, sign-in rules, scheduled reviews, exclusions and fleet services

Status: implemented in steps (#906), after docs/design/access-2.md (perimeters, roles, protections, guardrails, dated rights).

Access 2 decides who may do what and what a new VM may be. This document covers what keeps a fleet in order over
time, and the services around VMs that a team expects from a managed platform.

## 1. Compliance review

Guardrails (`app/core/guardrails.py`) are checked when a VM is created. A rule added later, a VM resized, moved into a
pool or made before the rule can break one. The compliance review (`GET /compliance`, `cluster.view`) checks every VM
of the answering node against the rules of the cluster and of each pool it belongs to (name pattern, size, storage
pools, networks, required tags) and against its pools' envelopes, and gives the share of compliant VMs. What can be
fixed without touching the guest (a missing required tag) is fixed from the page (`POST /compliance/fix`,
`cluster.manage`); the rest says what to change.

## 2. Required and inherited tags

- A guardrail may require tags on every new VM (`guardrail_tags`, a table of its own so that an older node keeps the
  rest of the guardrails). A VM is created with `tags`; it must carry the required ones, its pool's included.
- A VM inherits the tags of the pools it belongs to (`object_meta.effective_tags`): the tag restriction of an
  assignment and the compliance review read the inherited ones too. The VM's page shows them apart.
- The VM list tags a selection of VMs at once.

## 3. Dynamic pools

A pool may carry a rule (`pool_rules`, in the permissions' database, written by the daemon for an administrator
only): the VMs carrying a tag, or whose name matches a pattern, belong to it as its members do. Membership grants the
pool's rights, protections and envelope, so the rule is an administrator's: a delegate of the pool could otherwise
widen it to VMs it has no right on. A rule reads a VM's own tags, never the inherited ones (no loop).

Giving a VM a tag that a dynamic pool takes in puts it under that pool: whoever may set the VM's tags (`vm.options`)
may hand it to the pool's people, never take rights for itself on other VMs.

## 4. Sign-in rules

Rules on signing in and on the API, each for everyone, a global role, a group or an account: the source networks
allowed, a second factor required, the hours allowed. Checked at sign-in and on every request.

## 5. Scheduled access review

The access review (Access 2, section 8) runs on a schedule, names reviewers, and removes the assignments nobody kept
by its deadline.

## 6. Exclusions

An exclusion removes one privilege (or all) of an account or a group on one object, whatever the assignments above
it give. Administrators are not held by them.

## 7. Recycle bin

A deleted VM is kept for a number of days (its definition and disks), then removed; it can be brought back until then.

## 8. Secrets vault

Secrets (passwords, keys, tokens) kept sealed by the node, readable by the accounts given access, and referenced by
cloud-init, automation and backups instead of being copied.

## 9. VM identity

A VM gets an identity of its own: a token the guest reads from its cloud-init drive, acting with a role on a
perimeter, renewed by Hyperlite, revoked with the VM.

## 10. Costs and budgets

A price per vCPU, GB of memory and GB of disk per month; the cost of each VM, pool and tag; budgets with an alert.

## 11. Guest updates

The guests' system updates through the guest agent: what is pending per VM, and runs on a selection with a
maintenance window and a report.

## 12. Pool export

A pool's VMs, networks, storage pools and assignments as one Terraform / OpenTofu file.

## 13. VM groups

N identical VMs from one template, with a count to raise or lower.

## 14. Query search

A small query language over every object (`kind:vm state:stopped tag:prod pool:web backup:none age>30d`), from the
palette and a search page.
