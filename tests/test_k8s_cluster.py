"""Kubernetes (k3s) clusters on VMs: checks, verified downloads, the provisioning sequence, kubeconfig, deletion.

VM creation, SSH and the k3s release server are replaced by fakes: what is tested is the orchestration and the
safety rules (checksums, secrets kept off command lines, admin-only kubeconfig), not libvirt or k3s themselves.
"""

import hashlib
import io
import time

import pytest

SAMPLE_KUBECONFIG = """apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: AAAA
    server: https://127.0.0.1:6443
  name: default
contexts:
- context:
    cluster: default
    user: default
  name: default
current-context: default
kind: Config
users:
- name: default
  user:
    client-key-data: BBBB
"""


class _Net:
    def __init__(self, active=True):
        self.active = active

    def isActive(self):
        return self.active


class _Conn:
    def __init__(self, vms=(), networks=None):
        self.vms = set(vms)
        self.networks = networks if networks is not None else {"default": _Net()}

    def lookupByName(self, name):
        import libvirt

        if name not in self.vms:
            raise libvirt.libvirtError("not found")
        return name

    def networkLookupByName(self, name):
        import libvirt

        if name not in self.networks:
            raise libvirt.libvirtError("not found")
        return self.networks[name]

    def close(self):
        pass


@pytest.fixture()
def k8s(database, monkeypatch, tmp_path):
    from app.core import k8s_cluster, libvirt_utils, vm_limits

    conn = _Conn()
    monkeypatch.setattr(libvirt_utils, "open_conn", lambda node=None: conn)
    monkeypatch.setattr(vm_limits, "validate_vm_resources", lambda *a, **k: [])
    monkeypatch.setattr(k8s_cluster, "ARTIFACTS_DIR", tmp_path / "k3s")
    calls = []
    monkeypatch.setattr(k8s_cluster, "stable_version", lambda: "v1.36.4+k3s1")
    monkeypatch.setattr(k8s_cluster, "ensure_artifacts", lambda tag: tmp_path)
    monkeypatch.setattr(k8s_cluster, "_create_vm", lambda vm, *a: calls.append(("create", vm)))
    monkeypatch.setattr(k8s_cluster, "_start_vm", lambda vm: calls.append(("start", vm)))
    ips = {}
    monkeypatch.setattr(k8s_cluster, "wait_ssh", lambda vm: ips.setdefault(vm, f"10.0.0.{len(ips) + 10}"))

    def install(vm, folder, token, role, ip, server_ip=None):
        calls.append(("install", vm, role, ip, server_ip, len(token)))

    monkeypatch.setattr(k8s_cluster, "install_k3s", install)
    monkeypatch.setattr(k8s_cluster, "wait_nodes_ready", lambda vm, n: calls.append(("ready", vm, n)))
    monkeypatch.setattr(
        k8s_cluster,
        "vm_run",
        lambda vm, script, secret="", timeout=0: SAMPLE_KUBECONFIG if "k3s.yaml" in script else "",
    )
    return {"module": k8s_cluster, "conn": conn, "calls": calls}


