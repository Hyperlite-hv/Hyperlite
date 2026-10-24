import contextlib
import logging
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException

from app.core import (
    guest_agent,
    hookscript,
    privileged,
    vm_delete,
    vm_locks,
    vm_records,
)
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_vm_privilege
from app.core.tasks import create_task
from app.core.vm_meta import (
    touch_vm_activity,
)
from app.daemon.registry import DaemonError
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


def _power(domain, node, action, username):
    """A VM started, shut down, stopped, rebooted or reset: by hyperlite-daemon on this node (app/daemon/ops/vm.py),
    over another node's own libvirt otherwise, until the cluster domain. Returns the method of a shutdown or a reboot
    ("agent" or "acpi"). Raises libvirt.libvirtError or DaemonError, which _power_failure turns into an answer."""
    if node and node != "local":
        if action == "start":
            domain.create()
        elif action == "shutdown":
            return guest_agent.shutdown(domain)
        elif action == "reboot":
            return guest_agent.reboot(domain)
        else:
            domain.destroy()
            if action == "reset":
                domain.create()
        return None
    return privileged.run("vm.power", user=username, vm=domain.name(), action=action)["method"]


def _power_failure(e):
    """(status, reason) of a failed _power: libvirt's reason, 500; the daemon's refusal or absence, its own status."""
    if isinstance(e, DaemonError):
        return (500 if e.kind == "failed" else privileged.http_error(e).status_code), e.detail
    return 500, describe_exception(e)


def _stopped_networks(conn, domain):
    """The libvirt networks the VM has an interface on that are not running, in order."""
    try:
        root = ET.fromstring(domain.XMLDesc(0))
    except (libvirt.libvirtError, ET.ParseError):
        return []
    names = dict.fromkeys(s.get("network") for s in root.findall("./devices/interface/source") if s.get("network"))
    stopped = []
    for net in names:
        try:
            if not conn.networkLookupByName(net).isActive():
                stopped.append(net)
        except libvirt.libvirtError:
            stopped.append(net)  # gone: libvirt would refuse too
    return stopped


def _admit_ha_start(name, node, domain, user, task_id):
    """An HA VM about to start counts in HA's admission control (app/core/ha_admission.py): refused under "strict"
    when the loss of a member would then leave no room for its HA VMs; said in the log under "warn"."""
    from app.core import ha, ha_admission, ha_recovery

    if not ha.get_protected(name):
        return
    try:
        result = ha_admission.refuse_if_strict((name, node, ha_recovery.memory_mib(domain.XMLDesc(0)), True))
    except RuntimeError as e:
        log_action(user["username"], "start_vm", name, "failed", str(e), task_id=task_id)
        raise HTTPException(status_code=409, detail=str(e)) from None
    if result["detail"]:
        logger.warning("Starting HA VM %s: %s", name, result["detail"])


def _has_hookscript(domain):
    try:
        return hookscript.script_of_domain_xml(ET.fromstring(domain.XMLDesc(0))) is not None
    except (libvirt.libvirtError, ET.ParseError):
        return False


def _pre_stop(domain, name, node, task_id, username, action_name, force):
    """The VM's pre-stop hook script, before Hyperlite stops it (libvirt has no hook there). A failure refuses the
    shutdown or the stop; a forced one goes on, as an administrator must always be able to stop a VM."""
    try:
        if node and node != "local":
            run = hookscript.pre_stop(domain, node, task_id)
        else:
            # Run as root by hyperlite-daemon (app/daemon/ops/vm.py); its output goes into the task here.
            run = privileged.run("vm.pre_stop", user=username, vm=domain.name())
            if run is not None:
                hookscript.log_run(task_id, run)
    except (hookscript.HookError, OSError, ValueError, subprocess.SubprocessError, DaemonError) as e:
        run = {"ok": False, "exit_code": None, "phase": "pre-stop", "output": str(e), "seconds": 0}
        hookscript.log_run(task_id, run)
    if run is None or run["ok"] or force:
        return
    msg = hookscript.failure_message(run)
    log_action(username, action_name, name, "failed", msg, task_id=task_id)
    raise HTTPException(status_code=409, detail=f"{msg}. A forced stop runs it and stops the VM anyway.")


