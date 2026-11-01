# Design: the nodes' own firewall, at the cluster's and each node's level (#422)

Status: **done**: the nodes' input rules, security groups and IP sets (first part), and VMs and networks referencing the
security groups (second part, section 6).

## 1. Where we were

A firewall per VM (a libvirt nwfilter) and per network (an iptables chain on its bridge), and nothing for the nodes
themselves: every port a node listens on was open to whoever reached it. Proxmox VE has rules at the datacenter, node
and VM levels, reusable security groups, IP sets and aliases.

## 2. What it does

One document, an application setting replicated to every member (`host_firewall`, `app/core/host_firewall.py`): on or
off, the input policy (drop or accept), the cluster's rules, each node's rules (after the cluster's), security groups
(rules called from one line) and IP sets (lists of IPv4 addresses and networks, used as a rule's source as `+name`).

hyperlite-daemon turns it into an nftables table of its own, `inet hyperlite`, with one input chain. It never touches
libvirt's or iptables' tables: an accept in ours ends nothing for them, a drop is final, so ours only narrows. Pinned
first, whatever the rules say: loopback, established connections, invalid ones dropped, ICMPv6 (IPv6 needs it), the
cluster's members between them (Corosync, SSH, the API, migrations, from the members' addresses in the cluster's
configuration) and DHCP and DNS from the bridges of libvirt's NAT and routed networks (their dnsmasq runs on the host).
The script removes the table and makes it again in one `nft -f` transaction: a node never runs half of it.

The first time it is turned on, the page adds starting rules (the dashboard on 8000, SSH and ping from anywhere), to
narrow down afterwards, so that turning it on locks nobody out.

## 3. Applying it without locking anyone out

1. The page sends the whole document; nftables checks the ruleset first (`nft -c`) and the page shows it.
2. Applied on the node the request reaches, the rollback timer armed before (`app/core/rollback_timer.py`, the same as
   the host's network): unless confirmed from the browser before the delay ends, the previous ruleset comes back.
3. Confirmed, the API saves the document. Every member's daemon checks the saved document every 15 s and applies it
   when its ruleset differs from what it runs; the members apply it with no timer, the pinned rules keeping the
   cluster together.
4. At its start, the daemon drops a change a reboot interrupted, and the follow loop brings back the saved rules (the
   table does not survive a reboot otherwise).

A rule that would lock the administrator out of another node than the one they confirm from is possible (a node rule);
they still reach it through any member, which forwards `?node=` requests.

## 4. Verified

The ruleset in a network namespace with the real nftables (1.1.3): sets, a group, a disabled rule, members, the VMs'
bridges; applied, removed, removed again. And the filtering itself between two namespaces: port 8000 open to all, 7000
open to an IP set the client belongs to, 6000 open to another network only and 9999 with no rule, both dropped.

## 5. Not yet

Aliases (names for addresses: IP sets cover the
need), IPv6 sources, output and forward rules for the nodes.

## 6. VMs and networks referencing the security groups

A VM's or a network's firewall (the same form) takes a list of security groups besides its own rules. A group's
rules have no direction: for a VM or a network they are the traffic that enters it, their source being where it
comes from (an IP set stands for one rule per network, a port list for one rule per port or range).

- **For a VM**, each group is a libvirt nwfilter of its own, `hyperlite-group-<name>`, that every node's daemon
  defines from the saved document (`host_firewall.sync_groups`, in the follow loop, when the groups or IP sets
  change). The VM's filter references it with `<filterref>`. libvirt merges the referenced rules by priority: the
  groups' are 100 to 299, the VM's own from 300, its default policy 999, so the groups come first. A changed group
  applies to the running VMs at once (libvirt re-instantiates the filters that reference it).
- **libvirt lets a filter another references be undefined** (only a running VM's interface holds it): checked on a
  production node. A stopped VM whose group filter was removed would not start again, so a group some VM's filter
  still references is kept as it was until no VM uses it.
- **For a network**, the groups' rules go into its iptables chain, before its own rules, as traffic leaving the
  bridge towards its VMs (`-o bridge`). They are stored with its rules (`{"group": name}` entries) and every node's
  daemon rebuilds the chains that use groups when the groups change.
- A VM's or a network's firewall refuses a group that does not exist (422); one deleted since shows as such on the
  form. `GET /cluster/firewall/groups` lists the groups for any signed-in account (names, comments, rules: an IP set
  shows by its name only).

Verified: a group's filter and a VM filter referencing it defined by libvirt 11.3 on a production node (not attached
to any VM, removed after), which also showed the undefine above.
