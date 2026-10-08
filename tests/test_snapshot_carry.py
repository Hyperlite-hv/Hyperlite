"""A VM's snapshot records cross a live migration on shared storage: libvirt refuses to migrate a VM that has them."""

import libvirt

from app.core import snapshot_carry


class _Snap:
    def __init__(self, dom, name, parent):
        self.dom, self.name, self.parent = dom, name, parent

    def getName(self):
        return self.name

    def getXMLDesc(self, flags=0):
        parent = f"<parent><name>{self.parent}</name></parent>" if self.parent else ""
        return f"<domainsnapshot><name>{self.name}</name>{parent}</domainsnapshot>"

    def delete(self, flags):
        assert flags == libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_METADATA_ONLY  # never the data in the disks
        self.dom.deleted.append(self.name)
        del self.dom.snaps[self.name]


class _Dom:
    def __init__(self, tree=(), current=None):
        self.snaps = {}
        self.deleted, self.defined = [], []
        for name, parent in tree:
            self.snaps[name] = _Snap(self, name, parent)
        self.current = current

    def name(self):
        return "web"

    def listAllSnapshots(self, flags):
        return list(self.snaps.values())

    def snapshotCurrent(self, flags):
        if not self.current:
            raise libvirt.libvirtError("no current")
        return self.snaps[self.current]

    def snapshotCreateXML(self, xml, flags):
        name = xml.split("<name>")[1].split("</name>")[0]
        if name == "broken":
            raise libvirt.libvirtError("cannot redefine")
        self.defined.append((name, bool(flags & libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_CURRENT)))
        assert flags & libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REDEFINE


def test_records_go_children_first_and_come_back_parents_first_with_the_current_one():
    # "c" listed before its parent "b", itself a child of "a".
    src = _Dom([("c", "b"), ("a", None), ("b", "a"), ("d", "a")], current="c")
    stashed = snapshot_carry.stash(src)
    assert src.snaps == {}
    assert src.deleted.index("c") < src.deleted.index("b") < src.deleted.index("a")
    dst = _Dom()
    assert snapshot_carry.restore(dst, stashed) == []
    names = [n for n, _ in dst.defined]
    assert names.index("a") < names.index("b") < names.index("c") and names.index("a") < names.index("d")
    assert [n for n, current in dst.defined if current] == ["c"]


def test_no_snapshot_nothing_to_carry_and_a_failure_is_reported():
    assert snapshot_carry.stash(_Dom()) is None
    assert snapshot_carry.restore(_Dom(), None) == []
    stashed = snapshot_carry.stash(_Dom([("broken", None), ("fine", None)]))
    assert snapshot_carry.restore(_Dom(), stashed) == ["broken"]
