"""LVM pools (app/core/lvm_storage.py): what libvirt's logical pool does not do, read from LVM's own reports. The
real behaviour (libvirt blind to thin volumes, unable to resize, its pool deletion removing the whole group) is in
docs/design/lvm-storage.md."""

import json

import pytest

from app.core import lvm_storage

LVS = {
    "report": [
        {
            "lv": [
                {
                    "lv_name": "vm1-disk",
                    "lv_size": "1073741824",
                    "lv_attr": "-wi-a-----",
                    "pool_lv": "",
                    "data_percent": "",
                    "origin": "",
                },
                {
                    "lv_name": "thinpool",
                    "lv_size": "2147483648",
                    "lv_attr": "twi-aotz--",
                    "pool_lv": "",
                    "data_percent": "3.13",
                    "origin": "",
                },
                {
                    "lv_name": "vm2-disk",
                    "lv_size": "4294967296",
                    "lv_attr": "Vwi-a-tz--",
                    "pool_lv": "thinpool",
                    "data_percent": "1.56",
                    "origin": "",
                },
            ]
        }
    ]
}


@pytest.fixture()
def lvm(monkeypatch):
    calls = []
    answers = {
        "lvs": json.dumps(LVS),
        "vgs": json.dumps(
            {"report": [{"vg": [{"vg_name": "hlvg", "vg_size": "10733223936", "vg_free": "6438256640"}]}]}
        ),
    }

    def run(args, node=None):
        calls.append((args, node))
        return answers.get(args[1], "")

    monkeypatch.setattr(lvm_storage, "_run", run)
    return {"calls": calls, "answers": answers}


def test_every_volume_is_read_from_lvm_thin_ones_included(lvm):
    volumes = {v["name"]: v for v in lvm_storage.logical_volumes("hlvg")}
    assert volumes["vm1-disk"]["kind"] == "linear" and volumes["vm1-disk"]["path"] == "/dev/hlvg/vm1-disk"
    assert volumes["thinpool"]["kind"] == "thin_pool" and volumes["thinpool"]["data_percent"] == 3.13
    assert volumes["vm2-disk"]["kind"] == "thin" and volumes["vm2-disk"]["thin_pool"] == "thinpool"
    assert lvm_storage.volume_groups() == [{"name": "hlvg", "size": 10733223936, "free": 6438256640}]


def test_a_pool_is_not_removed_while_a_volume_libvirt_does_not_see_is_in_it(lvm):
    with pytest.raises(lvm_storage.LvmError, match="still holds 3 volume"):
        lvm_storage.refuse_removal_if_not_empty("hlvg")
    lvm["answers"]["lvs"] = json.dumps({"report": [{"lv": [LVS["report"][0]["lv"][2]]}]})
    with pytest.raises(lvm_storage.LvmError, match="vm2-disk"):
        lvm_storage.refuse_removal_if_not_empty("hlvg")  # a thin volume alone: libvirt calls this pool empty
    lvm["answers"]["lvs"] = json.dumps({"report": [{"lv": []}]})
    lvm_storage.refuse_removal_if_not_empty("hlvg")


def test_a_volume_is_grown_by_lvextend_never_shrunk(lvm):
    lvm_storage.extend("hlvg", "vm1-disk", 2 * 2**30)
    assert lvm["calls"][-1][0] == ["lvm", "lvextend", "-L", f"{2 * 2**30}b", "hlvg/vm1-disk"]
    with pytest.raises(lvm_storage.LvmError, match="only grown"):
        lvm_storage.extend("hlvg", "vm1-disk", 2**30)
    with pytest.raises(lvm_storage.LvmError, match="not found"):
        lvm_storage.extend("hlvg", "nope", 2 * 2**30)


@pytest.mark.parametrize("name", ["", "-x", "a b", "a;b", "../x", "a/b", "$(id)"])
def test_names_reach_lvm_only_when_they_are_names(lvm, name):
    with pytest.raises(lvm_storage.LvmError, match="Invalid"):
        lvm_storage.logical_volumes(name)
    assert lvm["calls"] == []


def test_a_remote_node_is_reached_over_ssh(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "app.core.cluster.get_node", lambda name: {"hostname": "10.0.0.2", "ssh_port": 22, "ssh_user": "root"}
    )
    monkeypatch.setattr("app.core.cluster.node_ssh_options", lambda extra=None: ["-o", "BatchMode=yes", *(extra or [])])

    class Done:
        returncode, stdout, stderr = 0, json.dumps({"report": [{"lv": []}]}), ""

    monkeypatch.setattr(lvm_storage.subprocess, "run", lambda args, **kw: seen.append(args) or Done())
    assert lvm_storage.logical_volumes("hlvg", node="node-b") == []
    assert seen[0][0] == "ssh" and seen[0][-2] == "root@10.0.0.2" and seen[0][-1].startswith("lvm lvs ")
