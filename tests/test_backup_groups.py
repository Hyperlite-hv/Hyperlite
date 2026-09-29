"""GFS retention and grouped backup jobs."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import backup_groups as bg
from app.core import backup_retention as br
from app.core import object_meta, permissions
from app.core.database import get_conn


def _daily(n, start=datetime(2026, 1, 1, 2, 0, tzinfo=UTC)):
    """One backup per day for n days, oldest first: (id, ts)."""
    return [(i, (start + timedelta(days=i)).isoformat()) for i in range(n)]


def test_count_only_keeps_the_most_recent():
    assert br.kept(_daily(10), 3) == {7, 8, 9}


def test_gfs_keeps_newest_per_period():
    backups = _daily(120)  # 2026-01-01 .. 2026-04-30
    keep = br.kept(backups, last=2, daily=7, weekly=4, monthly=3)
    days = {datetime.fromisoformat(ts).date().isoformat() for i, ts in backups if i in keep}
    assert {"2026-04-30", "2026-04-29"} <= days  # last
    assert {f"2026-04-{d}" for d in range(24, 31)} <= days  # 7 daily
    assert {"2026-04-26", "2026-04-19", "2026-04-12"} <= days  # the Sundays closing ISO weeks
    assert {"2026-03-31", "2026-02-28"} <= days  # newest of each previous month
    assert len(keep) == len(days) and len(keep) < 20


def test_periods_count_only_those_with_a_backup():
    # Two backups a month apart: 2 weekly slots reach both even though the weeks between are empty.
    backups = [(1, "2026-01-05T02:00:00+00:00"), (2, "2026-02-09T02:00:00+00:00"), (3, "2026-02-09T01:00:00+00:00")]
    assert br.kept(backups, last=0, weekly=2) == {1, 2}


def test_apply_retention_uses_the_policy(database, tmp_path):
    from app.core.backups import _apply_retention

    tmp_path = tmp_path / "bk"
    tmp_path.mkdir()
    with get_conn() as conn:
        for i, ts in _daily(40):
            d = tmp_path / str(i)
            d.mkdir()
            conn.execute(
                "INSERT INTO backups (id, vm_name, chemin, statut, cree_le, mode) VALUES (?, 'web', ?, 'termine', ?, 'froid')",
                (i + 1, str(d), ts),
            )
        conn.commit()
    _apply_retention("web", {"last": 3, "daily": None, "weekly": 3, "monthly": None})
    with get_conn() as conn:
        left = [r["id"] for r in conn.execute("SELECT id FROM backups ORDER BY id")]
    # 3 last (7-9 Feb) plus the newest of 3 ISO weeks: 9 Feb (Monday), 8 Feb (Sunday), 1 Feb (Sunday).
    assert left == [32, 38, 39, 40]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(str(i - 1) for i in left)


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("/srv/backups", True),
        ("/mnt/nas/hv", True),
        ("relative/dir", False),
        ("/srv/../etc", False),
        ("/etc/cron.d", False),
        ("/", False),
        ("/usr/local", False),
    ],
)
def test_backup_targets_stay_out_of_system_directories(path, ok):
    if ok:
        assert bg.validate_target(path) == path
    else:
        with pytest.raises(bg.GroupError):
            bg.validate_target(path)


def _job(**kw):
    return {
        "nom": "nightly",
        "selection": "toutes",
        "frequence": "quotidien",
        "heure": "02:30",
        "cible_dir": "/srv/bk",
        "retention_count": 7,
    } | kw


def test_selection_by_tag_pool_and_exclusion(database, monkeypatch):
    monkeypatch.setattr(bg, "_local_vm_names", lambda: ["api", "db", "web"])
    object_meta.put("vm", "web", "", ["prod"])
    object_meta.put("vm", "db", "", ["prod", "sql"])
    pool = permissions.create_pool("team")
    permissions.add_pool_member(pool, "api")
    tagged = bg.save_job(_job(nom="prod", selection="etiquette", valeur="Prod", exclues=["db"]))
    assert tagged["valeur"] == "prod" and bg.resolve(tagged) == ["web"]
    pooled = bg.save_job(_job(nom="team", selection="pool", valeur=str(pool)))
    assert bg.resolve(pooled) == ["api"]
    assert bg.resolve(bg.save_job(_job())) == ["api", "db", "web"]
    for bad, message in [
        (_job(nom="x", selection="pool", valeur="999"), "existing pool"),
        (_job(nom="x", selection="etiquette", valeur=""), "tag"),
        (_job(nom="x", heure="25:00"), "time"),
        (_job(nom="x", cible_dir="/etc"), "cannot be written"),
        (_job(nom="x", garder_mois=0), "garder_mois"),
        (_job(), "already exists"),
    ]:
        with pytest.raises(bg.GroupError, match=message):
            bg.save_job(bad)


def test_run_backs_up_each_vm_and_applies_the_job_policy_unless_the_vm_has_its_own(database, monkeypatch):
    from app.core import backups, vm_locks

    monkeypatch.setattr(bg, "_local_vm_names", lambda: ["a", "b", "c", "d"])
    calls, retained = [], []

    def fake_backup(vm, target, username="x"):
        calls.append((vm, target))
        if vm == "b":
            raise vm_locks.VmBusy("b", "a migration")
        if vm == "c":
            raise RuntimeError("disk full")
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO backups (vm_name, chemin, statut, cree_le, mode) VALUES (?, '/x', 'termine', '2026-01-01', 'froid')",
                (vm,),
            )
            conn.commit()
        return cur.lastrowid

    monkeypatch.setattr(backups, "run_backup", fake_backup)
    monkeypatch.setattr(backups, "_apply_retention", lambda vm, policy: retained.append((vm, policy)))
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO backup_jobs (vm_name, frequence, heure, cible_dir, retention_count, actif, prochaine_execution) VALUES ('d', 'quotidien', '01:00', '/srv', 3, 1, '2099-01-01')"
        )
        conn.commit()
    job = bg.save_job(_job(garder_semaines=4))
    results = bg.run_job(job)
    assert results == {
        "a": "ok",
        "b": "skipped: VM 'b' is busy: a migration is in progress. Try again when it has finished.",
        "c": "disk full",
        "d": "ok",
    }
    assert [c[1] for c in calls] == ["/srv/bk"] * 4
    assert retained == [("a", {"last": 7, "daily": None, "weekly": 4, "monthly": None})]
    with get_conn() as conn:
        assert [r["groupe_id"] for r in conn.execute("SELECT groupe_id FROM backups ORDER BY id")] == [
            job["id"],
            job["id"],
        ]


def test_api_is_admin_only_and_due_jobs_move_to_their_next_run(client, auth_headers, monkeypatch):
    monkeypatch.setattr(bg, "_local_vm_names", lambda: ["web"])
    admin = auth_headers("root", "admin")
    r = client.post("/backup-groups", json=_job(), headers=admin)
    assert r.status_code == 201, r.text
    job = r.json()
    assert client.get("/backup-groups", headers=admin).json()[0]["vms"] == ["web"]
    assert client.get("/backup-groups", headers=auth_headers("watcher", "observateur")).status_code == 403
    assert client.post("/backup-groups", json=_job(nom="bad", cible_dir="/etc"), headers=admin).status_code == 422

    ran = []
    monkeypatch.setattr(bg, "run_job", lambda j, username="scheduler": ran.append(j["nom"]) or {"web": "ok"})
    now = datetime.fromisoformat(job["prochaine_execution"]) + timedelta(minutes=1)
    bg.run_due(now)
    after = bg.get_job(job["id"])
    assert ran == ["nightly"] and after["derniere_execution"] == now.isoformat()
    assert datetime.fromisoformat(after["prochaine_execution"]) > now
    assert client.delete(f"/backup-groups/{job['id']}", headers=admin).status_code == 200


def test_vm_schedule_accepts_gfs_and_refuses_system_targets(client, auth_headers):
    admin = auth_headers("root", "admin")
    body = {"frequence": "quotidien", "heure": "01:00", "retention_count": 3, "garder_semaines": 4, "garder_mois": 6}
    r = client.put("/vms/web/backup-schedule", json=body, headers=admin)
    assert r.status_code == 200 and (r.json()["garder_semaines"], r.json()["garder_mois"]) == (4, 6)
    assert client.put("/vms/web/backup-schedule", json=body | {"cible_dir": "/etc"}, headers=admin).status_code == 422
