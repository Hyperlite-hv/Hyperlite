"""A cluster updated to API v1 (app/core/cluster_v1.py): read-only while a member still runs a version before 1.0,
then the replicated configuration rewritten once with the new names by the leader."""

import json

import pytest

from app.core import cluster_lead, cluster_v1, cluster_view, statd
from app.core.database import get_conn
from app.repositories.cfs import ids, inbound, shadow
from tests.test_cfs_inbound import Cluster


@pytest.fixture()
def fake(database, monkeypatch):
    fake = Cluster()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(shadow, "_stats", shadow._Stats())
    monkeypatch.setattr(inbound, "state", inbound._State())
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)
    monkeypatch.setattr(cluster_lead, "QUORUM_TTL_S", 0)
    monkeypatch.setattr(cluster_v1, "_TTL_S", 0)
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: True)
    cluster_lead.forget()
    ids.forget()
    shadow.seed()
    return fake


@pytest.fixture()
def members(monkeypatch):
    state = {"members": ["pve-b", "pve-c"], "statuses": {}}
    monkeypatch.setattr(cluster_view, "members", lambda: [{"name": n} for n in state["members"]])
    monkeypatch.setattr(statd, "table", lambda: state["statuses"])
    return state


def status(api):
    return {"vms": [], "age_s": 2.0, "stale": False, "api": api}


def test_a_member_before_1_0_holds_the_cluster_read_only_by_name(client, auth_headers, fake, members):
    headers = auth_headers("ana")
    members["statuses"] = {"pve-b": status(0), "pve-c": status(1)}
    r = client.post("/groups", json={"name": "ops"}, headers=headers)
    assert r.status_code == 503 and "pve-b still runs an older version" in r.json()["detail"]
    assert client.get("/groups", headers=headers).status_code == 200  # reading still works
    members["statuses"]["pve-b"] = status(1)  # updated
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 201


def test_a_member_that_sends_nothing_holds_nothing_back(fake, members):
    members["statuses"] = {"pve-c": status(1)}  # pve-b is down
    assert cluster_v1.behind() == [] and cluster_v1.read_only_reason() is None


def test_the_leader_rewrites_the_old_rows_once_every_member_runs_1_0(fake, members):
    members["statuses"] = {"pve-b": status(0), "pve-c": status(1)}
    old = {"id": 7, "name": "ops"}  # groups have no French column: a table that has some
    fake.remote("/db/groups/7", old)
    fake.remote("/db/backup_jobs/3", {"id": 3, "vm_name": "web", "frequence": "quotidien", "heure": "02:00",
                                     "cible_dir": "/srv/b", "actif": 1, "retention_count": 7,
                                     "prochaine_execution": "2026-10-20T02:00:00+00:00"})  # fmt: skip
    assert cluster_v1.step() is False  # pve-b still runs an older version: nothing rewritten
    assert json.loads(fake.files["/db/backup_jobs/3"])["frequence"] == "quotidien"

    members["statuses"]["pve-b"] = status(1)
    assert cluster_v1.step() is False  # rewritten this round, checked again at the next
    row = json.loads(fake.files["/db/backup_jobs/3"])
    assert row["frequency"] == "daily" and row["target_dir"] == "/srv/b" and "frequence" not in row
    assert json.loads(fake.files["/db/groups/7"]) == old  # already in the new names: untouched
    assert cluster_v1.step() is True and cluster_v1.rewritten()
    assert cluster_v1.step() is True  # done once for the cluster


def test_a_change_made_after_the_rewrite_goes_out(fake, members):
    members["statuses"] = {"pve-b": status(1)}
    fake.remote("/db/backup_jobs/3", {"id": 3, "vm_name": "web", "frequence": "quotidien", "heure": "02:00",
                                     "cible_dir": "/srv/b", "actif": 1, "retention_count": 7,
                                     "prochaine_execution": "2026-10-20T02:00:00+00:00"})  # fmt: skip
    inbound.apply()  # this node holds it, read with the new names
    cluster_v1.step()
    with get_conn() as db:
        db.execute("UPDATE backup_jobs SET time = '03:00' WHERE id = 3")
        db.commit()
    assert shadow.drain() == 1 and shadow.report()["conflicts"] == 0  # not taken for another node's change
    assert json.loads(fake.files["/db/backup_jobs/3"])["time"] == "03:00"
