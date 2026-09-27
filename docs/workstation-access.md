# Access from a workstation

The web console (VNC and SSH terminal in the browser) needs nothing on the user's computer. For daily work, users often prefer their own tools: a local terminal with their SSH keys and settings, or the Windows remote desktop client. The `hyperlite` client gives them that without making the VM networks reachable from the office network.

## How it works

```
workstation ssh / mstsc ⇄ hyperlite ══ WebSocket over the server's HTTPS port ══ Hyperlite server ⇄ VM port
```

1. `hyperlite` asks the API for a **ticket** for one port of one VM. The ticket is single use and valid for 30 seconds.
2. It opens a WebSocket to the server with that ticket. The server connects to the VM and relays the bytes both ways.
3. The guest protocol is relayed untouched: SSH and RDP stay encrypted from the workstation to the VM. Users sign in to the guest with the guest's own credentials, as usual.

The server only needs its usual HTTPS port. VMs on NAT or isolated networks are reachable this way, and nothing about their network is exposed.

This is the same model as the identity-aware tunnels of the public clouds (Google Cloud IAP, Azure Bastion native client, AWS Session Manager, Teleport).

## For users

In a VM's **Console** tab, open **From your workstation**.

**If the VM is on a bridged network of the site**, it is reachable like any machine of your network. The panel gives the `ssh user@address` command to copy and, for a Windows VM, a remote desktop file (`.rdp`) that Windows opens by itself. Nothing to install: this is what the clouds' "Connect" button offers when the company network is linked to the VMs.

**Otherwise (or from anywhere), through Hyperlite:**

1. **Once per computer:** download `hyperlite` from the panel and open it (a double-click). It registers the `hyperlite://` links for your account only (Windows: `HKCU`; Linux: `xdg-mime`), nothing else.
2. **Click Open in a terminal (SSH)** or **Remote desktop**. The browser asks to open hyperlite.
   - The first time, the terminal offers to sign this computer in: press Enter.
   - A page of the web interface opens. Check that its code is the one shown in the terminal, then click **Approve**. The sign-in is the usual one (password and 2FA, or SSO).
   - The SSH session then starts, in PowerShell on Windows, or the remote desktop opens.
3. **The next times, the click connects directly.** The computer stays signed in for 30 days by default and appears in **Account security › API tokens**, where it can be revoked. When the session expires, the next click asks for the approval again.

The same works from a terminal, with the commands below. `hyperlite login <server>` signs in explicitly (for example before a script).

### Commands

| Command | Effect |
|---|---|
| `hyperlite ssh <vm>` | SSH session with the workstation's `ssh`, as the user created with the VM. Use `user@vm` to choose the user. Other arguments are passed to `ssh` (`-L`, a remote command...). |
| `hyperlite rdp <vm>` | Remote desktop to a Windows VM. On Linux and macOS, prints a local address to open with your RDP client. |
| `hyperlite tunnel <vm> <port> [--listen 127.0.0.1:2222]` | Forward a VM port to a local port, for any other tool. |
| `hyperlite tunnel <vm> 22 --stdio` | For `ProxyCommand` in `~/.ssh/config`. |
| `hyperlite vms`, `status`, `logout` | List the VMs, the servers signed in to, sign out and revoke the token. |
| `hyperlite setup` | Register the `hyperlite://` links again (what a double-click on the program does). |

To use plain `ssh web-01`, add this to `~/.ssh/config`:

```
Host web-01
  ProxyCommand hyperlite tunnel --stdio --server https://hyperlite.example.com %n 22
  HostKeyAlias web-01.hyperlite.example.com.hyperlite
```

## For administrators

### Permissions

A tunnel needs the **`vm.tunnel`** privilege on the VM:
- administrators have it on every VM;
- the scoped **Operator** and **Manager** roles include it;
- custom roles can include it;
- observers and the **Reader** role do not have it.

### Settings

These are environment variables of the service, set in `.env` (see [configuration.md](configuration.md)).

| Variable | Default | Effect |
|---|---|---|
| `HYPERLITE_TUNNEL_PORTS` | `22,3389` | Guest ports that can be reached. Empty: tunnels are disabled. |
| `HYPERLITE_TUNNEL_IDLE_TIMEOUT_S` | `3600` | A tunnel without traffic is closed after this many seconds. |
| `HYPERLITE_TUNNEL_MAX_PER_USER` | `20` | Open tunnels per user. |
| `HYPERLITE_CLI_TOKEN_DAYS` | `30` | Lifetime of a workstation token. |

### Audit

The audit log records:
- workstation approvals and refusals (`cli_approve`, `cli_deny`) and sign-ins (`cli_login`);
- every ticket (`create_tunnel_ticket`);
- every tunnel (`tunnel_open`, `tunnel_close`, with its duration and the bytes sent and received).

Each entry includes the client address.

### Deployment on workstations

- **The executable:** it is the same for every server. It can be signed and distributed with the usual tools (Intune, GPO, SCCM, a package manager). The server shows the SHA-256 of each build it serves.
- **Corporate proxy:** `HTTPS_PROXY` and `NO_PROXY` are honoured.
- **Certificates:** the server certificate is verified against the system trust store, so a certificate from the company's CA works as is. With a self-signed certificate, `hyperlite login` shows its SHA-256 fingerprint and remembers it once the user confirms it, like an SSH host key.
- **Scripts and CI:** set `HYPERLITE_SERVER` and `HYPERLITE_TOKEN` (an API token). No sign-in is needed.
- **Configuration file:** per user (`%AppData%\hyperlite\config.json`, `~/.config/hyperlite/config.json`), readable by its owner only. `HYPERLITE_CONFIG` chooses another file.

### Security notes

- **Tickets:** single use, bound to one VM and one port, valid for 30 seconds, and only issued after the permission check.
- **Workstation approval:** only a web session can approve a workstation; an API token cannot. The page asks the user to compare the code with the one shown in the terminal, so a link sent by someone else cannot sign their workstation in.
- **`hyperlite://` links:**
  - a link to a server the computer is not signed in to only goes further after the person confirms that server in the terminal and approves the sign-in in that server's web interface;
  - VM and user names must match strict patterns before anything is started;
  - the terminal is started without a shell interpreting the link.

## Limits

- **VMs of remote nodes:** tunnels reach the VMs of the node that runs the Hyperlite service. VMs of other cluster nodes are not supported yet.
- **VM address:** the VM must have an IPv4 address known to libvirt (DHCP lease, or the ARP table of the host).
- **macOS links:** `hyperlite://` links are not registered on macOS yet; the commands work.
