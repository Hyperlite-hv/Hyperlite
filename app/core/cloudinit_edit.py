"""Cloud-init after creation: change the account's password and SSH keys of a VM made from a cloud image.

The VM keeps its cloud-init drive (<name>-cloudinit.iso). Writing a new one with a new instance id makes cloud-init
apply it at the next boot, as Proxmox does with its regenerated drive. What must not change with it:
- the guest's SSH host keys: `ssh_deletekeys: false`, otherwise every client would see a changed host key;
- Hyperlite's automation key, which automation jobs use to reach the VM: always kept in the keys.
A password goes through `chpasswd` (applied to an existing account; the `users` module only creates accounts) and is
never stored by Hyperlite: the drive holds it until the next change, like at creation.
"""

import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import libvirt
import yaml

from app.core.vm_builder import (
    generated_drives,
    get_or_create_automation_pubkey,
    hash_guest_password,
    protect_drive,
)


def _store():
    from app.repositories import registry

    return registry.objects().sync


USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEY_RE = re.compile(
    r"^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com) [A-Za-z0-9+/=]+( [^\r\n]{0,200})?$"
)
MAX_KEYS = 20

# What a VM's drive may also carry (CUSTOM), as vSphere's guest customisation and Proxmox VE's cloud-init options: the
# host name and domain, static addresses per interface (by MAC address, the others on DHCP), resolvers, and a
# cloud-config of the user's added to Hyperlite's own.
CUSTOM = ("hostname", "domain", "network", "dns", "dns_search", "user_data")
LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
MAX_USER_DATA = 16 * 1024
# The keys of the cloud-config Hyperlite writes itself (the account, the host name): a user's cloud-config that set them
# would silently replace them, or be replaced, depending on the order cloud-init reads them.
RESERVED_KEYS = frozenset(
    {
        "hostname",
        "fqdn",
        "prefer_fqdn_over_hostname",
        "manage_etc_hosts",
        "users",
        "user",
        "chpasswd",
        "password",
        "ssh_pwauth",
        "ssh_authorized_keys",
        "ssh_deletekeys",
        "disable_root",
    }
)


class CloudInitError(ValueError):
    pass


def _drive(domain, vm_name):
    root = ET.fromstring(domain.XMLDesc(0))
    for disk, path in generated_drives(root, vm_name):
        if path.name == f"{vm_name}-cloudinit.iso":
            return path, disk
    return None, None


def drive_path(domain, vm_name):
    """The VM's cloud-init drive when it has one Hyperlite made, else None (a VM installed from an ISO)."""
    return _drive(domain, vm_name)[0]


def reload_media(domain, vm_name):
    """A running VM keeps the old drive open, even across a guest reboot (the QEMU process stays): eject it and
    insert the new file, so the next boot of the guest reads it."""
    if not domain.isActive():
        return False
    path, disk = _drive(domain, vm_name)
    if disk is None:
        return False
    disk.remove(disk.find("source"))
    domain.updateDeviceFlags(ET.tostring(disk, encoding="unicode"), libvirt.VIR_DOMAIN_AFFECT_LIVE)
    ET.SubElement(disk, "source", {"file": str(path)})
    domain.updateDeviceFlags(ET.tostring(disk, encoding="unicode"), libvirt.VIR_DOMAIN_AFFECT_LIVE)
    return True


def empty_custom():
    return {"hostname": None, "domain": None, "network": [], "dns": [], "dns_search": [], "user_data": None}


def get_custom(vm_name):
    """The customisation last written on the VM's drive (empty_custom() when none)."""
    row = _store().cloudinit_custom(vm_name)
    return {**empty_custom(), **(json.loads(row["data"]) if row else {})}


def get_state(vm_name):
    """What Hyperlite last wrote: {"user", "ssh_keys", "modified_at"} and the customisation (CUSTOM); no password, never
    stored."""
    row = _store().cloudinit(vm_name)
    if not row:
        return None
    return {
        "user": row["username"],
        "ssh_keys": json.loads(row["ssh_keys"] or "[]"),
        **get_custom(vm_name),
        "modified_at": row["updated_at"],
    }


def save_state(vm_name, username, keys, custom):
    """The account, keys and customisation last written on the VM's drive, kept in the database (the API's)."""
    now = datetime.now(UTC).isoformat()
    _store().save_cloudinit(vm_name, username, json.dumps(keys), now)
    _store().save_cloudinit_custom(vm_name, json.dumps(custom or empty_custom()), now)


def delete_state(vm_name):
    _store().delete_cloudinit(vm_name)


