"""What the HA page reads and changes (#333 step 7): every member's state, an HA VM's policy changed in place, and
the HA history."""

from app.core import audit, cluster_lead, ha, ha_recovery, ha_selffence, self_node, statd


def _protect(database, vm="web"):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) "
            "VALUES (?, 'local', '<domain/>', 'admin', 'now')",
            (vm,),
        )
        db.commit()


def test_in_a_cluster_the_status_says_automatic_and_each_members_state(client, auth_headers, monkeypatch):
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: True)
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    monkeypatch.setattr(ha_recovery, "_members", lambda: ["n2", "n3"])
    monkeypatch.setattr(statd, "ages", lambda: {"n1": 1.0, "n2": 2.0, "n3": 90.0})
    monkeypatch.setattr(statd, "table", lambda: {"n1": {"mem_free_mb": 4000}, "n2": {"mem_free_mb": 2000}})
    published = {"n2": {"armed": True, "reset_within_s": 125, "at": "t"}, "n3": {"armed": False, "stopped": True}}
    monkeypatch.setattr(ha_selffence, "published", lambda name: published.get(name))
    body = client.get("/ha/status", headers=auth_headers("root", "admin")).json()
    assert body["automatic"] is True and body["leading"] is True and "admission" in body
    members = {m["name"]: m for m in body["members"]}
    assert members["n1"]["self"] and members["n1"]["online"]
    assert members["n2"]["online"] and members["n2"]["watchdog"]["armed"] and members["n2"]["mem_free_mb"] == 2000
    assert not members["n3"]["online"] and members["n3"]["watchdog"]["stopped"] and members["n3"]["seen_s"] == 90.0


def test_outside_a_cluster_the_status_is_the_dry_run(client, auth_headers):
    body = client.get("/ha/status", headers=auth_headers("root", "admin")).json()
    assert body["automatic"] is False and body["members"] == [] and body["mode"] == "attempt"


def test_an_ha_vms_policy_is_changed_in_place(client, auth_headers, database):
    _protect(database)
    headers = auth_headers("root", "admin")
    r = client.put("/ha/web/policy", json={"priority": "high", "preferred_nodes": ["n2", "n3"]}, headers=headers)
    assert r.status_code == 200 and r.json()["priority"] == "high" and r.json()["preferred_nodes"] == ["n2", "n3"]
    assert ha.get_protected("web") is not None  # still protected
    assert client.put("/ha/web/policy", json={"priority": "urgent"}, headers=headers).status_code == 422
    assert client.put("/ha/web/policy", json={"preferred_nodes": ["bad name!"]}, headers=headers).status_code == 422
    assert client.put("/ha/nothing/policy", json={}, headers=headers).status_code == 404
    assert client.put("/ha/web/policy", json={}, headers=auth_headers("eve", "viewer")).status_code == 403


def test_the_history_is_the_ha_events_only_newest_first(client, auth_headers, database):
    for action in ("ha_lost", "login", "ha_restarted", "hardware"):
        audit.log_action("system", action, "web", "success")
    audit._AUDIT_QUEUE.join()
    headers = auth_headers("root", "admin")
    items = client.get("/ha/history", headers=headers).json()["items"]
    assert [i["action"] for i in items] == ["ha_restarted", "ha_lost"]  # neither login nor "hardware" (ha... unescaped)
    assert client.get("/ha/history", headers=auth_headers("eve", "viewer")).status_code == 403
