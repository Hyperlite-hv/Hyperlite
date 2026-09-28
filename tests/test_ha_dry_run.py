"""HA dry run: per-node fencing settings (status test only, password never on a command line nor returned), the
watcher's rounds (suspect, failed, libvirt down, back), the decision automatic HA would take (isolation, witness,
fencing, target), and the lease check. Nothing here ever powers off or restarts anything."""

import stat
import textwrap

import pytest

from app.core import ha_fencing, ha_watch, secrets_crypto


def add_node(database, name, statut="en_ligne"):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, statut, added_at) VALUES (?, ?, 'root', 22, ?, '2026-01-01')",
            (name, f"{name}.example.lan", statut),
        )
        db.commit()


def protect(database, vm, node):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) VALUES (?, ?, '<domain/>', 'admin', '2026-01-01')",
            (vm, node),
        )
        db.commit()


def vm_row(database, vm):
    with database.get_conn() as db:
        return dict(db.execute("SELECT * FROM ha_protected_vms WHERE vm_name = ?", (vm,)).fetchone())


# --- Fencing settings -----------------------------------------------------------------------------------------------


def test_fencing_settings_keep_the_password_encrypted_and_never_return_it(database):
    saved = ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw", username="admin")
    assert saved["secret_defini"] is True and "secret" not in saved
    with database.get_conn() as db:
        stored = db.execute("SELECT secret FROM node_fencing WHERE node = 'node2'").fetchone()["secret"]
    assert stored != "s3cret-pw" and secrets_crypto.decrypt(stored) == "s3cret-pw"
    # An update without a password keeps the stored one.
    ha_fencing.save("node2", "redfish", "bmc2.example.lan", 443, "root", None, tls_non_verifie=True)
    assert ha_fencing.get("node2")["methode"] == "redfish" and ha_fencing.get("node2")["secret_defini"]
    # Leases only: no BMC, nothing kept.
    lease = ha_fencing.save("node2", "lease_only")
    assert lease["adresse"] is None and lease["secret_defini"] is False


@pytest.mark.parametrize(
    ("args", "text"),
    [
        (("pdu", "10.0.0.1", None, "a", "p"), "Unknown fencing method"),
        (("ipmi", "bmc; rm -rf /", None, "a", "p"), "host name or an IP"),
        (("ipmi", "10.0.0.1", 70000, "a", "p"), "port"),
        (("ipmi", "10.0.0.1", None, "a b", "p"), "user name"),
        (("amt", "10.0.0.1", None, "admin", None), "password is needed"),
    ],
)
def test_invalid_fencing_settings_are_refused(database, args, text):
    with pytest.raises(ha_fencing.FencingError) as err:
        ha_fencing.save("node2", *args)
    assert text in err.value.message


@pytest.fixture()
def fake_agent(tmp_path, monkeypatch):
    """A fence agent that records its argv and stdin, and answers what the test asks for."""
    script = tmp_path / "fence_ipmilan"

    def install(answer="Status: ON", code=0):
        script.write_text(
            textwrap.dedent(f"""\
            #!/bin/sh
            echo "$@" > {tmp_path}/argv
            cat > {tmp_path}/stdin
            echo "{answer}"
            exit {code}
            """)
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr(ha_fencing.shutil, "which", lambda name: str(script) if name == "fence_ipmilan" else None)
    return install, tmp_path


def test_the_fencing_test_only_reads_the_power_state_with_the_password_on_stdin(database, fake_agent):
    install, tmp = fake_agent
    install("Status: ON")
    ha_fencing.save("node2", "ipmi", "10.0.0.20", 623, "ADMIN", "s3cret-pw")
    assert ha_fencing.test("node2") == {
        "ok": True,
        "alimentation": "on",
        "detail": "Power state read through fence_ipmilan",
    }
    assert (tmp / "argv").read_text().strip() == ""  # nothing on the command line
    options = (tmp / "stdin").read_text().splitlines()
    assert "action=status" in options and "password=s3cret-pw" in options and "lanplus=1" in options
    assert "ipport=623" in options and not any(o.startswith("action=off") or o == "action=reboot" for o in options)


def test_a_failed_test_reports_the_agent_message_without_the_password(database, fake_agent):
    install, _ = fake_agent
    install("Unable to connect/login to fencing device with password s3cret-pw", code=1)
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw")
    result = ha_fencing.test("node2")
    assert result["ok"] is False and "Unable to connect" in result["detail"] and "s3cret-pw" not in result["detail"]


def test_a_missing_agent_says_which_package_to_install(database, monkeypatch):
    monkeypatch.setattr(ha_fencing.shutil, "which", lambda name: None)
    ha_fencing.save("node2", "amt", "pc2.example.lan", None, "admin", "pw")
    assert "apt install fence-agents" in ha_fencing.test("node2")["detail"]


# --- The watcher -----------------------------------------------------------------------------------------------------


@pytest.fixture()
def watch(database, monkeypatch):
    ha_watch._state.clear()
    answers = {}
    monkeypatch.setattr(ha_watch, "probe", lambda node: answers.get(node["name"], (True, True)))
    monkeypatch.setattr(ha_watch, "_tcp", lambda host, port, timeout=3: not host.startswith("node2"))
    monkeypatch.setattr(ha_watch, "witness_reachable", lambda temoin: True if temoin else None)
    sent = []
    monkeypatch.setattr("app.core.notifications.notify", lambda *a, **k: sent.append(a))
    return answers, sent


def rounds(n):
    for _ in range(n):
        ha_watch.tick()


def test_a_silent_node_goes_suspect_then_failed_and_the_decision_is_recorded_not_acted(database, watch):
    answers, sent = watch
    add_node(database, "node2")
    protect(database, "web", "node2")
    answers["node2"] = (False, False)
    rounds(3)
    assert vm_row(database, "web")["etat_ha"] == "suspect"
    rounds(3)
    row = vm_row(database, "web")
    # Two nodes, no witness: automatic restart is structurally off, whatever the fencing.
    assert row["etat_ha"] == "en_panne" and row["derniere_action"].startswith(
        "Manual recovery: Two nodes and no witness"
    )
    assert len(sent) == 1 and sent[0][0] == "ha_alert"
    rounds(3)
    assert len(sent) == 1  # one notification per transition, not per round

    answers["node2"] = (True, True)
    rounds(1)
    assert (
        vm_row(database, "web")["etat_ha"] == "ok"
        and vm_row(database, "web")["derniere_action"] == "Node reachable again"
    )


def test_with_a_witness_and_fencing_it_says_what_it_would_do_and_where(database, watch):
    answers, _ = watch
    add_node(database, "node2")
    add_node(database, "node3")
    protect(database, "db", "node2")
    ha_watch.save_settings("nas.example.lan", 3, 6)
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "pw")
    answers["node2"] = (False, False)
    rounds(6)
    assert vm_row(database, "db")["derniere_action"] == (
        "Would power node2 off through IPMI, confirm it is off, then restart the VM on local (dry run: nothing done)"
    )


