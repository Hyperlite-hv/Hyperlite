# Design: VLANs, bonds and host network settings

Status: **steps 1 and 2 decided** (step 2 "as Proxmox VE does", parts 1 and 2: section 7). Steps 3 to 5 wait for the
maintainer's decision.

## 1. Where we are

- Virtual networks are libvirt networks: NAT, isolated, or bridge onto an existing host bridge
  (`app/routers/network.py`). The host's own network (interfaces, bridges, bonds, addresses) is set up by hand or by
  the installer, outside Hyperlite.
- A VM interface can carry a `vlan_tag` (`PUT /vms/{name}/network`, `POST /vms/{name}/interfaces`). libvirt honours
  `<vlan><tag>` only on Open vSwitch bridges and SR-IOV networks (forward mode `hostdev` or `passthrough`). On a NAT,
  isolated, Linux bridge or macvtap network it refuses the tag when the VM starts ("vlan tag not supported for this
  connection type", verified on libvirt 10): a tag written to a stopped VM kept it from starting again. **Step 1 is
  done**: the API refuses a tag on a network that cannot carry it, before writing anything, and the network list says
  which networks can (`vlan`).
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
VLAN network (C) or make the bridge VLAN-aware". Step 1 (the refusal) is done.

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

## 7. Step 2: VLANs on a VLAN-aware Linux bridge

Decided by the maintainer: both parts, as Proxmox VE (`tag` and `trunks` of a network device on a "VLAN aware"
bridge).

**Part 1, a VM's VLANs on a bridge that already filters VLANs** (`app/core/bridge_vlan.py`):

- A bridge network is VLAN-capable when its host bridge has `vlan_filtering` on; `GET /networks` says so (`vlan`, and
  `vlan_mode`: `native` for Open vSwitch and SR-IOV, `bridge` for a VLAN-aware Linux bridge).
- libvirt refuses `<vlan>` on a Linux bridge even when it filters VLANs, so the interface's access VLAN and trunk
  VLANs are kept in the VM's own metadata (`urn:hyperlite:vlan:1`, one `<port>` per MAC address). They travel with the
  definition (migration, export) and follow a clone's new MAC addresses.
- Hyperlite sets them on the VM's bridge port with `bridge vlan`, as Proxmox VE's tap script does: the access VLAN as
  the port's PVID, untagged (VLAN 1 without one), the trunk VLANs tagged. It does so:
  - when the VM starts or arrives by migration: libvirt's qemu hook (`/etc/libvirt/hooks/qemu.d/hyperlite-vlan`,
    installed by the package) runs on `started` with the live definition, which names each tap and its bridge;
  - when an interface is added or changed on a running VM (the API);
  - when the service starts, for the VMs already running.
- A port whose VLANs cannot be set carries no traffic at all, rather than the bridge's untagged VLAN, and the reason is
  in the journal (`journalctl -t hyperlite-vlan`): libvirt keeps a hook's output only when the hook fails, and this
  hook never fails the VM.
- Known window: libvirt runs `started` once QEMU runs, so the tap sits on VLAN 1 for that moment (the firmware has
  not sent anything yet in practice). Proxmox VE sets the port before QEMU starts the guest.

Checked in the lab (Ubuntu 24.04, Linux 6.8, libvirt 10.0; a VLAN-aware bridge whose uplink is a trunk to a namespace
holding VLANs 20, 30 and 50):

| What | Result |
|---|---|
| libvirt's `<vlan>` on a VLAN-aware Linux bridge | Refused at start: "vlan tag not supported for this connection type" |
| The qemu.d hook | Runs on `prepare`, `start`, `started`, `stopped`, `release`; on `started` its input names the tap (`vnet0`), its bridge and the VM's metadata |
| A hook added while libvirt runs | **Never called**: libvirt finds its hooks when it starts, so the package restarts it once (running VMs keep running, networks stay active) |
| A new tap port | VLAN 1, PVID, untagged |
| A VM started with tag 20 and trunks 30, 40-42 | The hook sets its port: 20 untagged, 30 and 40-42 tagged |
| A port set by `apply_port` (tag 20, trunk 30), real frames | Reaches VLAN 20 untagged and VLAN 30 tagged; VLAN 50, not in its trunk, unreachable |
| An access VLAN inside a trunk range (20 in 18-22) | 20 untagged, 18-19 and 21-22 tagged |
| A live change, a change made by hand then the service's start-up pass | The port follows the API; the start-up pass sets a port changed by hand back |
| A VLAN recorded for a VM on a bridge that does not filter | The hook leaves its port with no VLAN at all, and the reason is in `journalctl -t hyperlite-vlan` (libvirt's own log has nothing: the hook exits 0) |
| The hook given a broken definition, or any event but `started` | Exits 0 at once, saying why for the former |
| `vlan_filtering` turned off and on again | The ports keep their VLANs |

**Part 2, making a bridge VLAN-aware from the dashboard**: next pull request, with the 60-second rollback of section
3.2.