def validate(username, password, keys):
    if not USER_RE.match(username or ""):
        raise CloudInitError("Invalid user name: lowercase letters, digits, '_' or '-', up to 32 characters")
    if password is not None:
        if len(password) < 8:
            raise CloudInitError("The password needs at least 8 characters")
        if any(c in password for c in "\r\n"):
            raise CloudInitError("The password must not contain a line break")
    cleaned = []
    for raw in keys or []:
        if any(c in str(raw).strip() for c in "\r\n"):
            # Two keys pasted as one entry: refused rather than merged into one odd key.
            raise CloudInitError("One SSH key per entry: this one spans several lines")
        key = " ".join(str(raw).split())
        if not key:
            continue
        if not KEY_RE.match(key):
            raise CloudInitError(f"Not an SSH public key: {key[:40]}…")
        if key not in cleaned:
            cleaned.append(key)
    if len(cleaned) > MAX_KEYS:
        raise CloudInitError(f"At most {MAX_KEYS} SSH keys")
    return cleaned


def _ip_list(values, what):
    out = []
    for raw in values or []:
        try:
            out.append(str(ipaddress.ip_address(str(raw).strip())))
        except ValueError:
            raise CloudInitError(f"{what}: '{raw}' is not an IP address") from None
    return out


def _domain(value, what):
    labels = str(value).strip().rstrip(".").split(".")
    if len(".".join(labels)) > 253 or not all(LABEL_RE.match(label) for label in labels):
        raise CloudInitError(f"{what}: '{value}' is not a domain name")
    return ".".join(labels).lower()


def validate_custom(custom, macs):
    """The customisation, checked and in its stored form; raises CloudInitError. `macs`: the VM's interfaces, in order;
    an entry of `network` without a MAC address is the first interface."""
    out = empty_custom()
    if custom.get("hostname"):
        if not LABEL_RE.match(custom["hostname"]):
            raise CloudInitError("hostname: letters, digits and '-', up to 63 characters, as a DNS label")
        out["hostname"] = custom["hostname"].lower()
    if custom.get("domain"):
        out["domain"] = _domain(custom["domain"], "domain")
    seen = set()
    for i, entry in enumerate(custom.get("network") or []):
        what = f"network[{i}]"
        mac = (entry.get("mac") or (macs[0] if macs else "")).lower()
        if not MAC_RE.match(mac) or mac not in [m.lower() for m in macs]:
            raise CloudInitError(f"{what}: the VM has no interface with the MAC address '{mac or entry.get('mac')}'")
        if mac in seen:
            raise CloudInitError(f"{what}: the interface {mac} is configured twice")
        seen.add(mac)
        addresses = []
        for raw in entry.get("addresses") or []:
            try:
                iface = ipaddress.ip_interface(str(raw).strip())
            except ValueError:
                raise CloudInitError(f"{what}: '{raw}' is not an address with its prefix (10.0.20.11/24)") from None
            if "/" not in str(raw):
                raise CloudInitError(f"{what}: '{raw}' needs its prefix length (10.0.20.11/24)")
            addresses.append(iface)
        if not addresses:
            raise CloudInitError(f"{what}: give at least one address, or leave the interface out to keep DHCP")
        gateway = None
        if entry.get("gateway"):
            [gateway] = _ip_list([entry["gateway"]], f"{what}.gateway")
            gw = ipaddress.ip_address(gateway)
            if not any(gw in a.network for a in addresses if a.version == gw.version):
                raise CloudInitError(f"{what}: the gateway {gateway} is outside the interface's networks")
        out["network"].append({"mac": mac, "addresses": [str(a) for a in addresses], "gateway": gateway})
    out["dns"] = _ip_list(custom.get("dns"), "dns")
    out["dns_search"] = [_domain(d, "dns_search") for d in custom.get("dns_search") or []]
    if custom.get("user_data"):
        text = custom["user_data"]
        if len(text.encode()) > MAX_USER_DATA:
            raise CloudInitError(f"user_data: at most {MAX_USER_DATA // 1024} KiB")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise CloudInitError(f"user_data is not valid YAML: {str(e).splitlines()[0]}") from None
        if not isinstance(data, dict):
            raise CloudInitError("user_data: a cloud-config mapping (packages:, runcmd:, write_files:...)")
        reserved = sorted(RESERVED_KEYS & set(data))
        if reserved:
            raise CloudInitError(
                f"user_data: {', '.join(reserved)} {'is' if len(reserved) == 1 else 'are'} written by Hyperlite "
                "(the account, the keys, the host name): set them with the fields of their own"
            )
        out["user_data"] = text
    return out


