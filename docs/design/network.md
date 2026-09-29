# Design: VLANs, bonds and host network settings

Status: **proposal, waiting for the maintainer's decision.** No code is written before it is accepted.

## 1. Where we are

- Virtual networks are libvirt networks: NAT, isolated, or bridge onto an existing host bridge
  (`app/routers/network.py`). The host's own network (interfaces, bridges, bonds, addresses) is set up by hand or by
  the installer, outside Hyperlite.
- A VM interface can carry a `vlan_tag` (`PUT /vms/{name}/network`). libvirt honours `<vlan><tag>` only on Open
  vSwitch bridges and a few direct modes. **On a standard Linux bridge the tag is accepted and silently ignored**: the
  VM lands on the untagged network while the UI says VLAN 20. That breaks the "no fake success" rule and is the first
  thing to fix.
- A per-network firewall exists (an iptables chain per bridge, `app/core/network_firewall.py`) and a per-VM firewall
  (libvirt nwfilter). There is no host firewall (the ports of the host itself).

## 2. Goals, in order

1. A VLAN tag on a VM does what it says, or is refused with the reason.
2. VLANs that work on the most common setup: a single Linux bridge on a trunk port (Proxmox's "VLAN aware bridge").
3. Bonds (two NICs for redundancy or bandwidth) and VLAN interfaces for the host, set from the dashboard.
4. A host firewall (SSH, the dashboard, migration ports) that can never lock the administrator out.

Non-goals here: overlay networks between sites (VXLAN/EVPN, Proxmox SDN zones), and routing between VLANs.

## 3. Options

### 3.1 VLANs for VMs

| Option | How | For | Against |
|---|---|---|---|
| A. VLAN-aware Linux bridge | `vlan_filtering=1` on the bridge; per VM port, `bridge vlan add dev vnetX vid 20 pvid untagged`, applied by a libvirt hook (`/etc/libvirt/hooks/qemu`) or by Hyperlite after start and after migration | No new package; what Proxmox does by default; one bridge carries every VLAN | libvirt does not apply `<vlan>` itself on Linux bridges, so Hyperlite must apply and re-apply the port VLAN (start, restart, migration, libvirtd restart) |
| B. Open vSwitch | Replace the bridge with an OVS bridge; libvirt applies `<vlan>` natively | Native in libvirt, trunks and tags per port | New package and a different bridge on every node; converting the management bridge to OVS is a risky change on a live host |
| C. One Linux bridge per VLAN | `br20` on `bond0.20`, one libvirt network per VLAN | Simplest, libvirt understands it | One bridge per VLAN per node; many VLANs means many objects |

**Recommended: A, with C as the fallback the UI can already offer.** The tag becomes real on a VLAN-aware bridge;
on a bridge without `vlan_filtering`, setting a tag is **refused** with "this bridge does not filter VLANs: create a
VLAN network (C) or make the bridge VLAN-aware". The silent case disappears in step 1, before anything else.

### 3.2 Host interfaces (bonds, VLAN interfaces, bridges)

| Option | How | For | Against |
|---|---|---|---|
| A. ifupdown2 files | Write `/etc/network/interfaces.d/hyperlite-*`, apply with `ifreload -a` | What Proxmox uses; atomic reload; Debian native | ifupdown2 must replace ifupdown on existing hosts |
| B. systemd-networkd | `.netdev`/`.network` files | Clean, declarative | Hosts installed with ifupdown would mix two managers |
| C. NetworkManager (nmcli) | | Common on desktops | Not on servers installed from our ISO |

**Recommended: A**, only for interfaces Hyperlite creates (a prefix in their file names), never rewriting the
administrator's own `/etc/network/interfaces`. Every change is **applied with a rollback timer**: the new
configuration is applied, the dashboard must confirm within 60 s from the browser, otherwise the previous files are
restored and reloaded automatically (the same idea as a network change on a switch or `netplan try`).

### 3.3 Host firewall

nftables, in its own table (`inet hyperlite`), never touching other tables. Rules come from a list in the dashboard;
three are **pinned and cannot be removed**: SSH from the cluster nodes, the dashboard port from the address the admin
is using right now, and the migration/NBD ports between nodes. Applying uses the same 60 s rollback. It is off until
enabled.

## 4. Risks

| Risk | Mitigation |
|---|---|
| Locking the administrator out (network or firewall change) | Rollback timer that needs a confirmation from the browser; pinned rules; a console command that disables everything (`hyperlite-network reset`) |
| VLAN port settings lost after a restart or migration | Applied from the libvirt `qemu` hook on `started`/`migrate`, and re-checked by the node poller |
| Existing tagged VMs on plain bridges | Step 1 lists them; the tag is kept in their definition but the UI shows "not applied" until the bridge is VLAN-aware |
| Different bridge names across nodes | The compatibility diagnostic already compares networks; it gains "VLAN-aware" |

## 5. Steps (one pull request each)

1. **Stop the silent VLAN.** Detect whether a bridge filters VLANs; refuse a tag on one that does not, show "not
   applied" on existing ones, and document the fallback. No host change.
2. **VLAN-aware bridge support** (3.1 A): apply and re-apply the port VLAN through the libvirt hook, checked by the
   poller; a "make this bridge VLAN-aware" action with the rollback timer.
3. **VLAN networks** (3.1 C) from the UI: a bridge on `<nic>.<vid>` per VLAN.
4. **Bonds and VLAN interfaces** for the host (3.2 A) with the rollback timer.
5. **Host firewall** (3.3), off by default, with the pinned rules.

## 6. Questions for the maintainer

1. How are the dev and production hosts cabled: one NIC or several, a trunk (tagged VLANs) from the switch or not?
2. Which VLANs do you need VMs on, and is the switch able to trunk them to the server?
3. Is changing the host network from the dashboard wanted at all, or should Hyperlite only use bridges an
   administrator made (steps 1 to 3 only)?
4. Host firewall: wanted now, or later?
