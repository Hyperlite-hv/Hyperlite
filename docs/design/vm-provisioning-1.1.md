# Design: automatic VM creation and self-service, 1.1

Status: **draft**, decisions of section 6 taken with Antho on 2026-10-12. No code before the draft is accepted.
Target: 1.1, after 1.0.0.

## 1. The goal

**Any operating system, created by Hyperlite with nothing done by hand.** The user picks a system from a list
(Debian, Ubuntu, Rocky, Alma, Red Hat, Kali, Windows Server, Windows 11, with or without a desktop), fills the VM's
name, its account and password, its address (automatic or fixed), and clicks Create. Hyperlite does the rest and
shows **"VM ready"**: an account that works (with sudo / administrator rights), SSH, the web terminal, the guest agent.

Then, on top of it, what VMware sells separately as Aria Automation: a **catalogue of offers** that internal teams use
themselves, within **quotas**, with an optional **approval**.

## 2. Where we are

- Automatic today: Debian 12 (a ready cloud image) and installation from an ISO for Red Hat-like systems (Kickstart)
  and Ubuntu Server (autoinstall), recognized by the ISO's file name.
- Everything else is installed by hand from the console (a Debian netinst ends with an account without sudo when a
  root password is set; Windows needs every screen clicked through).
- Templates exist but stay on one node, and a deployment only sets the name and the network.
- Measured on the lab: a Debian VM is usable after about 25 s, of which ~10 s download the guest agent at first boot;
  **without Internet the agent is never installed** and the VM cannot be driven. Nothing tells a script when the VM
  is ready. On ZFS and iSCSI every VM copies the whole system image (5.8 s for 3 GB, more for bigger images).

## 3. How VMware does it, and what we take

vSphere: an administrator installs a system once by hand, turns it into a **template**, keeps it in a **Content
Library** shared by every server, and deploys copies with a saved **customization specification** (name, fixed IP,
administrator password, Windows domain join). Aria Automation, a paid add-on, adds a catalogue, self-service, quotas
and approvals.

We take the templates, the shared library, the customization specifications and Aria's useful core. We do better on
one point: **no template is ever prepared by hand**. Hyperlite offers ready systems directly (as hosting providers
do), and installs any other system unattended from its ISO, Windows included.

## 4. Part A: automatic creation, for every system

### 4.1 The list of systems (the library)

One list, the same on every node of the cluster:
- **Ready images** for the systems that publish one (Debian, Ubuntu, Rocky, Alma, Fedora, openSUSE…): downloaded
  from the publisher, checksum verified, then **prepared once** with the guest agent inside, so every VM made from it
  is ready in seconds and works without Internet. Importable from a file on a site without Internet.
- **ISOs** for everything else (Windows Server and desktop, Red Hat, Kali, Linux with a desktop…): Hyperlite
  recognizes the system **from the ISO's content** (not its name) and installs it unattended.
- **Templates** made by the administrators from their own VMs, with their software and settings.

### 4.2 The customization sheet

The same fields whatever the system, saved and reused like VMware's specifications ("LAN servers", "Test"):
name and domain, address (automatic, or fixed with gateway and DNS), account, password, SSH keys, time zone,
keyboard, language, Windows product key (optional: empty means the company's KMS server or the trial period),
joining the Windows domain, commands to run at first start. Hyperlite writes them in each installer's own language
(cloud-init, Kickstart, preseed, autoinstall, Windows unattend); the user never sees those.

### 4.3 Sizes

Sizes defined by the administrator (Small 2 CPU / 4 GB, Medium…), plus a "custom" size the administrator can allow
or not.

### 4.4 "VM ready"

A creation is one task that goes through visible steps: disk, start, network, configuration, **ready**. "Ready" means
the guest agent answers and the system says its configuration is finished. If a step does not finish in time, the
task says which one. The API and Terraform wait for "ready".

### 4.5 Fast and economical

- A new VM's disk is an instant copy that only stores its differences: a linked qcow2 file (as today on directories
  and NFS) or a ZFS clone (new; today ZFS copies everything).
- Several VMs requested at once are created in parallel, a few at a time per node.

## 5. Part B: self-service for internal teams (the Aria part)

- **Offers**: the administrator publishes offers, each a system (or a template), a size or a choice of sizes, and a
  customization sheet; for example "Linux test server", "Windows Server with domain".
- **Teams and quotas**: each team (an existing user group) gets limits: number of VMs, CPUs, memory, disk. A request
  over the quota is refused with the reason.
- **Approval, offer by offer**: an offer can require a manager's approval; the request waits, the manager approves or
  refuses from the dashboard, and is notified.
- **Lifetime**: an offer can give its VMs a lifetime (for example 30 days, with a reminder before), extendable.
- **A private network per team, optional**: the administrator can give a team its own isolated network.
- Team members see and manage their own VMs only, with the rights the offer gives them (start, stop, console,
  snapshots…), through the existing permission system.
- No billing (no external customers): a usage summary per team is enough.

## 6. Decisions (Antho, 2026-10-12)

1. Every system, from the first version: Linux servers, Windows Server, Windows desktop, Linux with a desktop.
2. Self-service for internal teams; no external customers, no billing.
3. Predefined sizes, plus a custom size the administrator allows or not.
4. Windows: an optional product key; empty means KMS or the trial period.
5. A private network per team: possible, optional.
6. Approval: optional, offer by offer.

## 7. Order of the work (each step one PR, tested on the lab with the real installers)

1. The library and ready images prepared with the agent; the "ready" state.
2. The customization sheet and fixed addresses, for the ready images and the existing Kickstart and autoinstall.
3. Unattended installation from any ISO, recognized by its content: Debian and Kali (preseed), Ubuntu desktop,
   **Windows Server and desktop** (unattend, drivers, agent, product key, domain join).
4. Templates in the shared library, deployed with a customization sheet; ZFS clones.
5. Sizes and offers; many VMs at once.
6. Teams, quotas, approval, lifetime, private networks: the self-service.

Each step works on its own: after step 3, every system is created automatically, even before the catalogue exists.

## 8. What stays

Manual installation from an ISO stays available, as does disk import. The current API fields keep working (a
shortcut for "Debian image + default sheet"), so scripts written for 1.0 do not break.
