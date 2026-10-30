"""HA admission control (docs/design/ha-automatic.md, 4.8, #333 step 5): the loss of any one member must leave room for
its running HA VMs on the others; `warn` says it, `strict` refuses, `off` checks nothing."""

import pytest

from app.core import cluster_lead, cluster_view, ha, ha_admission, self_node, statd


def protect(database, vm, node, memory_mib):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) VALUES (?, ?, ?, 'admin', '2026-01-01')",
            (vm, node, f"<domain><name>{vm}</name><memory unit='MiB'>{memory_mib}</memory></domain>"),
        )
        db.commit()


@pytest.fixture()
def cluster(database, monkeypatch):
    """n1 (this node), n2, n3, all seen; each test says their free memory and what runs where."""
    world = {"free": {"n1": 4000, "n2": 4000, "n3": 4000}, "running": {"n1": [], "n2": [], "n3": []}, "ages": {}}
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr(cluster_view, "members", lambda: [{"name": "n2"}, {"name": "n3"}])
    monkeypatch.setattr(
        statd,
        "latest",
        lambda: {
            n: (world["ages"].get(n, 1.0), [{"name": vm, "state": "active"} for vm in vms])
            for n, vms in world["running"].items()
        },
    )
    monkeypatch.setattr(statd, "table", lambda: {n: {"mem_free_mb": f} for n, f in world["free"].items()})
    return world


def test_a_member_whose_ha_vms_fit_on_the_others_is_absorbed(cluster, database):
    protect(database, "web", "n2", 3000)
    cluster["running"]["n2"] = ["web"]
    assert ha_admission.check() == {"policy": "warn", "ok": True, "unabsorbed": [], "unknown": [], "detail": ""}


def test_a_vm_larger_than_any_other_members_free_memory_is_named(cluster, database):
    protect(database, "big", "n2", 3000)
    cluster["running"]["n2"] = ["big"]
    cluster["free"].update({"n1": 2000, "n3": 2000})  # 4000 in all, but no member alone has 3000
    result = ha_admission.check()
    assert result["ok"] is False and result["unabsorbed"] == ["n2"] and "n2 failed" in result["detail"]


def test_vms_are_spread_over_the_others_largest_first(cluster, database):
    for vm, mib in (("a", 1500), ("b", 1500), ("c", 900)):
        protect(database, vm, "n2", mib)
    cluster["running"]["n2"] = ["a", "b", "c"]
    cluster["free"].update({"n1": 2500, "n3": 2500})  # 1500 + 900 on one, 1500 on the other
    assert ha_admission.check()["ok"] is True
    cluster["free"]["n3"] = 1400  # one 1500 then has nowhere to go
    assert ha_admission.check()["unabsorbed"] == ["n2"]


def test_a_stopped_ha_vm_or_a_lost_member_does_not_count(cluster, database):
    protect(database, "idle", "n2", 9000)  # stopped: restarted by nobody, so it takes no room
    assert ha_admission.check()["ok"] is True
    protect(database, "web", "n2", 3000)
    cluster["running"]["n2"] = ["web"]
    cluster["free"]["n1"] = 1000
    cluster["ages"]["n3"] = 600.0  # n3 is lost: no room there
    assert ha_admission.check()["unabsorbed"] == ["n2"]


def test_an_unknown_free_memory_is_named_and_never_refused_for(cluster, database):
    protect(database, "web", "n2", 3000)
    cluster["running"]["n2"] = ["web"]
    cluster["free"]["n3"] = None  # a node before this version
    result = ha_admission.check()
    assert result["ok"] is True and result["unknown"] == ["n2"] and "not known" in result["detail"]
    ha_admission.set_policy("strict")
    assert ha_admission.refuse_if_strict(None)["ok"] is True


def test_a_vm_about_to_start_takes_its_memory_where_it_starts(cluster, database):
    protect(database, "web", "n2", 3000)
    cluster["running"]["n2"] = ["web"]
    cluster["free"].update({"n1": 3500, "n3": 3500})
    assert ha_admission.check(("other", "n1", 1000, True))["ok"] is True  # n1 keeps 2500: web fits on n3
    cluster["free"]["n3"] = 1000
    assert ha_admission.check(("other", "n1", 1000, True))["unabsorbed"] == ["n2"]  # n1 keeps 2500 < 3000
    # Turned on while it already runs: its memory is already out of n1's free memory.
    assert ha_admission.check(("other", "n1", 1000, False))["ok"] is True


def test_strict_refuses_warn_says_off_checks_nothing(cluster, database):
    protect(database, "big", "n2", 3000)
    cluster["running"]["n2"] = ["big"]
    cluster["free"].update({"n1": 2000, "n3": 2000})
    assert ha_admission.refuse_if_strict(None)["ok"] is False  # warn: said, not refused
    ha_admission.set_policy("strict")
    with pytest.raises(RuntimeError, match="admission control"):
        ha_admission.refuse_if_strict(None)
    ha_admission.set_policy("off")
    assert ha_admission.check()["ok"] is True
    with pytest.raises(ValueError):
        ha_admission.set_policy("sometimes")


def test_outside_a_cluster_nothing_is_checked(cluster, database, monkeypatch):
    protect(database, "big", "n2", 30000)
    cluster["running"]["n2"] = ["big"]
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: False)
    assert ha_admission.check()["ok"] is True


def test_the_policy_is_set_and_read_through_the_api(client, auth_headers, database):
    headers = auth_headers("root-admin", role="admin")
    r = client.put("/ha/admission", json={"policy": "strict"}, headers=headers)
    assert r.status_code == 200 and r.json()["policy"] == "strict"
    assert client.get("/ha/status", headers=headers).json()["admission"]["policy"] == "strict"
    assert client.put("/ha/admission", json={"policy": "maybe"}, headers=headers).status_code == 422
    assert ha.get_protected("nothing") is None


def test_starting_an_ha_vm_is_refused_under_strict_when_it_would_leave_a_member_unabsorbed(cluster, database):
    from fastapi import HTTPException

    from app.routers.vms import lifecycle

    protect(database, "big", "n2", 3000)
    cluster["running"]["n2"] = ["big"]
    cluster["free"].update({"n1": 3500, "n3": 2000})
    protect(database, "web", "n1", 1000)  # stopped for now, on this node

    class Domain:
        def XMLDesc(self, flags=0):
            return "<domain><name>web</name><memory unit='MiB'>1000</memory></domain>"

    user = {"username": "admin"}
    lifecycle._admit_ha_start("web", None, Domain(), user, None)  # warn: started, said in the log
    ha_admission.set_policy("strict")
    with pytest.raises(HTTPException) as err:
        lifecycle._admit_ha_start("web", None, Domain(), user, None)  # n1 would keep 2500 < 3000 for big
    assert err.value.status_code == 409 and "n2" in err.value.detail
