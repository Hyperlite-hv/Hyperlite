# Hook scripts on a VM's lifecycle (#515)

Proxmox VE runs a hook script per VM at four phases: `pre-start`, `post-start`, `pre-stop` and `post-stop`, called with
the VM's id and the phase; a failing `pre-start` stops the start. The script lives in a storage's `snippets` and only
`root@pam` attaches one. vSphere has no host-side script per VM: what runs on the host is VMware's own, and a guest's
power scripts run inside the guest (VMware Tools). Antho asked for the best of both, adapted to Hyperlite.

What Hyperlite takes:

- From Proxmox VE: the four phases, the arguments (`<vm> <phase>`), a failing `pre-start` or `pre-stop` refusing the
  action, the scripts in a library on the node that only root fills.
- From vSphere: nothing that runs as root comes from the web. The dashboard picks a script that root placed on the node;
  it never uploads or edits one, so a stolen administrator account does not become root on the nodes.

## What libvirt does (checked on libvirt 10.0, this repository's lab)

A script in `/etc/libvirt/hooks/qemu.d/` is called as root by libvirtd, with the VM's name, an operation and a
sub-operation, and the VM's XML on its standard input:

- start: `prepare begin`, `start begin`, `started begin`;
- stop (shutdown, destroy, the guest powering off): `stopped end`, `release end`;
- a hook failing at `prepare` refuses the start, and its standard error is in libvirt's error ("Hook script execution
  failed: ... unexpected exit status 1: <stderr>"). libvirt then still calls `stopped` and `release`;
- there is no operation before a stop.

## Design

**The library.** `/var/lib/hyperlite/snippets/`, next to `template/iso` and `dump`. A script is usable when it is a
plain file (not a link), owned by root, executable, neither group- nor world-writable, in a directory with the same
rules, its name made of `[A-Za-z0-9._-]` (at most 100 characters). The dashboard lists the scripts of the VM's node
with the reason an unusable one is refused. Root puts them there (scp, the console, configuration management).

**Attaching.** An administrator picks a script for a VM (audited). The choice lives in the VM's own metadata
(`urn:hyperlite:hookscript:1`), as the VLANs do: it follows the VM in a migration and a clone, and the hook
reads it from the XML libvirt hands it, without the database.

**Running.** Hyperlite installs one libvirt hook, `qemu.d/hyperlite-hookscript`, with the VLAN one (one installer for
both, `scripts/ensure-libvirt-hooks.sh`). It covers every start and stop, whoever asks: the API, the ordered boot, HA,
replication, `virsh`.

| Phase | Run by | When | A failure |
|---|---|---|---|
| `pre-start` | the libvirt hook | `prepare begin` | refuses the start; the script's error is the start's error |
| `post-start` | the libvirt hook | `started begin` | recorded |
| `pre-stop` | Hyperlite | before a shutdown, a stop or a forced restart asked through Hyperlite | refuses the shutdown or the stop; a forced stop runs it and goes on |
| `post-stop` | the libvirt hook | `release end`, only after the VM had started | recorded |

A guest powering itself off gets no `pre-stop` (as in Proxmox VE: there is nothing before it), but always gets its
`post-stop`. A start refused at `pre-start` gets no `post-stop`.

The script runs with `<vm> <phase>`, `HYPERLITE_VM` and `HYPERLITE_PHASE` in its environment, the VM's XML on its
standard input, at most 300 s (then killed with everything it started, which is a failure). Before running, the hook
checks the script again with the library's rules: a script made writable by others meanwhile is not run, and a
`pre-start` then fails. Its output goes to a file, not a pipe: a `post-start` that starts a daemon would otherwise keep
the pipe open and hold the VM's start in libvirt for as long as the daemon lives (checked in the lab: the start
returns in 0.6 s with the daemon running).

**Output.** Each run (phase, exit code, duration, the last 16 KiB of its output) is kept in
`/var/lib/hyperlite/hookscript-runs/<vm>.jsonl` on the node (the last 50 runs, root's only) and sent to the journal
(`journalctl -t hyperlite-hookscript`). A start, stop or restart asked through Hyperlite writes its runs into its task
log; the VM's page shows the last runs.

**Another node.** For a VM on another member, the API reads the library and the runs, and runs `pre-stop`, through
the same module on that node over SSH (`python -m app.core.hookscript list|check|runs|run`). A script must be in the
library of every node the VM may run on. What libvirt calls on each side of a live migration was not checked here (one
node): to check on two nodes before relying on it.

**Checked in the lab** (libvirt 10.0, QEMU 8.2): the phases and their arguments, the XML on standard input, a refused
start and its message, no `post-stop` after it, a script made writable by others refused, the API (attach, a refused
stop, a forced stop, a refused start answered 409, the task log), and the dashboard card in English and French. Not
checked: a live migration between two nodes (this lab has one), and a VM on another node.

**Scope.** VMs. Containers (libvirt's `lxc` hook) can follow the same way later.

**Privilege separation (#511).** The libvirt hook runs in libvirtd, so it needs nothing of the API. Only `pre-stop` runs
from the API, with the VM domain's other root actions, and moves with them to hyperlite-daemon.
