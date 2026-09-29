"""The CSV exports of the audit log and of the tasks hold EVERY matching row, not the page on screen."""

import csv
import io

import pytest

from app.core import csv_export


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def _insert_audit(database, n, action="start_vm", resource="vm1", result="succes"):
    with database.get_conn() as conn:
        conn.executemany(
            "INSERT INTO audit_log (timestamp, username, action, resource, result) VALUES (?, 'bob', ?, ?, ?)",
            [(f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}", action, resource, result) for i in range(n)],
        )
        conn.commit()


@pytest.fixture()
def small_batches(monkeypatch):
    monkeypatch.setattr(csv_export, "BATCH", 7)


def test_the_audit_export_holds_every_matching_entry(client, auth_headers, database, small_batches):
    headers = auth_headers("alice")
    _insert_audit(database, 50)
    _insert_audit(database, 5, action="delete_vm", result="echec")
    r = client.get("/audit/export.csv", params={"action": "start_vm"}, headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = _rows(r.text)
    assert rows[0] == ["timestamp", "user", "action", "resource", "result", "error", "ip"]
    body = rows[1:]
    assert len(body) == 50  # more than one batch, no duplicate, nothing lost
    assert {row[2] for row in body} == {"start_vm"}
    assert [row[0] for row in body] == sorted((row[0] for row in body), reverse=True)


def test_the_audit_export_is_for_administrators_only(client, auth_headers):
    r = client.get("/audit/export.csv", headers=auth_headers("olga", role="observateur"))
    assert r.status_code == 403


def test_a_formula_in_a_name_is_neutralised(client, auth_headers, database):
    _insert_audit(database, 1, resource='=HYPERLINK("http://x")')
    rows = _rows(client.get("/audit/export.csv", headers=auth_headers("alice")).text)
    assert any(row[3] == '\'=HYPERLINK("http://x")' for row in rows[1:])


def test_the_task_export_holds_every_matching_task(client, auth_headers, database, small_batches):
    from app.core.tasks import create_task, finish_task

    headers = auth_headers("alice")
    for i in range(20):
        finish_task(create_task("create_vm", f"vm{i}", node="hv", username="alice"), "termine")
    create_task("delete_vm", "other", node="hv", username="alice")
    r = client.get("/tasks/export.csv", params={"type": "create_vm"}, headers=headers)
    assert r.status_code == 200
    rows = _rows(r.text)
    assert rows[0][:4] == ["id", "created", "type", "target"]
    assert len(rows) == 21
    assert {row[2] for row in rows[1:]} == {"create_vm"}
    assert {row[6] for row in rows[1:]} == {"termine"}