def network_config(custom, macs):
    """A network configuration (version 2) for the drive, or None when every interface stays on DHCP with its own
    resolvers. Every interface is listed: cloud-init configures only those a given configuration names."""
    if not custom["network"] and not custom["dns"] and not custom["dns_search"]:
        return None
    static = {entry["mac"]: entry for entry in custom["network"]}
    ethernets = {}
    for i, mac in enumerate(macs):
        eth = {"match": {"macaddress": mac.lower()}}
        entry = static.get(mac.lower())
        if entry:
            eth["addresses"] = entry["addresses"]
            if entry["gateway"]:
                eth["routes"] = [
                    {
                        # "default" is not read by every cloud-init: the routes in full.
                        "to": "0.0.0.0/0" if ipaddress.ip_address(entry["gateway"]).version == 4 else "::/0",
                        "via": entry["gateway"],
                    }
                ]
        else:
            eth["dhcp4"] = True
        if custom["dns"] or custom["dns_search"]:
            eth["nameservers"] = {"addresses": custom["dns"], "search": custom["dns_search"]}
            if not entry:
                eth["dhcp4-overrides"] = {"use-dns": False}
        ethernets[f"nic{i}"] = eth
    return {"version": 2, "ethernets": ethernets}


def _yaml_str(value):
    """A single-quoted YAML scalar (quotes doubled): nothing typed by a user is interpreted as YAML."""
    return "'" + str(value).replace("'", "''") + "'"


def user_data(vm_name, username, password, keys, automation_key, custom=None):
    custom = custom or empty_custom()
    all_keys = [*keys, *([automation_key] if automation_key and automation_key not in keys else [])]
    hostname = custom["hostname"] or vm_name
    lines = [
        "#cloud-config",
        f"hostname: {_yaml_str(hostname)}",
        *([f"fqdn: {_yaml_str(hostname + '.' + custom['domain'])}"] if custom["domain"] else []),
        "manage_etc_hosts: true",
        # A new instance id makes cloud-init run again; the host keys must stay those clients already know.
        "ssh_deletekeys: false",
        "users:",
        f"  - name: {_yaml_str(username)}",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    shell: /bin/bash",
        "    lock_passwd: false",
    ]
    if all_keys:
        lines.append("    ssh_authorized_keys:")
        lines += [f"      - {_yaml_str(k)}" for k in all_keys]
    if password is not None:
        lines += [
            "chpasswd:",
            "  expire: false",
            "  users:",
            f"    - name: {_yaml_str(username)}",
            f"      password: {_yaml_str(hash_guest_password(password))}",
            "      type: hash",  # never in clear on the drive (vm_builder.hash_guest_password); lower case: cloud-init ignores "HASH"
            "ssh_pwauth: true",
        ]
    text = "\n".join(lines) + "\n"
    if custom["user_data"]:
        # The user's keys after Hyperlite's own, none of them the same (validate_custom): one cloud-config.
        text += yaml.safe_dump(yaml.safe_load(custom["user_data"]), default_flow_style=False, sort_keys=False)
    return text


def interface_macs(domain):
    """The MAC addresses of the VM's interfaces, in order."""
    root = ET.fromstring(domain.XMLDesc(0))
    return [m.get("address").lower() for m in root.findall("./devices/interface/mac") if m.get("address")]


def rewrite_drive(vm_name, iso_path, username, password, keys, custom=None, macs=()):
    """write_drive, then save_state."""
    write_drive(vm_name, iso_path, username, password, keys, custom, macs)
    save_state(vm_name, username, keys, custom)


def write_drive(vm_name, iso_path, username, password, keys, custom=None, macs=()):
    """Write the new drive next to the old one, then swap them: a failure leaves the old drive in place. `custom`: the
    customisation, already through validate_custom (None: none); `macs`: the VM's interfaces, for its network
    configuration. Root's work (hyperlite-daemon, app/daemon/ops/vm.py): the drive is private to root, and holds the
    automation key."""
    custom = custom or empty_custom()
    automation_key = get_or_create_automation_pubkey()
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-edit-"))
    try:
        (workdir / "user-data").write_text(user_data(vm_name, username, password, keys, automation_key, custom))
        hostname = custom["hostname"] or vm_name
        (workdir / "meta-data").write_text(f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {hostname}\n")
        command = ["cloud-localds"]
        net = network_config(custom, list(macs))
        if net is not None:
            (workdir / "network-config").write_text(yaml.safe_dump(net, default_flow_style=False, sort_keys=False))
            command.append(f"--network-config={workdir / 'network-config'}")
        tmp_iso = iso_path.with_name(f".{iso_path.name}.new")
        result = subprocess.run(
            [*command, str(tmp_iso), str(workdir / "user-data"), str(workdir / "meta-data")],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            tmp_iso.unlink(missing_ok=True)
            raise CloudInitError(f"Cannot build the cloud-init drive: {result.stderr.strip()[:300]}")
        if iso_path.exists():
            st = iso_path.stat()
            os.chown(tmp_iso, st.st_uid, st.st_gid)
        protect_drive(tmp_iso)  # private to root, whatever the old drive's mode was
        tmp_iso.replace(iso_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
