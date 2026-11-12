"""Numeric guest identifiers (app/core/vmid.py): the counter in hyperlite-cfs, the number kept with the UUID it was
given to, the numbering of guests that have none, and the API taking a VMID where it takes a name."""

import threading
import xml.etree.ElementTree as ET

import pytest

from app.core import vmid
from app.core.cfs_client import Conflict, Entry, NotFound

UUID = "6f1c2d3e-0000-4000-8000-000000000001"
OTHER = "6f1c2d3e-0000-4000-8000-000000000002"


def domain_xml(uuid, number=None, recorded_uuid=None):
    meta = (
        f"<metadata><v:vmid xmlns:v='{vmid.METADATA_NS}' uuid='{recorded_uuid or uuid}'>{number}</v:vmid></metadata>"
        if number is not None
        else ""
    )
    return f"<domain><name>x</name><uuid>{uuid}</uuid>{meta}</domain>"


def test_the_number_is_the_guests_only_with_its_own_uuid():
    assert vmid.of_root(ET.fromstring(domain_xml(UUID, 104))) == 104
    assert vmid.of_root(ET.fromstring(domain_xml(UUID))) is None
    assert vmid.of_root(ET.fromstring(domain_xml(OTHER, 104, recorded_uuid=UUID))) is None  # a copy: numbered again


class Counter:
    """hyperlite-cfs's compare-and-set, with a concurrent writer injected `races` times."""

    def __init__(self, value=None, races=0):
        self.value, self.version, self.races = value, 1, races

    def get(self, path):
        assert path == vmid.COUNTER
        if self.value is None:
            raise NotFound(1, "no entry")
        return Entry(data=str(self.value).encode(), version=self.version, mtime=0)

    def put(self, path, data, expected):
        if self.races:
            self.races -= 1
            self.value, self.version = (self.value or vmid.FIRST) + 1, self.version + 1
            raise Conflict(2, "changed")
        if (expected == 0 and self.value is not None) or (expected > 0 and expected != self.version):
            raise Conflict(2, "changed")
        self.value, self.version = int(data), self.version + 1
        return self.version


def test_the_counter_starts_at_100_and_never_hands_a_number_out_twice():
    counter = Counter()
    assert [vmid.allocate(counter) for _ in range(3)] == [100, 101, 102]
    raced = Counter(value=200, races=2)  # another node took 200 and 201 meanwhile
    assert vmid.allocate(raced) == 202 and raced.value == 203


def test_two_nodes_numbering_at_once_get_different_numbers():
    counter, got, lock = Counter(), [], threading.Lock()
    real_put = counter.put

    def put(path, data, expected):
        with lock:
            return real_put(path, data, expected)

    counter.put = put

    def take():
        for _ in range(20):
            got.append(vmid.allocate(counter))

    threads = [threading.Thread(target=take) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == list(range(100, 180))


class Domain:
    def __init__(self, name, uuid, number=None, recorded_uuid=None, active=False):
        self._name, self.uuid, self.active = name, uuid, active
        self.xml = domain_xml(uuid, number, recorded_uuid)
        self.metadata = []

    def name(self):
        return self._name

    def UUIDString(self):
        return self.uuid

    def isActive(self):
        return self.active

    def XMLDesc(self, flags):
        return self.xml

    def setMetadata(self, kind, xml, key, uri, flags):
        self.metadata.append((xml, key, uri, flags))


class Conn:
    def __init__(self, domains):
        self.domains = domains

    def listAllDomains(self):
        return self.domains


def test_guests_without_a_number_get_one_with_their_uuid():
    import libvirt

    numbered = Domain("old", UUID, 100)
    clone = Domain("clone", OTHER, 100, recorded_uuid=UUID)  # its source's metadata, copied
    new = Domain("new", "6f1c2d3e-0000-4000-8000-000000000003", active=True)
    numbers = iter([105, 106])
    assert vmid.number(Conn([numbered, clone, new]), lambda: next(numbers)) == [("clone", 105), ("new", 106)]
    assert numbered.metadata == []
    xml, key, uri, flags = clone.metadata[0]
    assert (key, uri, flags) == (vmid.METADATA_KEY, vmid.METADATA_NS, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
    assert ET.fromstring(xml).attrib == {"uuid": OTHER} and ET.fromstring(xml).text == "105"
    assert new.metadata[0][3] == libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_AFFECT_LIVE


@pytest.mark.parametrize(
    ("path", "kind"),
    [("/vms/104/firewall", "vm"), ("/containers/120/start", "container")],
)
def test_the_api_asks_which_guest_a_number_stands_for(client, auth_headers, monkeypatch, path, kind):
    asked = []
    monkeypatch.setattr(vmid, "resolve", lambda k, text: asked.append((k, text)))
    headers = auth_headers("root")
    client.get(path, headers=headers)
    client.get(path.replace("/104", "/web").replace("/120", "/ct1"), headers=headers)  # a name: not asked
    assert asked == [(kind, path.split("/")[2])]


def test_resolve_rewrites_the_request(monkeypatch):
    """The middleware's own rewrite, called directly on a request scope."""
    import asyncio

    from starlette.requests import Request

    from app import main

    monkeypatch.setattr(vmid, "resolve", lambda kind, text: ("db", "node-b"))
    scope = {
        "type": "http",
        "path": "/api/v1/vms/104/start",
        "raw_path": b"",
        "query_string": b"wait=false",
        "headers": [],
        "method": "POST",
    }
    seen = {}

    async def call_next(request):
        seen.update(path=request.scope["path"], query=request.scope["query_string"])
        return "answer"

    assert asyncio.run(main.guests_by_vmid(Request(scope), call_next)) == "answer"
    assert seen == {"path": "/api/v1/vms/db/start", "query": b"wait=false&node=node-b"}
