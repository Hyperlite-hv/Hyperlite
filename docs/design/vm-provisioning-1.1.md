# Design: VM provisioning for 1.1

Status: **draft**, to be discussed with Antho. No code before it is accepted. Target: 1.1, after 1.0.0.

Everything below starts from measurements on the lab cluster (three nested nodes: absolute times are slower than on
real hardware, the ratios hold) and from what teams leaving VMware or running Proxmox actually ask for.

## 1. The needs, by who has them

| Who | What they do every week | What they need from Hyperlite |
|---|---|---|
| **The admin of a company leaving VMware** (the bulk of the market since Broadcom's licensing change) | Deploys Windows Server and Linux servers from templates onto the company LAN | Templates, **fixed IP on the LAN**, **Active Directory join**, Windows sysprep, the same result every time |
| **A DevOps team** | Creates and throws away VMs from CI, Terraform, scripts | An API that says **when the VM is ready**, official cloud images, seconds not minutes, many VMs at once |
| **A site without Internet** (industry, defence, health) | Same as the others, offline | Images and templates imported from a file, nothing downloaded at first boot |

These come from what competitors sell and what migrations ask for, not from interviews: **Antho checks them with
real prospects** before 1.1 is planned in detail (section 9).

## 2. What it costs today (measured)

A Debian VM created through the API on the lab cluster, until it is usable:

| Step | Time | Where it goes |
|---|---|---|
| Create (disk + definition) | **1.1 s** | qcow2 *linked clone* of the base image: 0.04 s |
| Boot to an IP address | 13.5 s | Firmware, kernel, systemd, DHCP |
| Guest agent answers | **23.9 s** | **~10 s of `apt-get install qemu-guest-agent`** at first boot |
| cloud-init done: really ready | 25.5 s | |

And per storage type, the system disk of a new VM:

| Storage | How the disk is made | Measured |
|---|---|---|
| Directory, NFS | qcow2 linked to the base image | 0.04 s |
| ZFS, iSCSI LUN | **full copy** of the image (`qemu-img convert`) | 5.8 s for a 3 GB Debian image; grows with the image (a 20 GB Windows template: tens of seconds, per VM) |

What this shows:
1. **The disk is not the problem** on file storage; it is on ZFS and LUNs, where storage-native clones exist and are
   not used (`zfs clone` of a snapshot is instant).
2. **Half the wait is a download at first boot.** Every VM fetches the same agent from the Internet; **offline, the
   agent is never installed** and the VM can never be driven (no IP report, no clean shutdown by agent, no quiesced
   backup). This is a correctness problem before being a speed problem.
3. **Nothing says "ready".** The API returns at 1.1 s; a script must guess when the VM is usable.

## 3. How the largest do it

| | VMware vSphere | Nutanix AHV | OpenStack | Proxmox VE |
|---|---|---|---|---|
| Image library | Content Library, synchronized between sites | Image Service, cluster-wide | Glance, versioned | Files per storage |
| Sizes | per template | per VM | **Flavors** | per VM |
| Templates | templates, linked or full clones | clones from images | snapshot → image | templates, linked clones |
| Customization | **Customization Specifications**, saved and reused (name, IP, AD join, sysprep) | cloud-init / sysprep | metadata + config drive | cloud-init drive |
| "Ready" | VMware Tools reports customization done | NGT agent | cloud-init status | none |
| Multi-VM, self-service | Aria Automation blueprints, approvals | Calm | Heat | none |

They all separate **four things** that Hyperlite's form mixes: **what** to install (library), **how big** (size),
**how to customize** (specification), **where** (placement). A VM is a combination of the four.

## 4. Architecture

```
 Library (cluster-wide)          Sizes            Specifications             Placement
 cloud images, ISOs, templates;  2 vCPU / 4 GB…   network, accounts, keys,   node, storage,
 versioned, checksummed,                          domain join, time zone,    network, HA
 prepared once, importable                        first-boot commands
 offline
          \                         |                    |                       /
           └──────────── VM request (one declarative object: the API, Terraform, the form) ──┘
                                              │
                       Provisioning engine: one pipeline, one task per VM, a state machine
       place → clone disk (storage-native) → answers (cloud-init / Kickstart / preseed / unattend)
       → boot → wait for "ready" (agent + cloud-init or the Windows equivalent) → checks → ready
```

### 4.1 Library

- One cluster-wide catalogue in the replicated configuration (cfs): images (cloud images, uploaded disks), ISOs,
  templates. Each entry: name, OS, version, SHA-256, size, where it is stored.
- **Prepared once**: a cloud image added to the library is customized a single time (`virt-customize`): the guest
  agent installed, cloud-init limited to the NoCloud data source. Every VM then boots with the agent present: no
  download, works offline. Expected gain: the ~10 s measured above, and drivable VMs on sites without Internet.
- On shared storage the image lives once; otherwise each node keeps a cached copy, filled on first use (a copy task,
  like ISOs today), verified by checksum.
- Offline import: the same signed-file principle as the update file.

### 4.2 Disks: clone, never copy

| Storage | Clone |
|---|---|
| Directory, NFS | qcow2 linked to the library image (as today) |
| ZFS | `zfs snapshot` of the library zvol once, `zfs clone` per VM: instant, space shared |
| iSCSI LUN | full copy (no clone on a generic LUN), done once per LUN, in parallel per VM |

A "full clone" option detaches the VM from its image (`qemu-img rebase` / `zfs promote`) for VMs meant to live
long or move to storage without the image. An image cannot be deleted while VMs depend on it (the library says which).

### 4.3 Specification and answers

One `Specification` object (host name and domain, IPv4 fixed or DHCP, DNS, accounts and SSH keys, time zone,
keyboard, locale, AD join for Windows, first-boot commands), rendered by one module per format: cloud-init
(network-config v2, interface matched by MAC), Kickstart, preseed, autoinstall, Windows unattend. Each renderer is
tested against the real installer in the nested lab. Specifications can be saved and reused ("LAN servers"), as in
VMware. Secrets (passwords, the AD join account) are encrypted at rest and only written to the answers drive, which
is removed once the VM is ready.

### 4.4 Engine and "ready"

- One pipeline for every source, as a task per VM with explicit states: `placed → disk ready → started → network →
  customized → ready` (or `failed` with the state reached). The API's VM shows it; Terraform waits for `ready`.
- "Ready" = the agent answers **and** the guest reports its customization finished (cloud-init `status --wait`
  through the agent; on Windows, the end of the specialize pass). Timeout per OS, the failure says which state stuck.
- Placement: the node with the most free memory among those that have the network and the storage (none chosen),
  the same rule the DRS will refine.
- Many VMs at once: the pipeline runs per VM in parallel, bounded per node (disk clones and first boots are the load
  peaks), so 20 VMs do not take 20 times one.

## 5. Targets (to measure on real hardware at the end of each step)

| | Today (lab) | Target |
|---|---|---|
| Linux VM from a library image, to **ready** | 25.5 s | under 15 s on real hardware |
| Works without Internet | no (no agent) | yes |
| System disk on ZFS | full copy, grows with the image | under 1 s whatever the size |
| 10 VMs at once | not supported | under 2x the time of one |
| Windows Server from a template, joined to AD | manual | ready in under 5 minutes, no console |

## 6. Order of the work (each step one PR, tested in the lab)

1. **Library with prepared images** (agent included, offline import) and the "ready" state: the measured problem.
2. **Specification** and its renderers, fixed IP on a bridge (cloud-init, Kickstart, autoinstall).
3. **Storage-native clones** (ZFS) and templates in the library, deployed with a specification.
4. **Windows**: sysprep of a template, unattend at deployment, AD join.
5. **Unattended ISO installation recognized by the ISO's content** (Debian/Kali preseed, Ubuntu desktop, Windows).
6. **Many VMs at once** and the declarative request in the API (the base of #330 Terraform and #331).

## 7. What stays as it is

Manual installation from any ISO, disk import, the current API fields (kept as a shortcut for "Debian image + default
specification" so scripts written for 1.0 keep working).

## 8. Risks

- Linked clones tie VMs to their image: deleting or moving an image must be refused while VMs use it, and a full
  clone must be one click away.
- Preparing images changes them: the library keeps the publisher's checksum of the original and the prepared one's.
- Windows is the largest effort (sysprep, drivers, licensing): it comes after the Linux path is solid.

## 9. Questions for Antho

1. **Who first**: the company leaving VMware (Windows and AD first) or the DevOps team (cloud images, API, speed
   first)? The order of section 6 assumes the second, with Windows at step 4.
2. **Sizes** like OpenStack's flavors, or free sizes as today?
3. **Self-service** (non-admins creating VMs within quotas): 1.1, or 1.2 on top of this?
4. **Windows licensing**: a product key asked at deployment, or KMS/AVMA left to the company?
5. Can you check section 1 with two or three real prospects?
