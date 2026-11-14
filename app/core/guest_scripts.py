"""Script library (docs/design/console-2.md, section 7): ready-made scripts run in guests through their agent.

Built-in scripts ship with Hyperlite (BUILTIN, updated with it); administrators add their own (`guest_scripts`). A run
picks one script and VMs: each guest receives the script as a file and runs it in the background (the guest updates'
way, so a long installation outlives the agent's two-minute limit), then `tick` follows it every minute and keeps its
exit code and the end of its output. Starting is `vm.exec` on each VM as the account that asks (hyperlite-daemon checks
it); parameters are checked against their pattern and handed as environment variables, never pasted into the code.
"""

import base64
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from app.core.database import get_conn

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
VALUE_RE = re.compile(r"^[A-Za-z0-9 ._:/@+=,%-]{0,200}$")
GIVE_UP_H = 6  # a guest silent this long after its start: the run says so and stops following it
DIR_SH = "/var/lib/hyperlite-scripts"
DIR_PS = r"C:\ProgramData\Hyperlite\scripts"

BUILTIN = {
    "docker": {
        "name": "Install Docker",
        "description": "Docker Engine and the compose plugin from Docker's own repository (get.docker.com), started "
        "at boot. Debian, Ubuntu, Fedora, CentOS Stream, RHEL and their kin.",
        "shell": "sh",
        "params": [],
        "body": r"""set -e
if command -v docker >/dev/null 2>&1; then docker --version; echo "Docker is already installed"; exit 0; fi
if command -v curl >/dev/null 2>&1; then curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
elif command -v wget >/dev/null 2>&1; then wget -qO /tmp/get-docker.sh https://get.docker.com
else echo "Neither curl nor wget is installed"; exit 2; fi
sh /tmp/get-docker.sh
rm -f /tmp/get-docker.sh
systemctl enable --now docker
docker --version
""",
    },
    "node_exporter": {
        "name": "Install the Prometheus node exporter",
        "description": "The guest's own metrics on port 9100 for a Prometheus server, from the distribution's packages, "
        "started at boot.",
        "shell": "sh",
        "params": [],
        "body": r"""set -e
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y prometheus-node-exporter
  systemctl enable --now prometheus-node-exporter
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y golang-github-prometheus-node-exporter || dnf install -y node_exporter
  systemctl enable --now prometheus-node-exporter 2>/dev/null || systemctl enable --now node_exporter
elif command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive install golang-github-prometheus-node_exporter
  systemctl enable --now prometheus-node_exporter
else echo "No supported package manager"; exit 2; fi
sleep 2
(command -v curl >/dev/null && curl -fsS http://127.0.0.1:9100/metrics | head -n 3) || true
echo "node exporter listening on port 9100"
""",
    },
    "ssh_harden": {
        "name": "Harden SSH",
        "description": "Keys only: no password nor keyboard-interactive sign-in, root by key only (or never). Refused "
        "when no account has an authorized key, so nobody is locked out; the configuration is checked before sshd "
        "reloads.",
        "shell": "sh",
        "params": [
            {
                "name": "ROOT_LOGIN",
                "label": "Root sign-in (prohibit-password or no)",
                "default": "prohibit-password",
                "pattern": "^(prohibit-password|no)$",
            }
        ],
        "body": r"""set -e
keys=0
for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -s "$f" ] && keys=1; done
if [ "$keys" = 0 ]; then echo "No account has an authorized SSH key: refused, it would lock everyone out"; exit 3; fi
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/90-hyperlite-hardening.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PermitRootLogin ${ROOT_LOGIN:-prohibit-password}
PermitEmptyPasswords no
EOF
if ! grep -qiE '^\s*Include\s+/etc/ssh/sshd_config.d/' /etc/ssh/sshd_config; then
  sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
fi
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd
echo "SSH now accepts keys only (root: ${ROOT_LOGIN:-prohibit-password})"
""",
    },
    "auto_security_updates": {
        "name": "Turn on automatic security updates",
        "description": "unattended-upgrades on Debian and Ubuntu, dnf-automatic (security only) on Fedora and RHEL "
        "kin. Reboots stay yours to plan.",
        "shell": "sh",
        "params": [],
        "body": r"""set -e
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y unattended-upgrades
  printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
  systemctl enable --now unattended-upgrades
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y dnf-automatic
  if [ -f /etc/dnf/automatic.conf ]; then
    sed -i 's/^upgrade_type.*/upgrade_type = security/; s/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf
  fi
  systemctl enable --now dnf-automatic.timer 2>/dev/null || systemctl enable --now dnf5-automatic.timer
else echo "No supported package manager"; exit 2; fi
echo "Automatic security updates are on"
""",
    },
    "timezone": {
        "name": "Set the time zone",
        "description": "The guest's time zone (for example Europe/Paris), with timedatectl.",
        "shell": "sh",
        "params": [
            {
                "name": "TZ_NAME",
                "label": "Time zone",
                "default": "Europe/Paris",
                "pattern": "^[A-Za-z]+(/[A-Za-z0-9_+-]+){0,2}$",
            }
        ],
        "body": r"""set -e
timedatectl set-timezone "$TZ_NAME"
timedatectl | grep -i 'time zone'
""",
    },
    "free_disk": {
        "name": "Free disk space",
        "description": "Package caches emptied, packages no longer needed removed, the journal kept to 7 days; says how "
        "much space was freed.",
        "shell": "sh",
        "params": [],
        "body": r"""before=$(df -Pk / | awk 'NR==2 {print $4}')
if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get -y autoremove --purge; apt-get clean
elif command -v dnf >/dev/null 2>&1; then dnf -y autoremove; dnf clean all
elif command -v zypper >/dev/null 2>&1; then zypper clean --all; fi
command -v journalctl >/dev/null 2>&1 && journalctl --vacuum-time=7d
after=$(df -Pk / | awk 'NR==2 {print $4}')
echo "Freed $(( (after - before) / 1024 )) MiB on /"
""",
    },
    "win_rdp": {
        "name": "Windows: turn on remote desktop",
        "description": "Remote desktop allowed with network level authentication, and its firewall rules opened (any "
        "language of Windows).",
        "shell": "powershell",
        "params": [],
        "body": r"""$ErrorActionPreference = 'Stop'
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -Value 1
Get-NetFirewallRule | Where-Object { $_.Group -eq '@FirewallAPI.dll,-28752' } | Enable-NetFirewallRule
'Remote desktop is on'
""",
    },
    "win_openssh": {
        "name": "Windows: install OpenSSH Server",
        "description": "The OpenSSH Server feature of Windows, started at boot, with its firewall rule on port 22.",
        "shell": "powershell",
        "params": [],
        "body": r"""$ErrorActionPreference = 'Stop'
$cap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
'OpenSSH Server is running on port 22'
""",
    },
}


