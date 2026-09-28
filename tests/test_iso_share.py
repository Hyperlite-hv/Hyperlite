"""ISO library across the cluster: listing every node, copying between nodes, deleting on a node.

Remote nodes are simulated: ssh and scp are replaced by fakes backed by one directory per node, so the tests cover
the orchestration (validation, temporary name then rename, tasks, errors) without any real host.
"""

import time
from pathlib import Path

import pytest


def _wait_tasks(database, task_ids, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with database.get_conn() as conn:
            rows = [
                conn.execute("SELECT id, statut, erreur FROM tasks WHERE id = ?", (tid,)).fetchone() for tid in task_ids
            ]
        if all(r is not None and r["statut"] != "en_cours" for r in rows):
            return {r["id"]: dict(r) for r in rows}
        time.sleep(0.02)
    raise AssertionError("copy tasks did not finish")


def test_iso_names_are_restricted_to_plain_file_names():
    from app.core.iso_share import valid_iso_name

    assert valid_iso_name("debian-12.7.0-amd64-netinst.iso")
    assert valid_iso_name("Win11_24H2.ISO")
    for bad in ["", "../x.iso", "a/b.iso", "x.img", ".hidden.iso", "a b.iso", "x;rm -rf.iso", "$(id).iso"]:
        assert not valid_iso_name(bad), bad


def test_the_cluster_list_shows_every_node_and_reports_the_ones_that_cannot_be_read(cluster, client, auth_headers):
    (cluster["local"] / "debian.iso").write_bytes(b"d" * 10)
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u" * 20)
    cluster["add_node"]("down", "10.0.0.3", reachable=False)
    cluster["add_node"]("bare", "10.0.0.4", library=False)

    r = client.get("/isos/cluster", headers=auth_headers("viewer", role="observateur"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted((i["node"], i["nom"]) for i in body["isos"]) == [("antho", "ubuntu.iso"), ("local", "debian.iso")]
    errors = {u["node"]: u["erreur"] for u in body["injoignables"]}
    assert set(errors) == {"down", "bare"}
    assert "no Hyperlite ISO library" in errors["bare"]


def test_the_local_list_keeps_its_shape_and_names_the_node(cluster, client, auth_headers):
    (cluster["local"] / "debian.iso").write_bytes(b"d")
    r = client.get("/isos", headers=auth_headers("viewer", role="observateur"))
    assert r.status_code == 200
    [iso] = r.json()
    assert iso["nom"] == "debian.iso" and iso["node"] == "local"
    assert {"taille_mo", "ajoutee_le", "emplacement"} <= set(iso)


def test_copying_to_several_nodes_lands_complete_files_under_their_final_name(cluster, client, auth_headers, database):
    (cluster["local"] / "debian.iso").write_bytes(b"x" * 4096)
    antho = cluster["add_node"]("antho", "10.0.0.2")
    other = cluster["add_node"]("other", "10.0.0.5")

    r = client.post(
        "/isos/copy",
        json={"nom": "debian.iso", "source": "local", "cibles": ["antho", "other"]},
        headers=auth_headers("admin"),
    )
    assert r.status_code == 202, r.text
    tasks = r.json()["taches"]
    assert [t["node"] for t in tasks] == ["antho", "other"]
    done = _wait_tasks(database, [t["task_id"] for t in tasks])
    assert all(t["statut"] == "termine" for t in done.values()), done

    for lib in (antho, other):
        assert (lib / "debian.iso").read_bytes() == b"x" * 4096
        assert not list(lib.glob(".*part"))
    # The transfer writes a hidden temporary name, never the final one directly.
    assert all(Path(dst.split(":")[-1]).name == ".debian.iso.part" for _, dst in cluster["scp_calls"])


def test_an_image_can_be_brought_from_a_node_to_this_host(cluster, client, auth_headers, database):
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u" * 100)
    r = client.post(
        "/isos/copy", json={"nom": "ubuntu.iso", "source": "antho", "cibles": ["local"]}, headers=auth_headers("admin")
    )
    assert r.status_code == 202, r.text
    _wait_tasks(database, [r.json()["taches"][0]["task_id"]])
    assert (cluster["local"] / "ubuntu.iso").read_bytes() == b"u" * 100
    assert not list(cluster["local"].glob(".*"))


def test_a_copy_between_two_remote_nodes_is_relayed_and_leaves_nothing_behind(cluster, client, auth_headers, database):
    (cluster["add_node"]("antho", "10.0.0.2") / "alma.iso").write_bytes(b"a" * 50)
    other = cluster["add_node"]("other", "10.0.0.5")
    r = client.post(
        "/isos/copy", json={"nom": "alma.iso", "source": "antho", "cibles": ["other"]}, headers=auth_headers("admin")
    )
    assert r.status_code == 202, r.text
    _wait_tasks(database, [r.json()["taches"][0]["task_id"]])
    assert (other / "alma.iso").read_bytes() == b"a" * 50
    assert not list(cluster["local"].iterdir())


def test_a_failed_copy_is_reported_and_leaves_no_partial_file(cluster, client, auth_headers, database, monkeypatch):
    (cluster["local"] / "debian.iso").write_bytes(b"x" * 10)
    lib = cluster["add_node"]("antho", "10.0.0.2")
    from app.core import iso_share

    def broken_scp(args, timeout=None):
        dst = Path(args[-1].split(":")[-1])
        (lib / dst.name).write_bytes(b"half")
        raise RuntimeError("Connection reset by peer")

    monkeypatch.setattr(iso_share, "_scp", broken_scp)
    r = client.post("/isos/copy", json={"nom": "debian.iso", "cibles": ["antho"]}, headers=auth_headers("admin"))
    task = _wait_tasks(database, [r.json()["taches"][0]["task_id"]]).popitem()[1]
    assert task["statut"] == "echec" and "Connection reset" in task["erreur"]
    assert list(lib.iterdir()) == []


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"nom": "../etc/passwd.iso", "cibles": ["antho"]}, 422),
        ({"nom": "debian.iso", "cibles": []}, 422),
        ({"nom": "debian.iso", "cibles": ["local"]}, 422),
        ({"nom": "debian.iso", "cibles": ["ghost"]}, 404),
        ({"nom": "missing.iso", "cibles": ["antho"]}, 404),
        ({"nom": "debian.iso", "cibles": ["dup"]}, 409),
    ],
)
def test_copy_requests_are_validated_before_anything_starts(cluster, client, auth_headers, payload, status):
    (cluster["local"] / "debian.iso").write_bytes(b"x")
    cluster["add_node"]("antho", "10.0.0.2")
    (cluster["add_node"]("dup", "10.0.0.6") / "debian.iso").write_bytes(b"x")
    r = client.post("/isos/copy", json=payload, headers=auth_headers("admin"))
    assert r.status_code == status, r.text
    assert cluster["scp_calls"] == []