def test_without_fencing_or_when_isolated_it_would_not_act(database, watch, monkeypatch):
    answers, _ = watch
    add_node(database, "node2")
    add_node(database, "node3")
    protect(database, "db", "node2")
    ha_watch.save_settings("nas.example.lan", 3, 6)
    answers["node2"] = (False, False)
    rounds(6)
    assert vm_row(database, "db")["derniere_action"] == "Manual recovery: no fencing is set for node2"

    # The controller reaches nobody: it may be the isolated one.
    answers["node2"] = (True, True)
    rounds(1)
    monkeypatch.setattr(ha_watch, "_tcp", lambda host, port, timeout=3: False)
    monkeypatch.setattr(ha_watch, "witness_reachable", lambda temoin: False)
    ha_fencing.save("node2", "lease_only")
    answers["node2"] = (False, False)
    rounds(6)
    assert vm_row(database, "db")["derniere_action"].startswith("No action: this controller reaches only 1 of 4 voters")


def test_libvirt_down_with_ssh_up_is_not_a_failure(database, watch):
    answers, sent = watch
    add_node(database, "node2")
    protect(database, "web", "node2")
    answers["node2"] = (False, True)
    rounds(10)
    row = vm_row(database, "web")
    assert row["etat_ha"] == "libvirt_injoignable" and "most likely still run" in row["derniere_action"]
    assert sent == []


def test_settings_are_validated(database):
    assert ha_watch.save_settings("192.0.2.5:22", 2, 5)["temoin"] == "192.0.2.5:22"
    for bad in (("nas; reboot", 3, 6), ("nas.lan", 6, 3), ("nas.lan:ssh", 3, 6)):
        with pytest.raises(ValueError):
            ha_watch.save_settings(*bad)


@pytest.mark.parametrize(
    ("qemu", "lockd", "active", "text"),
    [
        ('lock_manager = "lockd"\n', 'file_lockspace_dir = "/mnt/nfs/lockd"\n', True, "/mnt/nfs/lockd"),
        ('#lock_manager = "lockd"\n', "", False, "is not set"),
        ('lock_manager = "lockd"\n', '#file_lockspace_dir = "/x"\n', False, "file_lockspace_dir"),
    ],
)
def test_the_lease_check_reads_the_libvirt_settings(qemu, lockd, active, text):
    result = ha_watch._lockd_state(qemu, lockd)
    assert result["actif"] is active and text in result["detail"]


# --- API ---------------------------------------------------------------------------------------------------------------


def test_fencing_endpoints_are_admin_only_and_hide_the_password(client, auth_headers, database, fake_agent):
    install, _ = fake_agent
    install("Status: OFF")
    add_node(database, "node2")
    admin = auth_headers("admin1")
    r = client.put(
        "/ha/fencing/node2",
        json={"methode": "ipmi", "adresse": "10.0.0.20", "utilisateur": "ADMIN", "secret": "pw"},
        headers=admin,
    )
    assert r.status_code == 200 and "secret" not in r.json() and r.json()["secret_defini"]
    assert client.post("/ha/fencing/node2/test", headers=admin).json()["alimentation"] == "off"
    assert client.put("/ha/fencing/ghost", json={"methode": "lease_only"}, headers=admin).status_code == 404
    status = client.get("/ha/status", headers=admin).json()
    assert status["mode"] == "essai" and status["redemarrage_auto_possible"] is False
    watcher = auth_headers("watcher", role="observateur")
    assert client.get("/ha/fencing", headers=watcher).status_code == 403
    assert client.post("/ha/fencing/node2/test", headers=watcher).status_code == 403
    assert client.get("/ha/status", headers=watcher).status_code == 200
