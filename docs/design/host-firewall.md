# Design: the nodes' own firewall, at the cluster's and each node's level (#422)

Status: **first part done** (the nodes' input rules, security groups and IP sets). Second part: VMs and networks
referencing the security groups.

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

VMs and networks referencing the security groups (the second part), aliases (names for addresses: IP sets cover the
need), IPv6 sources, output and forward rules for the nodes.
