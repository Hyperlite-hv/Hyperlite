"""A stopped VM moved to another cluster (docs/design/multi-cluster.md, section 9.1), as `qm remote-migrate --delete`:
the source's side, in a task. The VM read through hyperlite-daemon (transfer.describe, transfer.export), the target's
node asked over pinned HTTPS with the remote's manage token to prepare it (POST /sites/incoming), to take each disk
(PUT /sites/incoming/{id}/disks/{dev}, the stream of app/core/site_stream.py) and to define it (.../finish); then the
VM here deleted. Anything that fails before the target defined it cancels the incoming there, and the VM here is left
as it was."""

import contextlib
import logging
import os
import time
import urllib.parse

from app.core import privileged, site_stream, sites
from app.core.tasks import task_log, update_task_progress
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

DISK_TIMEOUT_S = 600  # a write of the target, a chunk at a time
FINISH_TIMEOUT_S = 300  # the target waits for its last disk to be flushed, then defines the VM


class MoveError(Exception):
    pass


def describe(vm, username, live=False):
    """What the dialog shows before a move: the VM's specification, what stays behind, its disks' allocated sizes."""
    return privileged.run("transfer.describe", user=username, vm=vm, live=live)


def _endpoint(remote, node):
    """The remote's endpoint of its node `node`: the disks go straight to the node that keeps the incoming VM."""
    for endpoint in sites._repo().endpoints(remote):
        if endpoint.get("node") == node:
            return endpoint
    raise MoveError(f"{remote} has no address for its node {node}: add the remote again to read its members")


def _throttled(chunks, limit_mib):
    """The chunks, at most `limit_mib` MiB/s on average (None: as fast as they come)."""
    if not limit_mib:
        yield from chunks
        return
    start, sent = time.monotonic(), 0
    for chunk in chunks:
        yield chunk
        sent += len(chunk)
        ahead = sent / (limit_mib * 1024 * 1024) - (time.monotonic() - start)
        if ahead > 0:
            time.sleep(ahead)


def _counted(chunks, task_id, done, total, every_s=2.0):
    """The chunks, the task's progress written as they go (what has been read of the disks, out of their size)."""
    last = 0.0
    for chunk in chunks:
        yield chunk
        done[0] += len(chunk)
        now = time.monotonic()
        if now - last >= every_s:
            last = now
            update_task_progress(task_id, min(99, int(done[0] * 100 / max(1, total))))


def move(task_id, username, vm, remote, node, pools, networks, start=False, limit_mib=None, name=None):
    """The whole move, in the task `task_id`: {"name", "remote", "node", "started", "kept"}. Raises MoveError or
    SiteError with what failed; the VM here is then untouched."""
    described = describe(vm, username)
    spec = dict(described["spec"])
    if name:
        spec["name"] = name
    devs = [d["dev"] for d in spec["disks"]]
    if len(pools) != len(devs):
        raise MoveError(f"Choose a storage pool for each of the {len(devs)} disk(s)")
    if len(networks) != len(spec["nics"]):
        raise MoveError(f"Choose a network for each of the {len(spec['nics'])} card(s)")
    for line in described["left"]:
        task_log(task_id, f"Not carried: {line}")
    client = sites._client(_endpoint(remote, node), sites._token(remote, "manage"))
    try:
        prepared = client.json(
            "/sites/incoming",
            "POST",
            {"spec": spec, "pools": pools, "allocated": [described["allocated"][d] for d in devs]},
            timeout=120,
        )
        incoming = prepared["incoming"]
        task_log(task_id, f"{remote}/{node} prepared the VM ({incoming[:8]})")
        try:
            total = sum(d["size"] for d in spec["disks"])
            done = [0]
            for disk in spec["disks"]:
                task_log(task_id, f"Sending {disk['dev']} ({disk['size'] // 1024**2} MiB)")
                _send_disk(client, incoming, vm, disk["dev"], username, task_id, done, total, limit_mib)
            moved = client.json(
                f"/sites/incoming/{incoming}/finish", "POST", {"networks": networks, "start": start},
                timeout=FINISH_TIMEOUT_S,
            )  # fmt: skip
        except BaseException:
            _cancel(client, incoming, task_id)
            raise
    finally:
        client.close()
    task_log(
        task_id, f"Defined on {remote}/{node} as {moved['name']}" + (" and started" if moved.get("started") else "")
    )
    kept = _delete_here(vm, username, remote)
    task_log(task_id, f"{vm} deleted here" + (f", disks kept: {', '.join(kept)}" if kept else ""))
    return {"name": moved["name"], "remote": remote, "node": node, "started": bool(moved.get("started")), "kept": kept}


def _delete_here(vm, username, remote):
    """The VM here deleted once the target has it, as DELETE /vms/{name} deletes it (its disks, drives, reservations,
    then its records). A failure leaves it defined in both places, stopped here: said, never started by Hyperlite."""
    from app.core import vm_records
    from app.daemon.registry import DaemonError

    try:
        kept = privileged.run("vm.delete", user=username, timeout=600, vm=vm)["kept"]
    except DaemonError as e:
        raise MoveError(
            f"{vm} is now on {remote}, but it could not be deleted here ({e.detail}): it stays stopped here, "
            "delete it once you checked the copy there"
        ) from None
    vm_records.forget(vm, None)
    return kept


