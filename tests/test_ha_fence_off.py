"""The fence itself (docs/design/ha-automatic.md, 4.2, #333 step 2): a node powered off through its BMC or AMT, its power
state read until it says off, never a reboot; and the manual recovery that relies on it."""

import stat
import textwrap

import pytest

from app.core import ha, ha_fencing


@pytest.fixture()
def bmc(tmp_path, monkeypatch):
    """A fence agent standing for a BMC: `off` powers the node off (or not, when it is broken), `status` reads it."""
    script = tmp_path / "fence_ipmilan"

    def install(obeys=True):
        power_off = f"touch {tmp_path}/off" if obeys else "true"
        script.write_text(
            textwrap.dedent(f"""\
            #!/bin/sh
            action=$(grep '^action=' | cut -d= -f2)
            echo "$action" >> {tmp_path}/actions
            case "$action" in
              off) {power_off}; echo "Success: Powered OFF (password s3cret-pw)";;
              status) if [ -e {tmp_path}/off ]; then echo "Status: OFF"; else echo "Status: ON"; fi;;
              *) echo "unexpected action"; exit 1;;
            esac
            """)
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr(ha_fencing.shutil, "which", lambda name: str(script) if name == "fence_ipmilan" else None)
    monkeypatch.setattr(ha_fencing, "OFF_CONFIRM_S", 0.3)
    monkeypatch.setattr(ha_fencing, "OFF_POLL_S", 0.05)
    return install, tmp_path


def actions(tmp):
    return (tmp / "actions").read_text().split()


def test_a_node_is_fenced_once_its_power_state_reads_off(database, bmc):
    install, tmp = bmc
    install()
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw")
    assert ha_fencing.power_off("node2") == {
        "fenced": True,
        "detail": "Powered off through fence_ipmilan, its power state read as off",
    }
    assert actions(tmp) == ["off", "status"] and "reboot" not in actions(tmp)


def test_a_bmc_that_does_not_power_off_is_no_fence(database, bmc):
    install, tmp = bmc
    install(obeys=False)
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw")
    result = ha_fencing.power_off("node2")
    assert result["fenced"] is False and "Not confirmed off" in result["detail"] and "s3cret-pw" not in result["detail"]
    assert actions(tmp)[0] == "off" and set(actions(tmp)[1:]) == {"status"}  # read again, never rebooted


def test_no_agent_or_no_bmc_is_no_fence(database, monkeypatch):
    monkeypatch.setattr(ha_fencing.shutil, "which", lambda name: None)
    assert ha_fencing.power_off("node2")["detail"] == "No BMC or AMT is set for this node"
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw")
    monkeypatch.setattr(ha_fencing, "OFF_CONFIRM_S", 0)
    result = ha_fencing.power_off("node2")
    assert result["fenced"] is False and "apt install fence-agents-ipmilan" in result["detail"]


@pytest.fixture()
def protected(database, monkeypatch):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) "
            "VALUES ('web', 'node2', '<domain><name>web</name></domain>', 'admin', '2026-01-01')"
        )
        db.commit()
    started = []

    class Domain:
        def create(self):
            started.append("web")

        def XMLDesc(self, flags=0):
            return "<domain><name>web</name></domain>"

        def setAutostart(self, on):
            pass

    class Conn:
        def lookupByName(self, name):
            import libvirt

            raise libvirt.libvirtError("Domain not found")

        def defineXML(self, xml):
            return Domain()

        def close(self):
            pass

    monkeypatch.setattr(ha, "open_conn", lambda key=None, **_k: Conn())
    monkeypatch.setattr(ha, "_attempt_ssh_fence", lambda node, vm: pytest.fail("SSH fence used despite a BMC"))
    return started


def test_a_recovery_through_a_bmc_restarts_the_vm_once_its_node_is_off(database, bmc, protected):
    install, _ = bmc
    install()
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw")
    ha.recover("web", "node3", "admin")
    assert protected == ["web"]


def test_a_recovery_is_refused_while_the_bmc_does_not_confirm_the_node_off(database, bmc, protected):
    install, _ = bmc
    install(obeys=False)
    ha_fencing.save("node2", "ipmi", "10.0.0.20", None, "ADMIN", "s3cret-pw")
    with pytest.raises(RuntimeError, match="not confirmed powered off"):
        ha.recover("web", "node3", "admin")
    assert protected == []  # never started elsewhere
