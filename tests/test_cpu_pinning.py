"""CPU pinning and NUMA placement: strict parsing of the CPU list (it ends up in the domain XML), checks against the
host's real CPUs, the XML written and read back, and the endpoints. Live pinning (pinVcpuFlags, pinEmulator), a
restart and a later vCPU increase were also checked on a real libvirt 10."""

import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import cpu_pinning
from app.routers.vms import settings

# Two NUMA cells of two cores with hyperthreads, and one offline CPU (listed without socket_id).
CAPS = """<capabilities><host><topology><cells num='2'>
  <cell id='0'><memory unit='KiB'>8388608</memory><cpus num='4'>
    <cpu id='0' socket_id='0' core_id='0' siblings='0,4'/><cpu id='1' socket_id='0' core_id='1' siblings='1,5'/>
    <cpu id='4' socket_id='0' core_id='0' siblings='0,4'/><cpu id='5' socket_id='0' core_id='1' siblings='1,5'/>
  </cpus></cell>
  <cell id='1'><memory unit='KiB'>8388608</memory><cpus num='5'>
    <cpu id='2' socket_id='1' core_id='0' siblings='2,6'/><cpu id='3' socket_id='1' core_id='1' siblings='3,7'/>
    <cpu id='6' socket_id='1' core_id='0' siblings='2,6'/><cpu id='7' socket_id='1' core_id='1' siblings='3,7'/>
    <cpu id='8'/>
  </cpus></cell>
</cells></topology></host></capabilities>"""
SINGLE_CELL = CAPS.split("<cell id='1'>")[0] + "</cells></topology></host></capabilities>"
DOMAIN = "<domain type='kvm'><name>vm1</name><memory>1048576</memory><vcpu placement='static'>2</vcpu><os/><devices/></domain>"


class FakeConn:
    def __init__(self, caps=CAPS):
        self.caps = caps
        self.domain = FakeDomain(self)

    def getCapabilities(self):
        return self.caps

    def lookupByName(self, name):
        if name != "vm1":
            raise libvirt.libvirtError("no domain")
        return self.domain

    def defineXML(self, xml):
        self.domain.xml = xml
        return self.domain

    def getCPUMap(self, flags):
        return (9, (True,) * 9, 8)

    def close(self):
        pass


class FakeDomain:
    def __init__(self, conn):
        self.xml = DOMAIN
        self.active = False
        self.live = {}

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return self.active

    def info(self):
        return (1, 0, 0, 2, 0)

    def pinVcpuFlags(self, vcpu, cpumap, flags):
        self.live[vcpu] = [i for i, on in enumerate(cpumap) if on]

    def pinEmulator(self, cpumap, flags):
        self.live["emulator"] = [i for i, on in enumerate(cpumap) if on]


def test_cpu_lists_are_parsed_and_written_back_compactly():
    assert cpu_pinning.parse_cpuset("0-3,6") == [0, 1, 2, 3, 6]
    assert cpu_pinning.parse_cpuset(" 5, 1-2 ") == [1, 2, 5]
    assert cpu_pinning.format_cpuset([6, 0, 1, 2, 3, 3]) == "0-3,6"


@pytest.mark.parametrize("text", ["", "a", "1-", "3-1", "1;reboot", "1,,2", "0-99999", "'/><x"])
def test_anything_but_a_plain_cpu_list_is_refused(text):
    with pytest.raises(cpu_pinning.PinningError):
        cpu_pinning.parse_cpuset(text)


def test_the_topology_lists_online_cpus_with_their_cell_and_hyperthread_siblings():
    topo = cpu_pinning.host_topology(FakeConn())
    assert [c["id"] for c in topo["cpus"]] == [0, 1, 2, 3, 4, 5, 6, 7]
    cpu2 = next(c for c in topo["cpus"] if c["id"] == 2)
    assert cpu2 == {"id": 2, "socket": 1, "coeur": 0, "cellule": 1, "freres": [2, 6]}
    assert topo["cellules"] == [
        {"id": 0, "cpus": "0-1,4-5", "memoire_mo": 8192},
        {"id": 1, "cpus": "2-3,6-7", "memoire_mo": 8192},
    ]


@pytest.mark.parametrize(
    ("cpuset", "strict", "text"),
    [
        ("8", False, "no CPU 8"),
        ("0-9", False, "no CPU 8-9"),
        ("2", True, "2 vCPUs but only 1 CPUs"),
        (None, True, "needs a list"),
    ],
)
def test_requests_the_host_cannot_honour_are_refused(cpuset, strict, text):
    with pytest.raises(cpu_pinning.PinningError) as err:
        cpu_pinning.plan(cpu_pinning.host_topology(FakeConn()), 2, cpuset, strict)
    assert text in err.value.message