class ScriptError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return datetime.now(UTC)


def _custom_row(r):
    s = dict(r)
    s["params"] = json.loads(s["params"] or "[]")
    s["builtin"] = False
    return s


def scripts():
    builtin = [{"id": f"builtin:{k}", **v, "builtin": True} for k, v in BUILTIN.items()]
    with get_conn() as db:
        custom = [_custom_row(r) for r in db.execute("SELECT * FROM guest_scripts ORDER BY name")]
    return builtin + custom


def get(script_id):
    if script_id.startswith("builtin:"):
        key = script_id[8:]
        return {"id": script_id, **BUILTIN[key], "builtin": True} if key in BUILTIN else None
    with get_conn() as db:
        row = db.execute("SELECT * FROM guest_scripts WHERE id = ?", (script_id,)).fetchone()
    return _custom_row(row) if row else None


def _check_params(params):
    out = []
    for p in params or []:
        if not NAME_RE.match(str(p.get("name", ""))):
            raise ScriptError(422, "A parameter's name is in capitals: A-Z, 0-9 and _, 32 characters at most")
        pattern = p.get("pattern") or VALUE_RE.pattern
        try:
            re.compile(pattern)
        except re.error:
            raise ScriptError(422, f"The pattern of {p['name']} is not a valid regular expression") from None
        default = str(p.get("default") or "")
        out.append({"name": p["name"], "label": str(p.get("label") or p["name"])[:100], "default": default[:200],
                    "pattern": pattern[:200]})  # fmt: skip
    if len({p["name"] for p in out}) != len(out):
        raise ScriptError(422, "Two parameters have the same name")
    return out