def test_only_an_administrator_can_copy(cluster, client, auth_headers):
    (cluster["local"] / "debian.iso").write_bytes(b"x")
    cluster["add_node"]("antho", "10.0.0.2")
    r = client.post(
        "/isos/copy",
        json={"nom": "debian.iso", "cibles": ["antho"]},
        headers=auth_headers("viewer", role="observateur"),
    )
    assert r.status_code == 403


class _NoVmConn:
    def listAllDomains(self):
        return []

    def close(self):
        pass


def test_an_image_can_be_deleted_on_a_remote_node(cluster, client, auth_headers, monkeypatch):
    from app.routers import isos

    lib = cluster["add_node"]("antho", "10.0.0.2")
    (lib / "ubuntu.iso").write_bytes(b"u")
    opened = []
    monkeypatch.setattr(isos, "open_conn", lambda node=None: opened.append(node) or _NoVmConn())
    headers = auth_headers("admin")

    assert client.delete("/isos/ubuntu.iso?node=antho", headers=headers).status_code == 400
    r = client.delete("/isos/ubuntu.iso?node=antho&confirm=true", headers=headers)
    assert r.status_code == 200, r.text
    assert not (lib / "ubuntu.iso").exists()
    assert opened == ["antho"]  # the in-use check reads that node's own VMs
    assert client.delete("/isos/ubuntu.iso?node=ghost&confirm=true", headers=headers).status_code == 404
