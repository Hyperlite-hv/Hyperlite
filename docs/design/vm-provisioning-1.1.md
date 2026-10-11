# Design: VM provisioning for 1.1

Status: **draft**, to be discussed with Antho. No code before it is accepted. Target: 1.1, after 1.0.0.

## 1. Why

Creating a VM is the action administrators repeat most, and the first thing a team coming from VMware compares.
VMware has templates and *customization specifications* (clone, then the VM comes up with its name, a fixed IP and
joined to the Windows domain); Proxmox has cloud-init but leaves the template preparation to the command line.
Companies want standard VMs (same base, same agents, same hardening) created in seconds, then self-service and
automation (API, Terraform) on top. Hyperlite today has the pieces, not a system.

## 2. Where we are

| Way | What it does | Limits |
|---|---|---|
| No ISO (default) | Copies the Debian 12 cloud image (`vm_builder.BASE_IMAGE`), cloud-init creates the account, password and SSH key. Ready in about a minute. | One image only, downloaded by Hyperlite from cloud.debian.org. |
| Recognized ISO | An answers ISO: Kickstart (RHEL, Rocky, Alma, CentOS, Fedora), autoinstall (Ubuntu Server). `app/core/unattended_install.py`. | Recognition by the **ISO file name**. Debian, Kali, Windows, Ubuntu desktop not covered. No language, keyboard, time zone or desktop choice. |
| Other ISO | Manual installation through the console. | Account and SSH key by hand; on Debian, an account without sudo when a root password is set. |
| Disk import, template | Boots an uploaded disk, or clones a template (`/templates/{name}/deploy`: new name and network, cloud-init re-seeds the host name). | Templates are local to one node; deploying sets only the name and the network; nothing for Windows (no sysprep). |

Fixed addresses exist only as DHCP reservations on the NAT/isolated networks Hyperlite manages
(`app/core/network_alloc.py`), not on a bridge to the company LAN, where most production VMs live.

## 3. Goals

1. **One creation form** whatever the source, with the same settings everywhere: name, resources, disks, network,
   account, SSH keys, **fixed IP** (address, gateway, DNS) or DHCP, host name and domain, time zone, keyboard,
   language. What a source cannot honour is greyed with the reason.
2. **Templates as the core** ("golden images"): made from a VM, cluster-wide (on shared storage, or copied to the
   node that deploys), with a description, an OS, and default settings; deployed in seconds (linked or full clone).
3. **Customization at deployment**, Linux (cloud-init) and **Windows** (sysprep + `unattend.xml` through a
   cloudbase-init or the answer file on a drive): name, fixed IP, administrator password, **Active Directory join**
   (domain, OU, an account allowed to join, its password kept encrypted).
4. **A catalogue of official cloud images**: Debian 12/13, Ubuntu LTS, Rocky, Alma, Fedora, openSUSE…; downloaded on
   demand, checked (the publisher's checksums and signatures), cached in the ISO library, **importable offline**
   (a node without network takes them from a file, like the update file).
5. **Unattended installation from an ISO**, recognized by its **content** (`.treeinfo`, `.disk/info`, `sources/install.wim`),
   not its name: Debian/Kali (preseed), Ubuntu Server and desktop, the RHEL family, **Windows and Windows Server**
   (autounattend: edition, disk, administrator, OOBE skipped, virtio drivers and the guest agent installed). Mainly
   used to build templates; the ISO stays available for anything else.
6. **Bulk and API**: create N VMs from one template with a naming pattern (`web-{01..10}`) and addresses from a range;
   the same in the API, so a Terraform provider can follow.

Non-goals for 1.1: a self-service portal with quotas (1.2, built on this), a marketplace of applications.

## 4. Sketch

- **Sources** become one notion: `image` (catalogue or uploaded cloud image), `template`, `iso` (unattended or
  manual), `disk` (import). The form asks "From what?", then the same settings.
- **Customization** is one object (`Customization`: host name, domain, network settings, accounts, keys, locale,
  Windows domain join, first-boot commands), turned into cloud-init, Kickstart, preseed, autoinstall or unattend.xml by
  one module per format, each tested against the real installer in the nested lab.
- **Templates** move to the replicated configuration (cfs), with their disk on shared storage when there is one;
  otherwise deployment on another node copies the disk first (a task, like ISO copies today).
- **Fixed IP on a bridge**: written into the guest (cloud-init network-config v2, unattend), since no DHCP of ours
  serves that LAN; an optional IP range per network avoids handing out the same address twice.
- **Saved specifications** ("customization profiles", as in VMware): a company keeps "LAN servers" (domain, DNS,
  time zone, keys) and picks it at creation.

## 5. Order of the work (each step one PR, tested in the lab)

1. Customization object and formats, fixed IP on a bridge (Linux).
2. Templates cluster-wide, deployment with customization, linked clones.
3. Windows: sysprep of a template, unattend at deployment, AD join.
4. Catalogue of cloud images, with offline import.
5. Unattended ISO installation by content: Debian/Kali, Ubuntu desktop, Windows.
6. Bulk creation and API.

## 6. Questions for Antho

1. **Which OS matter most** for the first customers (Windows Server is the biggest effort, and the most expected
   from VMware users)?
2. **Linked clones** (fast, share the template's disk) or **full clones only** (independent, simpler to move)?
3. **The catalogue's sources**: only official publishers, or also the company's own image server?
4. **Windows licensing**: the product key asked at deployment, or KMS/AVMA left to the company?
5. Is **bulk creation** in 1.1, or with the self-service portal in 1.2?
