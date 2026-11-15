"""Connection diagnosis (docs/design/console-2.md, section 9): can VM A reach B (a VM or an address) on a port?

Answered from three angles at once:
1. Hyperlite's own firewalls on the path, read from their rules as they are applied: A's VM firewall outbound, the
   networks' firewalls (the bridge's FORWARD chain), B's security groups and VM firewall inbound. The first rule that
   matches decides, as in the firewall itself, and the one that would drop the packet is named.
2. Where they are: their networks and addresses.
3. A real try from inside A through its agent (a TCP connect with a 5-second limit), and whether B's guest has
   something listening on the port (when B is a VM with an agent and the account may run commands in it).
"""

import ipaddress
import logging
import re
import xml.etree.ElementTree as ET

import libvirt

from app.core import privileged

logger = logging.getLogger(__name__)

TRY_SH = r"""
if command -v bash >/dev/null 2>&1; then
  out=$(timeout 5 bash -c "</dev/tcp/{ip}/{port}" 2>&1); rc=$?
  if [ $rc -eq 0 ]; then echo OPEN; elif [ $rc -eq 124 ]; then echo TIMEOUT; elif echo "$out" | grep -qi refused; then echo REFUSED; else echo "ERROR $out"; fi
elif command -v nc >/dev/null 2>&1; then
  out=$(nc -z -v -w 5 {ip} {port} 2>&1); rc=$?
  if [ $rc -eq 0 ]; then echo OPEN; elif echo "$out" | grep -qi refused; then echo REFUSED; elif echo "$out" | grep -qi 'timed out'; then echo TIMEOUT; else echo "ERROR $out"; fi
else echo NOTOOL; fi
"""
TRY_PS = (
    "$c = New-Object Net.Sockets.TcpClient; try {{ $t = $c.ConnectAsync('{ip}', {port}); "
    "if ($t.Wait(5000)) {{ if ($c.Connected) {{ 'OPEN' }} else {{ 'REFUSED' }} }} else {{ 'TIMEOUT' }} }} "
    "catch {{ if ($_.Exception.ToString() -match 'refused|actively') {{ 'REFUSED' }} else {{ 'ERROR ' + $_.Exception.Message }} }} "
    "finally {{ $c.Close() }}"
)
LISTEN_SH = (
    "if command -v ss >/dev/null 2>&1; then ss -H -ltn 'sport = :{port}' | grep -q . && echo YES || echo NO; "
    "elif command -v netstat >/dev/null 2>&1; then netstat -ltn | awk '{{print $4}}' | grep -q ':{port}$' && echo YES || echo NO; "
    "else echo NOTOOL; fi"
)
LISTEN_PS = "if (Get-NetTCPConnection -State Listen -LocalPort {port} -ErrorAction SilentlyContinue) {{ 'YES' }} else {{ 'NO' }}"


class DiagnoseError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _interfaces(domain):
    """[{"type", "network", "bridge"}] of a VM's interfaces."""
    root = ET.fromstring(domain.XMLDesc(0))
    out = []
    for iface in root.findall("./devices/interface"):
        src = iface.find("source")
        out.append(
            {
                "type": iface.get("type"),
                "network": src.get("network") if src is not None else None,
                "bridge": src.get("bridge") if src is not None else None,
            }
        )
    return out


def _vm_firewall(conn, name):
    from app.core import vm_firewall

    try:
        return vm_firewall.parse(conn.nwfilterLookupByName(vm_firewall.filter_name(name)).XMLDesc(0))
    except libvirt.libvirtError:
        return {"default_policy": "accept", "rules": [], "groups": []}


def _port_ok(rule_port, port):
    return rule_port in (None, "") or int(rule_port) == port


def _proto_ok(rule_proto, protocol):
    return rule_proto in ("all", protocol)


def first_match(config, direction, protocol, port):
    """The VM firewall's verdict for a packet in `direction` ("in" or "out"): (action, rule index or None)."""
    for i, rule in enumerate(config["rules"]):
        if (
            rule["direction"] in (direction, "inout")
            and _proto_ok(rule["protocol"], protocol)
            and _port_ok(rule.get("port"), port)
        ):
            return rule["action"], i
    return config["default_policy"], None


