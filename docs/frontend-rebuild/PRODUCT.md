# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Infrastructure and systems administrators, DevOps and SRE engineers, IT departments and advanced homelab operators who run their own virtualization hosts. They work on desktop and laptop screens for long sessions (routine operations, incident response, capacity checks), and occasionally check status or run one action from a tablet or a phone. Several people can share one installation, with different rights (administrator, read-only observer, custom roles, per-VM and per-container ACL).

The typical installation is small to medium: 1 to 10 hypervisor nodes and up to a few hundred VMs, reached over HTTPS on a private network (often through a VPN such as Tailscale). The interface must stay fluid up to about 1,000 VMs.

## Product Purpose

Hyperlite is a self-hosted hypervisor management console. It drives libvirt/QEMU-KVM virtual machines and LXC containers on the local host and on registered remote hosts (over SSH, with no agent installed on the managed nodes), and packages everything as an appliance ISO or an APT package for Debian.

Success means an administrator can see the real state of the infrastructure at a glance, find any VM, node, storage or network in seconds, perform routine and advanced operations safely, and diagnose an incident from the same screens (tasks, logs, events, alerts) without reaching for a shell.

## Positioning

An agentless multi-node control plane over stock libvirt and SSH that installs as a single appliance or package and is honest about what a host can do: it detects the capabilities of each host, shows a compatibility diagnostic before risky operations (registering a node, live migration), refuses or warns instead of failing halfway, and never automates a dangerous recovery (HA recovery stays a manual, confirmed action).

## Operating Context

Workflows covered today: VM creation (cloud image, ISO with unattended installation for Debian/Ubuntu/RHEL families, manual installation including Windows with a drivers ISO, import of an existing disk), lifecycle (start, graceful and forced stop, restart, delete), graphical (VNC) and SSH consoles, host shell, hardware hot-plug, snapshots (libvirt internal and native ZFS), clone, template conversion and deployment, disk export and import, hot and cold backups with schedules, retention and restore, live migration with a compatibility check, basic HA with manual recovery, LXC containers (images from Docker Hub or OCI registries, clone, backup/restore), storage pools (directory, NFS, ZFS), virtual networks with per-network and per-VM firewalls, cluster nodes, host capability and compatibility views, deployment profiles and allocation policy, users, roles, groups, pools, ACL, two-factor authentication, personal API tokens, OIDC single sign-on, an audit journal, persisted tasks with progress, outgoing notifications (webhook, email), an automation job engine, and a self-update mechanism with automatic rollback.

The API is the source of truth for every screen. Metrics are collected continuously and kept as history.

## Capabilities and Constraints

- The API and database use French wire identifiers and values (`nom`, `etat`, `en_cours`, `observateur`...). They are contracts and are never renamed; only displayed text is translated.
- The interface is bilingual from the start: English and French, with a language selector. (Decided with the product owner.)
- The interface has a dark and a light theme; dark is the default. Both are preserved.
- Existing routes, actions, form fields (including advanced ones), permissions, WebSocket consoles, polling and API contracts must all be preserved by any redesign. No feature may be removed; a difficult feature is documented, kept and re-housed.
- Known scope limits that the interface must state honestly and never disguise: no fencing/STONITH (HA recovery is manual), VMs on ZFS pools are not live-migratable, ZFS pools created from the interface are local to one node, no Ceph, no unattended Windows installation, no instantaneous snapshot for containers.
- Remote-node operations depend on the node parameter of the API; the local host is the default.
- Undecided: whether the current logo mark is kept or redrawn.

## Brand Commitments

The product name is Hyperlite ("Hyperlite Hypervisor" in the current header). No other identity commitment is binding: the visual world is to be replaced, not polished.

## Evidence on Hand

Real data only: a working dev instance with a real libvirt host and a real Windows Server VM, an end-to-end test suite covering the current interface (`docs/webui-test-matrix.md`), feature and architecture documentation (`docs/features.md`, `docs/architecture.md`, `docs/api.md`). There are no customer testimonials, benchmarks or case studies, and none may be invented.

## Product Principles

1. **Preserve capability.** Nothing the product can do disappears in a redesign; advanced options stay reachable, only their presentation improves.
2. **Real data, honest state.** Never mock or embellish. Always show when data is stale, unavailable, offline or permission-restricted.
3. **Risk is explicit.** Destructive or cluster-wide actions name the resource and the impact, are never one accidental click away, and the safest reasonable default is offered first.
4. **Diagnose where you look.** The state, tasks, logs, events and alerts of a resource are one step from the resource itself, and compatibility is shown before an action, not after it fails.
5. **Calm in normal times, unmistakable in failure.** A healthy infrastructure looks quiet; a problem is found at once without an interface that alarms permanently.

## Accessibility & Inclusion

Target WCAG 2.2 AA: full keyboard operation (including the resource tree), visible focus, status never conveyed by colour alone, respect for reduced motion, and comfortable contrast for multi-hour sessions. French and English users are equal citizens.
