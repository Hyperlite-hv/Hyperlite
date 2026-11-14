"""Guest updates (docs/design/governance.md, section 11): the guests' own system updates, through their agent.

A scan asks each running guest what its package manager has pending (apt, dnf, zypper on Linux; Windows Update on
Windows) and keeps the count, the security ones and the names. A run installs them on a selection within a window: at
its start Hyperlite launches the installation in the background in each guest (it outlives the agent's two-minute
limit), follows it every minute, reboots the guest at the end when asked and when the guest says it needs one, and
keeps each guest's outcome. Every step goes through the guest agent as root (SYSTEM): vm.exec.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from app.core import privileged
from app.core.database import get_conn

LOG = "/var/log/hyperlite-guest-update"
WIN_LOG = r"C:\ProgramData\Hyperlite\guest-update"

SCAN_SH = r"""
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq >/dev/null 2>&1
  apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | awk '/^Inst /{print "pkg " $2 ((tolower($0) ~ /securi/) ? " security" : "")}'
  echo "family apt"
elif command -v dnf >/dev/null 2>&1; then
  dnf -q check-update 2>/dev/null | awk 'NF==3 && $1 ~ /\./ {print "pkg " $1}'
  dnf -q updateinfo list --security 2>/dev/null | awk 'NF>=3 {print "sec " $3}'
  echo "family dnf"
elif command -v zypper >/dev/null 2>&1; then
  zypper -q --non-interactive list-updates 2>/dev/null | awk -F'|' 'NR>2 && NF>4 {gsub(/ /,"",$3); print "pkg " $3}'
  echo "family zypper"
else
  echo "family unknown"
fi
"""

SCAN_PS = r"""
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
foreach ($u in $r.Updates) {
  $sec = ($u.Categories | Where-Object { $_.Name -match 'Security' }) -ne $null
  if ($sec) { "pkg " + $u.Title.Replace(' ', '_') + " security" } else { "pkg " + $u.Title.Replace(' ', '_') }
}
"family windows"
"""

START_SH = (
    "mkdir -p /var/log && rm -f {log}.rc && (export DEBIAN_FRONTEND=noninteractive; "
    "if command -v apt-get >/dev/null; then apt-get update -qq && apt-get -y -o Dpkg::Options::=--force-confold upgrade; "
    "elif command -v dnf >/dev/null; then dnf -y upgrade; "
    "elif command -v zypper >/dev/null; then zypper --non-interactive update; else false; fi) "
    "> {log}.out 2>&1; echo $? > {log}.rc; "
    "if [ -f /var/run/reboot-required ] || (command -v needs-restarting >/dev/null && ! needs-restarting -r >/dev/null); "
    "then echo yes > {log}.reboot; else echo no > {log}.reboot; fi"
)
START_PS = (
    "New-Item -ItemType Directory -Force C:\\ProgramData\\Hyperlite | Out-Null; Remove-Item -ErrorAction Ignore "
    "'{log}.rc'; Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-Command',"
    '\'$s=New-Object -ComObject Microsoft.Update.Session; $r=$s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0"); '
    "$c=New-Object -ComObject Microsoft.Update.UpdateColl; foreach($u in $r.Updates){{$u.AcceptEula(); [void]$c.Add($u)}}; "
    "if($c.Count -gt 0){{$d=$s.CreateUpdateDownloader(); $d.Updates=$c; [void]$d.Download(); $i=$s.CreateUpdateInstaller(); "
    "$i.Updates=$c; $res=$i.Install(); if($res.RebootRequired){{Set-Content ''{log}.reboot'' yes}} else {{Set-Content ''{log}.reboot'' no}}; "
    "Set-Content ''{log}.rc'' $res.ResultCode}} else {{Set-Content ''{log}.reboot'' no; Set-Content ''{log}.rc'' 2}}'"
)
STATUS_SH = (
    "cat {log}.rc 2>/dev/null; echo '|'; cat {log}.reboot 2>/dev/null; echo '|'; tail -c 2000 {log}.out 2>/dev/null"
)
STATUS_PS = "Get-Content -ErrorAction Ignore '{log}.rc'; '|'; Get-Content -ErrorAction Ignore '{log}.reboot'"


def _now():
    return datetime.now(UTC)


def _exec(vm, shell, script, user="system", timeout_s=110):
    return privileged.run(
        "vm.agent_exec", user=user, vm=vm, node=None, shell=shell, script=script, timeout_s=timeout_s,
        timeout=timeout_s + 15,
    )  # fmt: skip


def _shell(vm):
    """powershell for a Windows guest, sh otherwise (the agent's own os info)."""
    try:
        info = privileged.run("vm.agent_info", user="system", vm=vm, node=None)
        os_id = json.dumps(info.get("os") or info).lower()
    except Exception:  # no agent answer: the scan says so with sh's own failure
        return "sh"
    return "powershell" if "windows" in os_id or "mswindows" in os_id else "sh"


def parse_scan(text):
    pkgs, security, family = [], set(), None
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if parts[0] == "family" and len(parts) > 1:
            family = parts[1]
        elif parts[0] == "pkg" and len(parts) > 1:
            pkgs.append(parts[1])
            if len(parts) > 2 and parts[2] == "security":
                security.add(parts[1])
        elif parts[0] == "sec" and len(parts) > 1:
            security.add(parts[1])
    pending = sorted(set(pkgs))
    return {"family": family, "pending": len(pending), "security": len(security & set(pending)) or len(security),
            "packages": pending[:500]}  # fmt: skip


def scan(vm, user="system"):
    """Ask the guest what it has pending; the row kept."""
    from app.daemon.registry import DaemonError

    shell = _shell(vm)
    found = {"family": None, "pending": None, "security": None, "packages": [], "error": None}
    try:
        result = _exec(vm, shell, SCAN_PS if shell == "powershell" else SCAN_SH, user)
        found.update(parse_scan(result.get("stdout") or ""))
        if result.get("timed_out"):
            found["error"] = "The scan did not finish within two minutes"
    except DaemonError as e:
        found["error"] = e.detail
    with get_conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO guest_updates (vm_name, checked_at, family, pending, security, packages, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                vm,
                _now().isoformat(),
                found["family"],
                found["pending"],
                found["security"],
                json.dumps(found["packages"]),
                found["error"],
            ),
        )
        db.commit()
    return {"vm_name": vm, **found}