def group_match(doc, groups, source_ip, protocol, port):
    """The security groups' verdict on traffic entering a VM: (action, group name, rule) or None when none matches.
    A rule whose source cannot be judged (A's address unknown) is reported as such."""
    from app.core import host_firewall

    for name in groups:
        for r in host_firewall.expand_group(doc, name):
            if not _proto_ok(r["protocol"], protocol):
                continue
            if r["ports"] is not None and not r["ports"][0] <= port <= r["ports"][1]:
                continue
            if r["net"] is not None:
                if source_ip is None:
                    return "unknown", name, r
                if ipaddress.ip_address(source_ip) not in r["net"]:
                    continue
            return r["action"], name, r
    return None


def _check(layer, verdict, detail, **extra):
    return {"layer": layer, "verdict": verdict, "detail": detail, **extra}


def _bridge_filtered():
    """Whether frames between two VMs of one bridge go through iptables (br_netfilter's setting on this node)."""
    try:
        with open("/proc/sys/net/bridge/bridge-nf-call-iptables") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def _exec(vm, node, shell, script, user):
    out = privileged.run("vm.agent_exec", user=user, vm=vm, node=node, shell=shell, script=script, timeout_s=20,
                         timeout=35)  # fmt: skip
    return (out.get("stdout") or "").strip()


def _shell(vm, node=None):
    """powershell for a Windows guest, sh otherwise (the agent's own os info)."""
    import json

    try:
        info = privileged.run("vm.agent_info", user="system", vm=vm, node=node)
    except Exception:  # no agent answer: the try says so with sh's own failure
        return "sh"
    return "powershell" if "windows" in json.dumps(info.get("os") or info).lower() else "sh"