def save(fields, username, script_id=None):
    name = (fields.get("name") or "").strip()
    if not 1 <= len(name) <= 100:
        raise ScriptError(422, "A name of 1 to 100 characters")
    shell = fields.get("shell")
    if shell not in ("sh", "powershell"):
        raise ScriptError(422, "A script is sh or powershell")
    body = fields.get("body") or ""
    if not body.strip() or len(body) > 32000:
        raise ScriptError(422, "A script of 1 to 32 000 characters")
    params = _check_params(fields.get("params"))
    desc = (fields.get("description") or "").strip()[:500]
    with get_conn() as db:
        if script_id is None:
            script_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO guest_scripts (id, name, description, shell, body, params, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (script_id, name, desc, shell, body, json.dumps(params), username, _now().isoformat()),
            )
        else:
            cur = db.execute(
                "UPDATE guest_scripts SET name = ?, description = ?, shell = ?, body = ?, params = ? WHERE id = ?",
                (name, desc, shell, body, json.dumps(params), script_id),
            )
            if cur.rowcount == 0:
                return None
        db.commit()
    return get(script_id)


def delete(script_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM guest_scripts WHERE id = ?", (script_id,))
        db.commit()
    return cur.rowcount > 0


def values_for(script, given):
    """The parameters' values: given or default, each matching its pattern."""
    out = {}
    for p in script["params"]:
        value = str((given or {}).get(p["name"], p["default"]))
        if not re.fullmatch(p["pattern"], value) or not VALUE_RE.match(value):
            raise ScriptError(422, f"{p['label']}: “{value}” is not accepted")
        out[p["name"]] = value
    return out


def program(script, values):
    """The file the guest runs: the parameters as environment variables, then the script."""
    if script["shell"] == "powershell":
        head = "".join(f"$env:{k} = '{v}'\n" for k, v in values.items())
    else:
        head = "".join(f"export {k}='{v}'\n" for k, v in values.items())
    return head + script["body"]


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def start_command(shell, run_key, text):
    if shell == "powershell":
        d = DIR_PS
        runner = (
            f"try {{ & '{d}\\{run_key}.ps1' *> '{d}\\{run_key}.out'; $c = if ($LASTEXITCODE) {{ $LASTEXITCODE }} "
            f"else {{ 0 }} }} catch {{ $_ | Out-File -Append '{d}\\{run_key}.out'; $c = 1 }}\n"
            f"Set-Content '{d}\\{run_key}.rc' $c\n"
        )
        return (
            f"New-Item -ItemType Directory -Force '{d}' | Out-Null; "
            f"[IO.File]::WriteAllBytes('{d}\\{run_key}.ps1', [Convert]::FromBase64String('{_b64(text)}')); "
            f"[IO.File]::WriteAllBytes('{d}\\{run_key}-run.ps1', [Convert]::FromBase64String('{_b64(runner)}')); "
            f"Remove-Item -ErrorAction Ignore '{d}\\{run_key}.rc'; "
            f"Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass',"
            f"'-File','{d}\\{run_key}-run.ps1'"
        )
    d = DIR_SH
    return (
        f"mkdir -p {d} && chmod 700 {d} && echo {_b64(text)} | base64 -d > {d}/{run_key}.sh && rm -f {d}/{run_key}.rc "
        f"&& (nohup sh -c 'sh {d}/{run_key}.sh > {d}/{run_key}.out 2>&1; echo $? > {d}/{run_key}.rc' "
        f">/dev/null 2>&1 &)"
    )


def status_command(shell, run_key):
    if shell == "powershell":
        d = DIR_PS
        return (
            f"Get-Content -ErrorAction Ignore '{d}\\{run_key}.rc'; '|'; if (Test-Path '{d}\\{run_key}.out') "
            f"{{ $t = Get-Content -Raw '{d}\\{run_key}.out'; if ($t) {{ $t.Substring([Math]::Max(0, $t.Length - 3000)) }} }}"
        )
    d = DIR_SH
    return f"cat {d}/{run_key}.rc 2>/dev/null; echo '|'; tail -c 3000 {d}/{run_key}.out 2>/dev/null"


def _row(r):
    run = dict(r)
    run["vms"] = json.loads(run["vms"])
    run["params"] = json.loads(run["params"] or "{}")
    run["results"] = json.loads(run["results"] or "{}")
    return run


def runs(limit=100):
    with get_conn() as db:
        return [
            _row(r) for r in db.execute("SELECT * FROM guest_script_runs ORDER BY created_at DESC LIMIT ?", (limit,))
        ]


def _save_run(run):
    with get_conn() as db:
        db.execute(
            "UPDATE guest_script_runs SET status = ?, results = ? WHERE id = ?",
            (run["status"], json.dumps(run["results"]), run["id"]),
        )
        db.commit()


def run(script_id, vms, given, username):
    """Start `script_id` on `vms` now, as `username` (vm.exec checked by the daemon on each); the run."""
    from app.core import guest_updates
    from app.core.audit import log_action
    from app.daemon.registry import DaemonError

    script = get(script_id)
    if script is None:
        raise ScriptError(404, "No such script")
    values = values_for(script, given)
    text = program(script, values)
    run_id = uuid.uuid4().hex
    key = f"r{run_id[:12]}"
    results = {}
    for vm in sorted(set(vms)):
        try:
            shell = guest_updates._shell(vm)
            if shell != script["shell"]:
                results[vm] = {"state": "skipped", "error": f"This script is for {script['shell']} guests"}
                continue
            guest_updates._exec(vm, shell, start_command(shell, key, text), user=username, timeout_s=30)
            results[vm] = {"state": "running", "shell": shell, "started_at": _now().isoformat()}
        except DaemonError as e:
            results[vm] = {"state": "failed", "error": e.detail}
    status = "running" if any(r["state"] == "running" for r in results.values()) else "done"
    with get_conn() as db:
        db.execute(
            "INSERT INTO guest_script_runs (id, script_id, script_name, vms, params, status, results, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                script_id,
                script["name"],
                json.dumps(sorted(set(vms))),
                json.dumps(values),
                status,
                json.dumps(results),
                username,
                _now().isoformat(),
            ),
        )
        db.commit()
    log_action(username, "guest_script", ", ".join(sorted(set(vms))), "success", f"{script['name']} (run {run_id})")
    return next(r for r in runs() if r["id"] == run_id)