def _send_disk(client, incoming, vm, dev, username, task_id, done, total, limit_mib):
    _, fds = privileged.run_fds("transfer.export", user=username, vm=vm, dev=dev)
    path = f"/sites/incoming/{incoming}/disks/{urllib.parse.quote(dev)}"
    with os.fdopen(fds[0], "rb") as source:
        for fd in fds[1:]:
            os.close(fd)
        chunks = site_stream.encode(site_stream.pieces_from_export(source))
        answer = client.json(
            path, "PUT", stream=_throttled(_counted(chunks, task_id, done, total), limit_mib), timeout=DISK_TIMEOUT_S
        )
    task_log(task_id, f"{dev} received and checked ({answer.get('digest', '')[:12]})")


def _cancel(client, incoming, task_id):
    """The incoming VM removed at the target, its volumes with it; a failure is said, the target removes it after an
    hour anyway."""
    try:
        client.close()
        client.json(f"/sites/incoming/{incoming}", "DELETE", timeout=60)
        task_log(task_id, "The target removed what it had received")
    except sites.SiteError as e:
        task_log(task_id, f"The target could not remove what it received ({e.message}): it does after an hour")


# ---- A running VM moved live (docs/design/multi-cluster.md, section 9.2), as `qm remote-migrate --online` ----

LIVE_TIMEOUT_S = 24 * 3600  # libvirt's job: the task says how far it got; a cancellation stops it


def move_live(task_id, username, vm, remote, node, pools, networks, limit_mib=None):
    """The running VM moved to the remote's node, in the task `task_id`: the target's CPU checked, the VM prepared
    there, then libvirt's live migration through the pinned tunnel, and the VM defined there from what it runs.
    {"name", "remote", "node", "started": True}. Raises MoveError or SiteError; the VM keeps running here."""
    described = privileged.run("transfer.describe", user=username, vm=vm, live=True)
    spec = described["spec"]
    devs = [d["dev"] for d in spec["disks"]]
    if len(pools) != len(devs):
        raise MoveError(f"Choose a storage pool for each of the {len(devs)} disk(s)")
    if len(networks) != len(spec["nics"]):
        raise MoveError(f"Choose a network for each of the {len(spec['nics'])} card(s)")
    for line in described["left"]:
        task_log(task_id, f"Not carried: {line}")
    endpoint, token = _endpoint(remote, node), sites._token(remote, "manage")
    client = sites._client(endpoint, token)
    tunnel, migrated = None, False
    try:
        if described.get("cpu"):
            cpu = client.json("/sites/incoming/cpu-check", "POST", {"cpu_xml": described["cpu"]}, timeout=60)
            if not cpu.get("compatible"):
                raise MoveError(
                    f"{remote}/{node} cannot run this VM's CPU ({cpu.get('detail')}): shut it down and move it offline"
                )
        prepared = client.json(
            "/sites/incoming", "POST",
            {"spec": spec, "pools": pools, "allocated": [described["allocated"][d] for d in devs]}, timeout=120,
        )  # fmt: skip
        incoming = prepared["incoming"]
        try:
            ready = client.json(f"/sites/incoming/{incoming}/live", "POST", {"networks": networks}, timeout=60)
            task_log(task_id, f"{remote}/{node} is ready to receive it running ({incoming[:8]})")
            _, fds = privileged.run_fds("transfer.live_listen", user=username, incoming=incoming)
            tunnel = _open_tunnel(endpoint, token, incoming, fds)
            privileged.run(
                "transfer.live_run", user=username, timeout=LIVE_TIMEOUT_S, incoming=incoming, vm=vm,
                volumes=ready["volumes"], networks=networks, limit_mib=limit_mib, task_id=task_id,
            )  # fmt: skip
            migrated = True  # the VM runs there now: whatever fails below never removes what it runs on
            tunnel.stop()
            moved = client.json(f"/sites/incoming/{incoming}/live/define", "POST", {}, timeout=120)
        except BaseException as e:
            if migrated:
                from app.core import vm_records

                vm_records.forget(vm, None)  # libvirt undefined it here once the target confirmed
                detail = getattr(e, "message", None) or getattr(e, "detail", None) or str(e)
                raise MoveError(
                    f"{vm} runs on {remote}/{node} now, but it could not be defined there ({detail}): it keeps "
                    "running; define it there before it is stopped"
                ) from None
            if tunnel is not None:
                tunnel.stop()
                if tunnel.error:
                    task_log(task_id, f"The tunnel stopped: {tunnel.error}")
            with contextlib.suppress(Exception):
                privileged.run("transfer.live_close", user=username, incoming=incoming)
            _cancel(client, incoming, task_id)
            if isinstance(e, DaemonError):
                raise MoveError(f"{e.detail}") from None
            raise
    finally:
        client.close()
    # libvirt undefined it here once the target confirmed (UNDEFINE_SOURCE): its records go too.
    from app.core import vm_records

    vm_records.forget(vm, None)
    task_log(task_id, f"Runs on {remote}/{node} as {moved['name']}, defined there; gone from here")
    return {"name": moved["name"], "remote": remote, "node": node, "started": True, "kept": {}}


def _open_tunnel(endpoint, token, incoming, fds):
    """The pinned WebSocket to the target node, carrying the three listening sockets' connections."""
    from app.core import peer_https, site_tunnel

    conn = peer_https._connect(endpoint["address"], endpoint["port"], endpoint["fingerprint"], 30)
    sock, conn.sock = conn.sock, None  # the connection's pinned TLS socket, for the WebSocket
    path = f"{sites.API}/sites/incoming/{incoming}/tunnel"
    return site_tunnel.SourceTunnel(sock, endpoint["address"], endpoint["port"], path, token, fds).start()