def _wait_status(client, headers, name, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = client.get("/kubernetes/clusters", headers=headers).json()
        row = next((r for r in rows if r["nom"] == name), None)
        if row and row["statut"] != "creation":
            return row
        time.sleep(0.02)
    raise AssertionError("cluster provisioning did not finish")


def test_a_cluster_is_built_server_first_then_workers_and_hands_over_a_kubeconfig(k8s, client, auth_headers, database):
    admin = auth_headers("admin")
    r = client.post("/kubernetes/clusters", json={"nom": "demo", "workers": 2}, headers=admin)
    assert r.status_code == 202, r.text
    row = _wait_status(client, admin, "demo")
    assert row["statut"] == "pret", row
    assert row["serveur"] == "demo-server" and row["workers"] == ["demo-worker-1", "demo-worker-2"]
    assert row["version"] == "v1.36.4+k3s1" and row["adresse"] == "10.0.0.10"
    assert row["kubeconfig_disponible"] is True and "jeton" not in row and "kubeconfig" not in row

    installs = [c for c in k8s["calls"] if c[0] == "install"]
    assert [(c[1], c[2]) for c in installs] == [
        ("demo-server", "server"),
        ("demo-worker-1", "agent"),
        ("demo-worker-2", "agent"),
    ]
    assert all(c[4] == "10.0.0.10" for c in installs[1:])  # workers join the server's address
    assert installs[0][5] == 64  # a 256-bit random token
    assert ("ready", "demo-server", 3) in k8s["calls"]

    config = client.get("/kubernetes/clusters/demo/kubeconfig", headers=admin)
    assert config.status_code == 200
    assert "https://10.0.0.10:6443" in config.text and "127.0.0.1" not in config.text
    assert "current-context: demo" in config.text and "name: default" not in config.text
    assert 'filename="demo.kubeconfig"' in config.headers["content-disposition"]

    from app.core import audit as audit_module

    audit_module._AUDIT_QUEUE.join()  # audit entries are written by a background thread
    with database.get_conn() as conn:
        stored = conn.execute("SELECT jeton, kubeconfig FROM k8s_clusters WHERE nom = 'demo'").fetchone()
        audit = [r["action"] for r in conn.execute("SELECT action FROM audit_log").fetchall()]
    assert "BBBB" not in stored["kubeconfig"] and len(stored["jeton"]) > 64  # encrypted at rest
    assert "download_kubeconfig" in audit


def test_a_failure_names_the_step_and_leaves_the_cluster_in_error(k8s, client, auth_headers, monkeypatch):
    def broken(vm, folder, token, role, ip, server_ip=None):
        raise RuntimeError("demo-server: sudo: unable to resolve host")

    monkeypatch.setattr(k8s["module"], "install_k3s", broken)
    admin = auth_headers("admin")
    client.post("/kubernetes/clusters", json={"nom": "broken", "workers": 1}, headers=admin)
    row = _wait_status(client, admin, "broken")
    assert row["statut"] == "echec"
    assert row["erreur"].startswith("installing the k3s server:")
    assert client.get("/kubernetes/clusters/broken/kubeconfig", headers=admin).status_code == 409


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"nom": "Bad_Name"}, 422),
        ({"nom": "ab"}, 422),
        ({"nom": "demo", "workers": 0}, 422),
        ({"nom": "demo", "workers": 6}, 422),
        ({"nom": "demo", "memoire_mo": 512}, 422),
        ({"nom": "demo", "disque_go": 5}, 422),
        ({"nom": "demo", "reseau": "nope"}, 422),
        ({"nom": "demo", "reseau": "down"}, 422),
        ({"nom": "taken"}, 409),
    ],
)
def test_requests_are_checked_before_anything_is_created(k8s, client, auth_headers, payload, status):
    k8s["conn"].networks["down"] = _Net(active=False)
    k8s["conn"].vms.add("taken-worker-2")
    r = client.post("/kubernetes/clusters", json=payload, headers=auth_headers("admin"))
    assert r.status_code == status, r.text
    assert k8s["calls"] == []
    assert client.get("/kubernetes/clusters", headers=auth_headers("viewer", role="observateur")).json() == []


def test_only_administrators_create_clusters_or_read_their_credentials(k8s, client, auth_headers):
    viewer = auth_headers("viewer", role="observateur")
    assert client.post("/kubernetes/clusters", json={"nom": "demo"}, headers=viewer).status_code == 403
    client.post("/kubernetes/clusters", json={"nom": "demo", "workers": 1}, headers=auth_headers("admin"))
    _wait_status(client, viewer, "demo")
    assert client.get("/kubernetes/clusters/demo/kubeconfig", headers=viewer).status_code == 403


def test_deleting_a_cluster_removes_its_vms_and_its_record(k8s, client, auth_headers, monkeypatch):
    from app.routers.vms import lifecycle

    admin = auth_headers("admin")
    client.post("/kubernetes/clusters", json={"nom": "demo", "workers": 1}, headers=admin)
    _wait_status(client, admin, "demo")

    deleted = []

    class _Dom:
        def __init__(self, name):
            self.name = name

        def isActive(self):
            return True

        def destroy(self):
            deleted.append(("destroy", self.name))

    k8s["conn"].vms.update({"demo-server", "demo-worker-1"})
    monkeypatch.setattr(k8s["conn"], "lookupByName", _Dom)
    monkeypatch.setattr(lifecycle, "delete_vm", lambda vm, confirm, node, user: deleted.append(("delete", vm)))

    assert client.delete("/kubernetes/clusters/demo", headers=admin).status_code == 400
    r = client.delete("/kubernetes/clusters/demo?confirm=true", headers=admin)
    assert r.status_code == 200, r.text
    assert deleted == [
        ("destroy", "demo-server"),
        ("delete", "demo-server"),
        ("destroy", "demo-worker-1"),
        ("delete", "demo-worker-1"),
    ]
    assert client.get("/kubernetes/clusters", headers=admin).json() == []
    assert client.delete("/kubernetes/clusters/demo?confirm=true", headers=admin).status_code == 404


