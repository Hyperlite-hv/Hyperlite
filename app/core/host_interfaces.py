"""The host's network interfaces a bridged libvirt network can use, read without changing anything.

A "bridge" network can sit on:
- an existing Linux bridge (br0, vmbr0...): libvirt attaches VMs to it; the host and the VMs reach each other;
- a physical NIC, a bond or a VLAN interface directly: libvirt uses macvtap. The VMs are on the LAN like any
  machine, with no change to the host's own network configuration, but the host itself cannot reach them over
  that interface (a macvtap limitation), so Hyperlite cannot learn their address from it or open its SSH terminal.

Wi-Fi cards are listed but not offered: a Wi-Fi link accepts frames only from its own address, so neither a bridge
nor macvtap works on it. Loopback, VPN tunnels, VM taps and the bridges of libvirt's own networks are left out.
"""

import json
import re
import subprocess
from pathlib import Path

SYS_NET = Path("/sys/class/net")
# Interfaces libvirt or a hypervisor creates for itself: never a place to attach a new network.
_OWN = re.compile(r"^(lo|virbr.*|vnet\d+|macvtap\d+|veth.*|tap.*|docker\d*|cni.*|flannel.*)$")
USABLE = ("pont", "physique", "bond", "vlan")


def _kind(path):
    """The interface's kind from sysfs: its uevent DEVTYPE (bridge, bond, vlan, wlan), or a hardware device."""
    if (path / "tun_flags").exists():  # VPN tunnels (tailscale0, wg...) and taps
        return None
    try:
        uevent = (path / "uevent").read_text()
    except OSError:
        uevent = ""
    devtype = next((line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("DEVTYPE=")), "")
    if devtype == "bridge" or (path / "bridge").is_dir():
        return "pont"
    if devtype == "bond" or (path / "bonding").is_dir():
        return "bond"
    if devtype == "vlan":
        return "vlan"
    if devtype == "wlan" or (path / "wireless").is_dir() or (path / "phy80211").exists():
        return "wifi"
    if (path / "device").exists():
        return "physique"
    return None


def _addresses():
    """{interface: ["192.168.3.42/24", ...]} from `ip -j`; empty when ip is unavailable."""
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, timeout=10, check=True)
        data = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return {i["ifname"]: [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])] for i in data}


def list_host_interfaces(sys_net=None, addresses=None):
    sys_net = SYS_NET if sys_net is None else sys_net
    addresses = _addresses() if addresses is None else addresses
    result = []
    for path in sorted(Path(sys_net).iterdir()):
        name = path.name
        if _OWN.match(name):
            continue
        kind = _kind(path)
        if kind is None:
            continue

        def read(attr, p=path):
            try:
                return (p / attr).read_text().strip()
            except OSError:
                return None

        result.append(
            {
                "nom": name,
                "type": kind,
                "etat": read("operstate"),
                "mac": read("address"),
                "adresses": addresses.get(name, []),
                "utilisable": kind in USABLE,
            }
        )
    # Bridges first (the host and its VMs reach each other), then NICs, bonds, VLANs; Wi-Fi last.
    order = {"pont": 0, "physique": 1, "bond": 2, "vlan": 3, "wifi": 4}
    return sorted(result, key=lambda i: (order[i["type"]], i["nom"]))


def find(name, sys_net=None):
    return next((i for i in list_host_interfaces(sys_net, addresses={}) if i["nom"] == name), None)