def parse_status(out):
    rc, _, tail = (out or "").partition("|")
    rc = rc.strip()
    return (int(rc) if rc.lstrip("-").isdigit() else None), tail.strip()[-3000:]


def tick(now=None):
    """Follow the running scripts: each guest's exit code and the end of its output (every minute)."""
    from app.core import guest_updates
    from app.core.audit import log_action
    from app.daemon.registry import DaemonError

    now = now or _now()
    for r in runs():
        if r["status"] != "running":
            continue
        key = f"r{r['id'][:12]}"
        for vm, res in r["results"].items():
            if res.get("state") != "running":
                continue
            try:
                out = guest_updates._exec(vm, res["shell"], status_command(res["shell"], key), timeout_s=30)
                rc, tail = parse_status(out.get("stdout") or "")
            except DaemonError as e:
                rc, tail = None, None
                res["error"] = e.detail  # the guest rebooting or busy: followed again next minute
            if rc is not None:
                res.update({"state": "done" if rc == 0 else "failed", "exit": rc, "output": tail,
                            "ended_at": now.isoformat()})  # fmt: skip
                res.pop("error", None)
            elif now - datetime.fromisoformat(res["started_at"]) > timedelta(hours=GIVE_UP_H):
                res.update({"state": "failed", "error": f"no end after {GIVE_UP_H} h", "output": tail})
        if all(x.get("state") != "running" for x in r["results"].values()):
            r["status"] = "done"
            failed = [vm for vm, x in r["results"].items() if x.get("state") != "done"]
            log_action("system", "guest_script", ", ".join(r["vms"]), "failed" if failed else "success",
                       f"{r['script_name']}: " + (f"failed on {', '.join(failed)}" if failed else "done"))  # fmt: skip
        _save_run(r)
