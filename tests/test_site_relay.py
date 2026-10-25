"""The target's guard on a live migration between clusters (app/core/site_relay.py): the source's libvirt may only
prepare and finish the migration of the incoming VM, on the volumes and networks this host prepared for it."""

import struct

import pytest

from app.core import site_relay
from app.core.site_relay import Refused

UUID = "6f1c1c39-6b0b-4b8a-9d4a-3f0e8e2b9a11"
EXPECTED = {
    "name": "web",
    "uuid": UUID,
    "volumes": {"/var/lib/libvirt/images/web-disk0.qcow2"},
    "networks": {"prod"},
    "emulators": {"/usr/bin/qemu-system-x86_64"},
    "sockets": "/run/hyperlite/migration/abc/",
}
# What a running VM's migratable definition looks like, its disk and card already on what the target prepared.
RUNNING = f"""<domain type='kvm' id='3'>
  <name>web</name><uuid>{UUID}</uuid>
  <metadata><libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0"><libosinfo:os id="http://debian.org/debian/13"/></libosinfo:libosinfo></metadata>
  <memory unit='KiB'>1048576</memory><vcpu placement='static'>2</vcpu>
  <os><type arch='x86_64' machine='pc-q35-10.0'>hvm</type><loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE_4M.fd</loader><nvram>/var/lib/libvirt/qemu/nvram/web_VARS.fd</nvram></os>
  <cpu mode='custom' match='exact'><model fallback='allow'>Skylake-Client-IBRS</model><feature policy='require' name='vmx'/></cpu>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/var/lib/libvirt/images/web-disk0.qcow2' index='1'/><backingStore/><target dev='sda' bus='scsi'/><alias name='scsi0-0-0-0'/></disk>
    <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><target dev='sdw' bus='sata'/><readonly/></disk>
    <interface type='network'><mac address='52:54:00:12:34:56'/><source network='prod' portid='1c0e' bridge='virbr1'/><target dev='vnet3'/><model type='virtio'/></interface>
    <serial type='pty'><source path='/dev/pts/4'/><target type='isa-serial' port='0'/></serial>
    <console type='pty' tty='/dev/pts/4'><source path='/dev/pts/4'/><target type='serial' port='0'/></console>
    <channel type='unix'><source mode='bind' path='/var/lib/libvirt/qemu/channel/target/domain-3-web/org.qemu.guest_agent.0'/><target type='virtio' name='org.qemu.guest_agent.0'/></channel>
    <graphics type='vnc' port='5900' autoport='yes' listen='127.0.0.1'><listen type='address' address='127.0.0.1'/></graphics>
    <tpm model='tpm-crb'><backend type='emulator' version='2.0'/></tpm>
    <rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>
  </devices>
  <seclabel type='dynamic' model='apparmor' relabel='yes'/>
</domain>"""


def xdr_string(s):
    b = s.encode()
    return struct.pack(">I", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)


def call(procedure, payload=b"", program=site_relay.REMOTE_PROGRAM, kind=site_relay.CALL):
    body = site_relay.HEADER.pack(program, 1, procedure, kind, 7, 0) + payload
    return struct.pack(">I", 4 + len(body)) + body


def migrate(procedure, params, cookie=b"", flags=0):
    payload = struct.pack(">I", len(params))
    for field, value in params.items():
        payload += xdr_string(field) + (
            struct.pack(">i", 7) + xdr_string(value) if isinstance(value, str) else struct.pack(">iQ", 4, value)
        )
    payload += struct.pack(">I", len(cookie)) + cookie + b"\0" * ((4 - len(cookie) % 4) % 4) + struct.pack(">I", flags)
    if procedure == site_relay.FINISH3_PARAMS:
        payload += struct.pack(">i", 0)
    return call(procedure, payload)


def prepare(xml=RUNNING, **extra):
    return migrate(
        site_relay.PREPARE3_PARAMS,
        {"destination_xml": xml, "migrate_uri": "unix:///run/hyperlite/migration/abc/memory.sock", **extra},
    )


def test_a_migration_passes_whole_even_cut_anywhere():
    f = site_relay.CallFilter(EXPECTED)
    stream = (
        call(1)
        + call(66)
        + call(60)
        + prepare()
        + call(4, kind=site_relay.MESSAGE, program=site_relay.KEEPALIVE_PROGRAM)
    )
    stream = (
        stream[:-28]
        + call(1, program=site_relay.KEEPALIVE_PROGRAM, kind=site_relay.MESSAGE)
        + migrate(site_relay.FINISH3_PARAMS, {"destination_name": "web"})
        + call(2)
    )
    with pytest.raises(Refused):  # keepalive's procedure 4 does not exist
        site_relay.CallFilter(EXPECTED).feed(call(4, kind=site_relay.MESSAGE, program=site_relay.KEEPALIVE_PROGRAM))
    passed = b"".join(f.feed(stream[i : i + 13]) for i in range(0, len(stream), 13))
    assert passed == stream and f.seen[-3:] == [
        "DOMAIN_MIGRATE_PREPARE3_PARAMS",
        "DOMAIN_MIGRATE_FINISH3_PARAMS",
        "CONNECT_CLOSE",
    ]