def test_a_restart_during_provisioning_is_reported(k8s, database):
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO k8s_clusters (nom, reseau, serveur, workers, vcpu, memoire_mo, disque_go, statut, cree_le) "
            "VALUES ('half', 'default', 'half-server', '[]', 2, 2048, 20, 'creation', 'now')"
        )
        conn.commit()
    k8s["module"].recover_interrupted()
    row = k8s["module"].get_cluster("half")
    assert row["statut"] == "echec" and "restart" in row["erreur"]


def test_the_join_token_never_appears_on_a_command_line(database, monkeypatch):
    from app.core import k8s_cluster

    seen = {}
    monkeypatch.setattr(k8s_cluster, "_ssh_target", lambda vm: ("10.0.0.10", "kube", ["-o", "BatchMode=yes"]))

    class _R:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(args, **kwargs):
        seen["args"], seen["input"] = args, kwargs.get("input")
        return _R()

    monkeypatch.setattr(k8s_cluster.subprocess, "run", fake_run)
    k8s_cluster.vm_run("demo-server", 'echo "$HL_SECRET" > /dev/null', secret="s3cr3t-token")
    assert not any("s3cr3t-token" in a for a in seen["args"])
    assert seen["input"].startswith("s3cr3t-token\n")


def test_k3s_downloads_are_verified_and_cached(database, monkeypatch, tmp_path):
    from app.core import k8s_cluster

    blobs = {"k3s": b"binary", "k3s-airgap-images-amd64.tar.zst": b"images"}
    sums = "".join(f"{hashlib.sha256(v).hexdigest()}  {k}\n" for k, v in blobs.items())
    fetched = []

    def urlopen(url, timeout=0):
        name = url.rsplit("/", 1)[-1]
        fetched.append(name)
        return io.BytesIO(sums.encode() if name == "sha256sum-amd64.txt" else blobs[name])

    monkeypatch.setattr(k8s_cluster, "ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(k8s_cluster.urllib.request, "urlopen", urlopen)
    folder = k8s_cluster.ensure_artifacts("v1.36.4+k3s1")
    assert (folder / "k3s").read_bytes() == b"binary" and (folder / "k3s").stat().st_mode & 0o111
    assert "v1.36.4%2Bk3s1" not in str(folder)  # the folder keeps the plain tag
    fetched.clear()
    k8s_cluster.ensure_artifacts("v1.36.4+k3s1")
    assert fetched == ["sha256sum-amd64.txt"]  # cached files are only re-checked

    blobs["k3s"] = b"tampered"
    (folder / "k3s").unlink()
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        # the checksum file still lists the original binary
        sums_ok = sums
        monkeypatch.setattr(
            k8s_cluster.urllib.request,
            "urlopen",
            lambda url, timeout=0: io.BytesIO(
                sums_ok.encode() if url.endswith(".txt") else blobs[url.rsplit("/", 1)[-1]]
            ),
        )
        k8s_cluster.ensure_artifacts("v1.36.4+k3s1")
    assert not (folder / "k3s").exists() and not list(folder.glob(".*part"))
    with pytest.raises(RuntimeError, match="Unexpected k3s version"):
        k8s_cluster.ensure_artifacts("v1.36.4; rm -rf /")


def test_flannel_is_given_the_interface_that_holds_the_node_address(database, monkeypatch):
    """On an isolated network a VM has no default route, and k3s stops if flannel has to guess its interface."""
    from app.core import k8s_cluster

    answers = {"ok": "ens3\n", "vlan": "ens3.10@ens3\n", "none": "", "odd": "eth0; rm -rf /\n"}
    for case, expected in (("ok", "ens3"), ("vlan", "ens3.10")):
        monkeypatch.setattr(k8s_cluster, "vm_run", lambda vm, script, secret="", timeout=0, c=case: answers[c])
        assert k8s_cluster.interface_of("demo-server", "192.168.100.10") == expected
    for case in ("none", "odd"):
        monkeypatch.setattr(k8s_cluster, "vm_run", lambda vm, script, secret="", timeout=0, c=case: answers[c])
        with pytest.raises(RuntimeError, match="no network interface"):
            k8s_cluster.interface_of("demo-server", "192.168.100.10")