def states():
    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM guest_updates ORDER BY vm_name")]
    for r in rows:
        r["packages"] = json.loads(r["packages"] or "[]")
    return rows


def runs():
    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM guest_update_runs ORDER BY window_start DESC LIMIT 100")]
    for r in rows:
        r["vms"] = json.loads(r["vms"])
        r["results"] = json.loads(r["results"] or "{}")
        r["reboot"] = bool(r["reboot"])
    return rows


def schedule(vms, window_start, window_end, reboot, username):
    run_id = uuid.uuid4().hex
    with get_conn() as db:
        db.execute(
            "INSERT INTO guest_update_runs (id, vms, window_start, window_end, reboot, status, results, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, 'scheduled', '{}', ?, ?)",
            (
                run_id,
                json.dumps(sorted(set(vms))),
                window_start,
                window_end,
                1 if reboot else 0,
                username,
                _now().isoformat(),
            ),
        )
        db.commit()
    return next(r for r in runs() if r["id"] == run_id)


def cancel(run_id):
    with get_conn() as db:
        cur = db.execute(
            "UPDATE guest_update_runs SET status = 'cancelled' WHERE id = ? AND status = 'scheduled'", (run_id,)
        )
        db.commit()
    return cur.rowcount > 0


def _save(run):
    with get_conn() as db:
        db.execute(
            "UPDATE guest_update_runs SET status = ?, results = ? WHERE id = ?",
            (run["status"], json.dumps(run["results"]), run["id"]),
        )
        db.commit()


def _start(vm):
    shell = _shell(vm)
    log = WIN_LOG if shell == "powershell" else LOG
    script = (
        START_PS.format(log=log)
        if shell == "powershell"
        else f"nohup sh -c '{START_SH.format(log=log)}' >/dev/null 2>&1 &"
    )
    _exec(vm, shell, script, timeout_s=30)
    return {"state": "running", "shell": shell, "started_at": _now().isoformat()}


def _follow(vm, res):
    """The guest's outcome once its installation ended: done (and rebooted when asked and needed) or failed."""
    log = WIN_LOG if res["shell"] == "powershell" else LOG
    out = (_exec(vm, res["shell"], (STATUS_PS if res["shell"] == "powershell" else STATUS_SH).format(log=log), timeout_s=30)
           .get("stdout") or "")  # fmt: skip
    rc, _, rest = out.partition("|")
    rc = rc.strip()
    if not rc:
        return res  # still running
    reboot, _, tail = rest.partition("|")
    ok = rc in ("0", "2") if res["shell"] == "powershell" else rc == "0"
    return {**res, "state": "done" if ok else "failed", "exit": rc, "needs_reboot": reboot.strip() == "yes",
            "log": tail.strip()[-1500:], "ended_at": _now().isoformat()}  # fmt: skip


def tick(now=None):
    """Start the runs whose window opened, follow the running ones, close them (hyperlite-scheduler, every minute)."""
    from app.core.audit import log_action
    from app.daemon.registry import DaemonError

    now = now or _now()
    for run in runs():
        if run["status"] not in ("scheduled", "running"):
            continue
        start, end = datetime.fromisoformat(run["window_start"]), datetime.fromisoformat(run["window_end"])
        if run["status"] == "scheduled":
            if now < start:
                continue
            run["status"] = "running"
            for vm in run["vms"]:
                if now >= end:
                    run["results"][vm] = {"state": "skipped", "error": "The window closed before it started"}
                    continue
                try:
                    run["results"][vm] = _start(vm)
                except DaemonError as e:
                    run["results"][vm] = {"state": "failed", "error": e.detail}
            _save(run)
            continue
        for vm, res in run["results"].items():
            if res.get("state") != "running":
                continue
            try:
                res = _follow(vm, res)
            except DaemonError as e:
                res = {**res, "error": e.detail}  # the guest rebooting or busy: followed again next minute
            if res["state"] == "done" and run["reboot"] and res.get("needs_reboot"):
                try:
                    privileged.run("vm.power", user="system", vm=vm, action="reboot")
                    res["rebooted"] = True
                except DaemonError as e:
                    res["error"] = f"not rebooted: {e.detail}"
            run["results"][vm] = res
        if all(r.get("state") != "running" for r in run["results"].values()):
            run["status"] = "done"
            failed = [vm for vm, r in run["results"].items() if r.get("state") != "done"]
            log_action("system", "guest_updates", ", ".join(run["vms"]), "failed" if failed else "success",
                       f"failed on {', '.join(failed)}" if failed else "installed")  # fmt: skip
        _save(run)
        for vm in run["vms"]:
            if run["status"] == "done" and run["results"].get(vm, {}).get("state") == "done":
                try:
                    scan(vm)
                except Exception:  # the count is refreshed at the next scan, said in the log
                    logging.getLogger(__name__).warning("Guest %s not scanned after its updates", vm, exc_info=True)