@pytest.mark.parametrize(
    ("procedure", "why"),
    [(11, "define"), (9, "create"), (60 + 1000, "unknown"), (24, "lookup by UUID")],
)
def test_any_other_call_closes_the_channel(procedure, why):
    with pytest.raises(Refused, match="Not a migration's call"):
        site_relay.CallFilter(EXPECTED).feed(call(1) + call(procedure))


def test_another_program_or_a_huge_message_is_refused():
    with pytest.raises(Refused, match="Not a migration's call"):
        site_relay.CallFilter(EXPECTED).feed(call(1, program=0x06900690))  # the admin program
    with pytest.raises(Refused, match="bytes"):
        site_relay.CallFilter(EXPECTED).feed(struct.pack(">I", 64 * 1024 * 1024) + b"\0" * 24)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda x: x.replace("<name>web</name>", "<name>other</name>"), "Another VM"),
        (lambda x: x.replace(UUID, "6f1c1c39-0000-4b8a-9d4a-3f0e8e2b9a11"), "Another VM"),
        (lambda x: x.replace("web-disk0.qcow2' index", "../../etc/shadow' index"), "volume prepared"),
        (
            lambda x: x.replace(
                "<backingStore/>", "<backingStore type='file'><source file='/etc/shadow'/></backingStore>"
            ),
            "backing",
        ),
        (lambda x: x.replace("source network='prod'", "source network='mgmt'"), "network chosen"),
        (lambda x: x.replace("<interface type='network'>", "<interface type='ethernet'>"), "network chosen"),
        (lambda x: x.replace("/usr/bin/qemu-system-x86_64</emulator>", "/srv/evil-qemu</emulator>"), "emulator"),
        (lambda x: x.replace("</devices>", "<hostdev mode='subsystem' type='pci'/></devices>"), "hostdev"),
        (
            lambda x: x.replace(
                "</devices>", "<filesystem type='mount'><source dir='/'/><target dir='r'/></filesystem></devices>"
            ),
            "filesystem",
        ),
        (
            lambda x: x.replace(
                "</domain>",
                "<qemu:commandline xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'><qemu:arg value='-x'/></qemu:commandline></domain>",
            ),
            "schema",
        ),
        (lambda x: x.replace("relabel='yes'", "relabel='no'"), "confinement"),
        (lambda x: x.replace("type='dynamic' model='apparmor'", "type='none'"), "confinement"),
        (
            lambda x: x.replace(
                "<target dev='sdw' bus='sata'/>", "<source file='/etc/shadow'/><target dev='sdw' bus='sata'/>"
            ),
            "medium",
        ),
        (lambda x: x.replace("domain-3-web/org.qemu", "../../../../etc/org.qemu"), "socket"),
        (lambda x: x.replace("<serial type='pty'>", "<serial type='file'>"), "type file"),
        (
            lambda x: x.replace("listen='127.0.0.1'><listen type='address' address='127.0.0.1'/>", "listen='0.0.0.0'>"),
            "console",
        ),
        (
            lambda x: x.replace(
                "<backend type='emulator' version='2.0'/>",
                "<backend type='passthrough'><device path='/dev/tpm0'/></backend>",
            ),
            "TPM",
        ),
        (lambda x: x.replace("/dev/urandom</backend>", "/dev/sda</backend>"), "random"),
        (lambda x: x.replace("<nvram>/var/lib/libvirt/qemu/nvram/web_VARS.fd", "<nvram>/etc/shadow"), "UEFI"),
        (lambda x: x.replace("</os>", "<kernel>/boot/vmlinuz</kernel></os>"), "kernel"),
        (lambda x: x.replace("</devices>", "<memoryBacking><source type='file'/></memoryBacking></devices>"), "Memory"),
        (
            lambda x: x.replace(
                "</devices>", "<watchdog model='i6300esb'><source path='/etc/x'/></watchdog></devices>"
            ),
            "path",
        ),
    ],
)
def test_the_domain_to_run_must_be_the_incoming_one_on_what_was_prepared(change, message):
    site_relay.check_destination_xml(RUNNING, EXPECTED)  # the running VM itself passes
    with pytest.raises(Refused, match=message):
        site_relay.check_destination_xml(change(RUNNING), EXPECTED)


def test_the_parameters_and_the_cookie_are_checked():
    ok = site_relay.CallFilter(EXPECTED)
    assert ok.feed(prepare())
    for bad, message in (
        (migrate(site_relay.PREPARE3_PARAMS, {"destination_xml": RUNNING, "persistent_xml": RUNNING}), "not allowed"),
        (prepare(migrate_uri="tcp://10.0.0.9:49152"), "channel"),
        (prepare(disks_uri="unix:///run/libvirt/libvirt-sock"), "channel"),
        (
            migrate(site_relay.PREPARE3_PARAMS, {"destination_xml": RUNNING}, flags=site_relay.PERSIST_DEST),
            "define the VM",
        ),
        (migrate(site_relay.PREPARE3_PARAMS, {"migrate_uri": "unix:///run/hyperlite/migration/abc/m"}), "missing"),
        (
            migrate(
                site_relay.PREPARE3_PARAMS,
                {"destination_xml": RUNNING},
                cookie=b"<qemu-migration><persistent/></qemu-migration>",
            ),
            "cookie",
        ),
        (migrate(site_relay.FINISH3_PARAMS, {"destination_name": "other"}), "Another name"),
    ):
        with pytest.raises(Refused, match=message):
            site_relay.CallFilter(EXPECTED).feed(bad)