def test_the_memory_follows_the_cpus_only_when_they_sit_in_one_cell_of_a_multi_cell_host():
    topo = cpu_pinning.host_topology(FakeConn())
    assert cpu_pinning.plan(topo, 2, "2-3", False)["numa_cellule"] == 1
    assert cpu_pinning.plan(topo, 2, "1-2", False)["numa_cellule"] is None  # spans both cells
    single = cpu_pinning.host_topology(FakeConn(SINGLE_CELL))
    assert cpu_pinning.plan(single, 2, "0-1", False)["numa_cellule"] is None


def test_the_xml_is_written_read_back_and_removed():
    root = ET.fromstring(DOMAIN)
    cpu_pinning.apply_to_xml(root, 2, {"cpus": [2, 3], "strict": True, "numa_cellule": 1})
    assert root.find("vcpu").get("cpuset") == "2-3"
    assert [(p.get("vcpu"), p.get("cpuset")) for p in root.findall("cputune/vcpupin")] == [("0", "2"), ("1", "3")]
    assert root.find("cputune/emulatorpin").get("cpuset") == "2-3"
    assert root.find("numatune/memory").attrib == {"mode": "strict", "nodeset": "1"}

    class Dom:
        def XMLDesc(self, flags):
            return ET.tostring(root, encoding="unicode")

    assert cpu_pinning.read(Dom()) == {"cpuset": "2-3", "strict": True, "numa_cellule": 1}
    cpu_pinning.apply_to_xml(root, 2, {"cpus": None, "strict": False, "numa_cellule": None})
    assert root.find("cputune") is None and root.find("numatune") is None and "cpuset" not in root.find("vcpu").attrib
    assert cpu_pinning.read(Dom()) == {"cpuset": None, "strict": False, "numa_cellule": None}


@pytest.fixture()
def api(client, auth_headers, monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(settings, "open_conn", lambda *a, **k: conn)
    return {"client": client, "headers": auth_headers("admin1"), "conn": conn}


def test_the_endpoint_pins_a_stopped_vm_and_reports_the_host_topology(api):
    r = api["client"].put("/vms/vm1/cpu-pinning", json={"cpuset": "2-3", "strict": True}, headers=api["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["cpuset"], body["strict"], body["numa_cellule"], body["redemarrage_requis"]) == ("2-3", True, 1, False)
    assert len(body["topologie"]["cpus"]) == 8
    assert '<vcpupin vcpu="0" cpuset="2"' in api["conn"].domain.xml
    assert api["conn"].domain.live == {}  # stopped: nothing to pin live

    got = api["client"].get("/vms/vm1/cpu-pinning", headers=api["headers"]).json()
    assert got["cpuset"] == "2-3" and got["strict"] is True


def test_a_running_vm_is_pinned_at_once_and_told_when_its_memory_needs_a_restart(api):
    api["conn"].domain.active = True
    body = api["client"].put("/vms/vm1/cpu-pinning", json={"cpuset": "2-3"}, headers=api["headers"]).json()
    assert api["conn"].domain.live == {0: [2, 3], 1: [2, 3], "emulator": [2, 3]}
    assert body["redemarrage_requis"] is True  # the memory moves to cell 1 at the next start
    body = api["client"].put("/vms/vm1/cpu-pinning", json={"cpuset": None}, headers=api["headers"]).json()
    assert api["conn"].domain.live[0] == list(range(9)) and body["cpuset"] is None


def test_invalid_requests_come_back_as_422_without_touching_the_vm(api):
    before = api["conn"].domain.xml
    r = api["client"].put("/vms/vm1/cpu-pinning", json={"cpuset": "0-99"}, headers=api["headers"])
    assert r.status_code == 422 and "no CPU" in r.json()["detail"]
    assert api["client"].put("/vms/vm1/cpu-pinning", json={"cpuset": "1;x"}, headers=api["headers"]).status_code == 422
    assert api["conn"].domain.xml == before
    assert api["client"].get("/vms/nope/cpu-pinning", headers=api["headers"]).status_code == 404


def test_an_observer_cannot_pin(api, auth_headers):
    headers = auth_headers("watcher", role="observateur")
    assert api["client"].put("/vms/vm1/cpu-pinning", json={"cpuset": "2"}, headers=headers).status_code == 403
