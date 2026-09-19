from datetime import UTC, datetime

from app.core.database import get_conn


def set_vm_ssh_user(vm_name, username):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_ssh_users (vm_name, username) VALUES (?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET username = excluded.username",
            (vm_name, username),
        )
        conn.commit()


def get_vm_ssh_user(vm_name):
    with get_conn() as conn:
        row = conn.execute("SELECT username FROM vm_ssh_users WHERE vm_name = ?", (vm_name,)).fetchone()
        return row["username"] if row else None


def delete_vm_ssh_user(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_ssh_users WHERE vm_name = ?", (vm_name,))
        conn.commit()


def rename_vm_ssh_user(old_name, new_name):
    """Used by cloning: the cloned disk already contains the cloud-init that ran
    once on the source VM, so the same system user exists there."""
    username = get_vm_ssh_user(old_name)
    if username:
        set_vm_ssh_user(new_name, username)


# ---- OS label declared at creation (see database.py::vm_os_label) ----


def set_vm_os_label(vm_name, os_label):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_os_label (vm_name, os_label) VALUES (?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET os_label = excluded.os_label",
            (vm_name, os_label),
        )
        conn.commit()


def get_vm_os_label(vm_name):
    with get_conn() as conn:
        row = conn.execute("SELECT os_label FROM vm_os_label WHERE vm_name = ?", (vm_name,)).fetchone()
        return row["os_label"] if row else None


def delete_vm_os_label(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_os_label WHERE vm_name = ?", (vm_name,))
        conn.commit()


def rename_vm_os_label(old_name, new_name):
    label = get_vm_os_label(old_name)
    if label:
        set_vm_os_label(new_name, label)


# ---- Progress tracking of an unattended installation (recognized ISO) ----


def mark_provisioning(vm_name, os_family, task_id=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_provisioning (vm_name, os_family, started_at, task_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET os_family = excluded.os_family, started_at = excluded.started_at, task_id = excluded.task_id",
            (vm_name, os_family, datetime.now(UTC).isoformat(), task_id),
        )
        conn.commit()


def get_provisioning(vm_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT os_family, started_at, task_id FROM vm_provisioning WHERE vm_name = ?", (vm_name,)
        ).fetchone()
        return dict(row) if row else None


def clear_provisioning(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_provisioning WHERE vm_name = ?", (vm_name,))
        conn.commit()


# ---- Automatic deletion of inactive VMs ----


def set_vm_auto_cleanup(vm_name, inactive_days):
    """Enable or reconfigure. Also resets the counter (last_active_at = now):
    changing the threshold restarts from zero, which is more intuitive than
    letting an old counter run under a new threshold."""
    now = datetime.now(UTC).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vm_auto_cleanup (vm_name, inactive_days, last_active_at, warned_at, created_at) "
            "VALUES (?, ?, ?, NULL, ?) "
            "ON CONFLICT(vm_name) DO UPDATE SET inactive_days = excluded.inactive_days, last_active_at = excluded.last_active_at, warned_at = NULL",
            (vm_name, inactive_days, now, now),
        )
        conn.commit()


def get_vm_auto_cleanup(vm_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT inactive_days, last_active_at, warned_at, created_at FROM vm_auto_cleanup WHERE vm_name = ?",
            (vm_name,),
        ).fetchone()
        return dict(row) if row else None


def delete_vm_auto_cleanup(vm_name):
    with get_conn() as conn:
        conn.execute("DELETE FROM vm_auto_cleanup WHERE vm_name = ?", (vm_name,))
        conn.commit()


def touch_vm_activity(vm_name):
    """Called when a VM starts (app/routers/vms.py::start_vm). Resets the
    inactivity counter AND the warning already sent (a VM that was just
    restarted is no longer "about to be deleted"). Does nothing when no policy
    is configured for this VM (an unmatched WHERE modifies 0 rows, silently)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE vm_auto_cleanup SET last_active_at = ?, warned_at = NULL WHERE vm_name = ?",
            (datetime.now(UTC).isoformat(), vm_name),
        )
        conn.commit()


def list_all_auto_cleanup():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT vm_name, inactive_days, last_active_at, warned_at, created_at FROM vm_auto_cleanup"
        ).fetchall()
        return [dict(r) for r in rows]
