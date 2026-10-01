from datetime import UTC, datetime


def _store():
    from app.repositories import registry

    return registry.objects().sync


def set_vm_ssh_user(vm_name, username):
    _store().set_vm_ssh_user(vm_name, username)


def get_vm_ssh_user(vm_name):
    return _store().vm_ssh_user(vm_name)


def delete_vm_ssh_user(vm_name):
    _store().delete_vm_ssh_user(vm_name)


def rename_vm_ssh_user(old_name, new_name):
    """Used by cloning: the cloned disk already contains the cloud-init that ran
    once on the source VM, so the same system user exists there."""
    username = get_vm_ssh_user(old_name)
    if username:
        set_vm_ssh_user(new_name, username)


# ---- OS label declared at creation (see database.py::vm_os_label) ----


def set_vm_os_label(vm_name, os_label):
    _store().set_vm_os_label(vm_name, os_label)


def get_vm_os_label(vm_name):
    return _store().vm_os_label(vm_name)


def delete_vm_os_label(vm_name):
    _store().delete_vm_os_label(vm_name)


def rename_vm_os_label(old_name, new_name):
    label = get_vm_os_label(old_name)
    if label:
        set_vm_os_label(new_name, label)


# ---- Progress tracking of an unattended installation (recognized ISO) ----


def mark_provisioning(vm_name, os_family, task_id=None):
    _store().mark_provisioning(vm_name, os_family, datetime.now(UTC).isoformat(), task_id)


def get_provisioning(vm_name):
    return _store().provisioning(vm_name)


def clear_provisioning(vm_name):
    _store().clear_provisioning(vm_name)


# ---- Automatic deletion of inactive VMs ----


def set_vm_auto_cleanup(vm_name, inactive_days):
    """Enable or reconfigure. Also resets the counter (last_active_at = now):
    changing the threshold restarts from zero, which is more intuitive than
    letting an old counter run under a new threshold."""
    _store().set_auto_cleanup(vm_name, inactive_days, datetime.now(UTC).isoformat())


def get_vm_auto_cleanup(vm_name):
    return _store().auto_cleanup(vm_name)


def delete_vm_auto_cleanup(vm_name):
    _store().delete_auto_cleanup(vm_name)


def touch_vm_activity(vm_name):
    """Called when a VM starts (app/routers/vms.py::start_vm). Resets the
    inactivity counter AND the warning already sent (a VM that was just
    restarted is no longer "about to be deleted"). Does nothing when no policy
    is configured for this VM (an unmatched WHERE modifies 0 rows, silently)."""
    _store().touch_activity(vm_name, datetime.now(UTC).isoformat())


def list_all_auto_cleanup():
    return _store().all_auto_cleanup()