def diagnose(user, from_vm, to, port, protocol="tcp", from_node=None, to_node=None):
    from app.core import guest_agent, host_firewall, network_firewall, permissions
    from app.core.libvirt_utils import open_conn
    from app.daemon.registry import DaemonError

    if protocol not in ("tcp", "udp"):
        raise DiagnoseError(422, "The protocol is tcp or udp")
    if not 1 <= int(port) <= 65535:
        raise DiagnoseError(422, "A port from 1 to 65535")
    if not permissions.has_privilege(user, from_vm, "vm.view"):
        raise DiagnoseError(403, f"Insufficient rights on VM '{from_vm}'")
    checks = []
    conn_a = open_conn(from_node, readonly=True) if from_node else open_conn(readonly=True)
    try:
        try:
            dom_a = conn_a.lookupByName(from_vm)
        except libvirt.libvirtError:
            raise DiagnoseError(404, f"VM '{from_vm}' not found") from None
        a_running = bool(dom_a.isActive())
        a_ip = guest_agent.ipv4(dom_a) if a_running else None
        a_ifaces = _interfaces(dom_a)
        fw_a = _vm_firewall(conn_a, from_vm)
    finally:
        conn_a.close()

    # The target: a VM of the node named, or an address.
    b_vm, b_ip, b_ifaces, fw_b, b_running = None, None, [], None, False
    try:
        b_ip = str(ipaddress.ip_address(to))
    except ValueError:
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", to or ""):
            raise DiagnoseError(422, "The target is a VM's name or an IPv4 address") from None
        if not permissions.has_privilege(user, to, "vm.view"):
            raise DiagnoseError(403, f"Insufficient rights on VM '{to}'") from None
        conn_b = open_conn(to_node, readonly=True) if to_node else open_conn(readonly=True)
        try:
            try:
                dom_b = conn_b.lookupByName(to)
            except libvirt.libvirtError:
                raise DiagnoseError(404, f"VM '{to}' not found") from None
            b_vm = to
            b_running = bool(dom_b.isActive())
            b_ip = guest_agent.ipv4(dom_b) if b_running else None
            b_ifaces = _interfaces(dom_b)
            fw_b = _vm_firewall(conn_b, to)
        finally:
            conn_b.close()

    a_nets = {i["network"] or i["bridge"] for i in a_ifaces}
    b_nets = {i["network"] or i["bridge"] for i in b_ifaces}
    same = bool(a_nets & b_nets)
    place = {
        "from": {
            "vm": from_vm,
            "node": from_node,
            "running": a_running,
            "ip": a_ip,
            "networks": sorted(n for n in a_nets if n),
        },
        "to": {
            "vm": b_vm,
            "node": to_node,
            "running": b_running,
            "ip": b_ip,
            "networks": sorted(n for n in b_nets if n),
        },
        "same_network": same,
    }
    if not a_running:
        checks.append(_check("state", "block", f"{from_vm} is not running"))
    if b_vm and not b_running:
        checks.append(_check("state", "block", f"{b_vm} is not running"))

    # 1. A's own firewall, outbound.
    action, idx = first_match(fw_a, "out", protocol, port)
    checks.append(_check("vm_firewall_out", "pass" if action == "accept" else "block",
                         f"{from_vm}: " + (f"rule {idx + 1}" if idx is not None else "default policy") + f" ({action})",
                         vm=from_vm, rule=idx))  # fmt: skip

    # 2. The networks' firewalls (the bridge's FORWARD chain): crossed between networks, and inside one network only
    #    when this node sends bridged frames through iptables.
    crossing = (not same) or _bridge_filtered()
    for net, direction in [(n, "out") for n in sorted(a_nets) if n] + [(n, "in") for n in sorted(b_nets) if n]:
        if same and not crossing:
            break
        cfg = network_firewall.get_network_firewall(net)
        if not cfg["rules"] and cfg["default_policy"] == "accept" and not cfg.get("groups"):
            continue
        act, i = first_match(cfg, direction, protocol, port)
        checks.append(_check("network_firewall", "pass" if act == "accept" else "block",
                             f"{net} ({direction}): " + (f"rule {i + 1}" if i is not None else "default policy") + f" ({act})",
                             network=net, rule=i))  # fmt: skip
    if same and not crossing:
        checks.append(_check("network_firewall", "pass", "Same network: its firewall is not crossed between its VMs"))

    # 3. B's security groups and own firewall, inbound.
    if fw_b is not None:
        doc = host_firewall.saved() or host_firewall.DEFAULT
        g = group_match(doc, fw_b.get("groups") or [], a_ip, protocol, port) if fw_b.get("groups") else None
        if g is not None:
            act, gname, _r = g
            checks.append(_check("security_group", {"accept": "pass", "unknown": "unknown"}.get(act, "block"),
                                 f"{b_vm}: security group {gname} ({act})", vm=b_vm, group=gname))  # fmt: skip
        if g is None or g[0] == "unknown":
            act, i = first_match(fw_b, "in", protocol, port)
            checks.append(_check("vm_firewall_in", "pass" if act == "accept" else "block",
                                 f"{b_vm}: " + (f"rule {i + 1}" if i is not None else "default policy") + f" ({act})",
                                 vm=b_vm, rule=i))  # fmt: skip

    # 4. A real try from inside A, and whether B listens.
    live, listening = None, None
    if a_running and b_ip and protocol == "tcp":
        if permissions.has_privilege(user, from_vm, "vm.exec"):
            try:
                shell = _shell(from_vm, from_node)
                script = (TRY_PS if shell == "powershell" else TRY_SH).format(ip=b_ip, port=int(port))
                live = (_exec(from_vm, from_node, shell, script, user["username"]).splitlines() or ["ERROR no answer"])[
                    -1
                ]
            except DaemonError as e:
                live = f"ERROR {e.detail}"
        else:
            live = "NORIGHT"
    if b_vm and b_running and protocol == "tcp":
        if permissions.has_privilege(user, b_vm, "vm.exec"):
            try:
                shell = _shell(b_vm, to_node)
                listening = (
                    _exec(
                        b_vm,
                        to_node,
                        shell,
                        (LISTEN_PS if shell == "powershell" else LISTEN_SH).format(port=int(port)),
                        user["username"],
                    ).splitlines()
                    or [""]
                )[-1]
            except DaemonError as e:
                listening = f"ERROR {e.detail}"
        else:
            listening = "NORIGHT"
    return {"place": place, "checks": checks, "try": live, "listening": listening,
            "verdict": verdict(checks, live, listening), "protocol": protocol, "port": int(port)}  # fmt: skip


def verdict(checks, live, listening):
    """What stops the connection, or "reachable"."""
    if any(c["verdict"] == "block" and c["layer"] == "state" for c in checks):
        return "not_running"
    if any(c["verdict"] == "block" for c in checks):
        return "blocked_by_rule"
    if live == "OPEN":
        return "reachable"
    if live == "REFUSED" or listening == "NO":
        return "nothing_listening"
    if live == "TIMEOUT":
        return "guest_firewall" if listening == "YES" else "no_answer"
    return "unknown"