@router.post("/{name}/start")
def start_vm(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))):
    conn = open_conn(node)
    task_id = create_task("start_vm", name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "start_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "start_vm", name, "failed", "VM already running", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already running")
        # A restore, a cold backup, a disk move or a clone works on the stopped VM's disks: started meanwhile, the VM
        # would write to files being copied or replaced (a restore then deletes them), as Proxmox's lock prevents.
        busy = vm_locks.running(name, node)
        if busy:
            msg = f"VM '{name}' is busy: {busy} is in progress. Start it once it has finished."
            log_action(user["username"], "start_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=409, detail=msg)
        _admit_ha_start(name, node, domain, user, task_id)
        stopped = _stopped_networks(conn, domain)
        if stopped:
            # libvirt's own refusal named no remedy ("network 'default' is not active").
            msg = (
                f"The network {', '.join(repr(n) for n in stopped)} of this VM is stopped: start it on the Network page"
            )
            log_action(user["username"], "start_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=409, detail=msg)
        hooked, since = _has_hookscript(domain), time.time()
        try:
            _power(domain, node, "start", user["username"])
        except (libvirt.libvirtError, DaemonError) as e:
            status, msg = _power_failure(e)
            if hooked:
                # A pre-start that refused: its own words, not libvirt's command line, and a refusal rather than an
                # internal error.
                if hookscript.start_error(msg) != msg:
                    status = 409
                msg = hookscript.start_error(msg)
                hookscript.log_runs_since(task_id, name, since, node)
            log_action(user["username"], "start_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=status, detail=f"Unable to start the VM: {msg}") from e
        if hooked:
            hookscript.log_runs_since(task_id, name, since, node)
        touch_vm_activity(name)  # resets the inactivity counter
        log_action(user["username"], "start_vm", name, "success", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


# A guest may ignore the shutdown request (the ACPI button pressed while it is still booting, or just after a live
# migration: twice in a 24 h lab run, the guest healthy and stopping at once on a second request): the VM then kept
# running, and the request said nothing. The request is sent again after ASK_AGAIN_S while the VM runs; asked with
# force_after, the VM still running at the end is stopped by force, as Proxmox's shutdown timeout with forceStop.
FORCE_AFTER_RANGE = (10, 3600)
ASK_AGAIN_S = (30, 90)


def _watch_stop(name, node, username, force_after=None):
    from app.core.libvirt_utils import open_conn as _open

    asks = [t for t in ASK_AGAIN_S if force_after is None or t < force_after]
    if force_after and not asks:
        asks = [force_after / 2]
    end = force_after or ASK_AGAIN_S[-1] + 1
    start = time.monotonic()
    while time.monotonic() - start < end:
        time.sleep(2)
        conn = _open(node)
        try:
            domain = conn.lookupByName(name)
            if not domain.isActive():
                return
            if asks and time.monotonic() - start >= asks[0]:
                asks.pop(0)
                with contextlib.suppress(libvirt.libvirtError, DaemonError):
                    _power(domain, node, "shutdown", username)
        except libvirt.libvirtError:
            return  # gone meanwhile
        finally:
            conn.close()
    if not force_after:
        return
    conn = _open(node)
    try:
        domain = conn.lookupByName(name)
        if domain.isActive():
            _power(domain, node, "stop", username)
            log_action(
                username, "force_stop_vm", name, "success", f"still running {force_after} s after the shutdown request"
            )
    except (libvirt.libvirtError, DaemonError) as e:
        log_action(username, "force_stop_vm", name, "failed", _power_failure(e)[1])
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_vm(
    name: str,
    force: bool = False,
    force_after: int | None = None,
    node: str | None = None,
    user: dict = Depends(require_vm_privilege("vm.power")),
):
    if force_after is not None and not FORCE_AFTER_RANGE[0] <= force_after <= FORCE_AFTER_RANGE[1]:
        raise HTTPException(
            status_code=422, detail=f"force_after: between {FORCE_AFTER_RANGE[0]} and {FORCE_AFTER_RANGE[1]} seconds"
        )
    conn = open_conn(node)
    action_name = "force_stop_vm" if force else "stop_vm"
    task_id = create_task(action_name, name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "stop_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "stop_vm", name, "failed", "VM already stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already stopped")
        _pre_stop(domain, name, node, task_id, user["username"], action_name, force)
        try:
            method = _power(domain, node, "stop" if force else "shutdown", user["username"])
        except (libvirt.libvirtError, DaemonError) as e:
            status, msg = _power_failure(e)
            log_action(user["username"], action_name, name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=status, detail=f"Unable to stop the VM: {msg}") from e
        detail = f"via {method}" if method else None
        if not force:
            if force_after:
                detail = f"{detail}, forced after {force_after} s if still running"
            threading.Thread(target=_watch_stop, args=(name, node, user["username"], force_after), daemon=True).start()
        log_action(user["username"], action_name, name, "success", detail, task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/restart")
def restart_vm(
    name: str, force: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))
):
    conn = open_conn(node)
    task_id = create_task("restart_vm", name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restart_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "restart_vm", name, "failed", "VM stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is stopped, start it first")
        try:
            method = None
            if force:
                busy = vm_locks.running(name, node)
                if busy:  # a hot backup or a live copy holds the VM: destroying it would break that work
                    raise HTTPException(status_code=409, detail=f"VM '{name}' is busy: {busy} is in progress")
                _pre_stop(domain, name, node, task_id, user["username"], "restart_vm", force)
                _power(domain, node, "reset", user["username"])
            else:
                method = _power(domain, node, "reboot", user["username"])
        except (libvirt.libvirtError, DaemonError) as e:
            status, msg = _power_failure(e)
            log_action(user["username"], "restart_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=status, detail=f"Unable to restart the VM: {msg}") from e
        log_action(
            user["username"], "restart_vm", name, "success", f"via {method}" if method else None, task_id=task_id
        )
        return _domain_summary(domain)
    finally:
        conn.close()


# Unlinking a disk of hundreds of gigabytes, on an NFS pool above all, takes longer than an ordinary operation.
DELETE_TIMEOUT_S = 600


def _perform_vm_deletion(conn, domain, name, node=None, username="system"):
    """The deletion of a stopped VM (app/core/vm_delete.py: its disks but those it does not own, its generated drives,
    its DHCP reservations, then its records), shared by DELETE /vms/{name} and the automatic clean-up of inactive VMs
    (app/core/vm_cleanup.py), so the two never diverge. Returns {path: reason} of the disks kept.

    hyperlite-daemon deletes a VM of this node. Another node's (`conn` over qemu+ssh) is deleted over its libvirt and
    the cluster's SSH key until the cluster domain: its disk paths are paths on that node, which a local unlink() would
    miss, or worse. Raises DaemonError or libvirt.libvirtError, which the caller turns into an answer."""
    if not node or node == "local":
        keep = privileged.run("vm.delete", user=username, timeout=DELETE_TIMEOUT_S, vm=name)["kept"]
    else:
        keep = _delete_on_node(conn, domain, name, node)
    vm_records.forget(name, node)
    return keep


def _delete_on_node(conn, domain, name, node):
    from app.core.cluster import get_node, node_ssh_options

    found = vm_delete.leftovers(domain, name)
    # The qcow2 headers of another node's disks are not read from here: its linked disks are not looked for.
    keep = vm_delete.kept(conn, name, found, local=False)
    vm_delete.undefine(domain, found)
    vm_records.release_addresses(conn, found.reservations)
    remote = get_node(node)
    if remote:
        target = f"{remote['ssh_user']}@{remote['hostname']}"
        for path in found.files:
            if str(path) in keep:
                continue
            result = subprocess.run(
                ["ssh", *node_ssh_options(), "-p", str(remote["ssh_port"]), target, "rm", "-f", str(path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                logger.warning("Disk %s of the deleted VM %s left on %s: %s", path, name, node, result.stderr.strip())
    found.devices = [dev for dev in found.devices if dev not in keep]
    vm_delete.delete_logical_volumes(conn, found, name, node)
    return keep


def _kept_sentence(keep):
    return "; ".join(f"disk {path} kept ({reason})" for path, reason in keep.items())


@router.delete("/{name}")
def delete_vm(
    name: str, confirm: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.allocate"))
):
    """An administrator, or vm.allocate on the VM or on a pool holding it (the account that created it there)."""
    with vm_locks.claim_or_409(name, "a deletion", node=node):
        return _delete_vm(name, confirm, node, user)


def _delete_vm(name, confirm, node, user):
    conn = open_conn(node)
    task_id = create_task("delete_vm", name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "delete_vm", name, "failed", "VM running, it must be stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is running. Stop it before deleting it")
        if not confirm:
            log_action(user["username"], "delete_vm", name, "failed", "Confirmation manquante", task_id=task_id)
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )

        try:
            keep = _perform_vm_deletion(conn, domain, name, node=node, username=user["username"])
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to delete the VM: {msg}") from e
        except DaemonError as e:
            log_action(user["username"], "delete_vm", name, "failed", e.detail, task_id=task_id)
            raise privileged.http_error(e) from None

        log_action(user["username"], "delete_vm", name, "success", _kept_sentence(keep) or None, task_id=task_id)
        # kept_disks: what stays on the storage, as Proxmox VE's and vSphere's tasks say it.
        return {"message": f"VM '{name}' deleted", "kept_disks": keep}
    finally:
        conn.close()


# --- Automatic deletion of inactive VMs: post-creation management
# (enable/reconfigure/disable). The creation itself goes through
# VMCreate.auto_cleanup_days above. The same privilege level as the per-VM
# firewall (vm.hardware): this is a VM setting, not a cluster-wide action like
# migration.
