"""The API as `www-data` on the lab's real nodes (#511, step 4e: docs/design/privilege-separation.md), when
HYPERLITE_LAB_API_USER names that account: a drop-in runs hyperlite.service as it, right after the package is installed
(before the first call), or once the cluster is formed as root (HYPERLITE_LAB_API_AFTER=cluster: forming it is root's
work the API still does itself). HYPERLITE_LAB_API_DROPIN_FILE and HYPERLITE_LAB_API_PATCH_FILE carry the probe's
lab-only workarounds, each standing for a fix the report proposes. Before the lab is taken down, every refusal in each
node's journal is written to DIR/nN/api-user.txt, with the paths it names. Without the variable, nothing changes."""

import os
from pathlib import Path

import pytest

USER = os.environ.get("HYPERLITE_LAB_API_USER")
AFTER = os.environ.get("HYPERLITE_LAB_API_AFTER", "lab")  # the fixture after which the API is switched
DROPIN_FILE = os.environ.get("HYPERLITE_LAB_API_DROPIN_FILE")  # a whole drop-in, instead of User= and Group= alone
PATCH_FILE = os.environ.get("HYPERLITE_LAB_API_PATCH_FILE")  # a script run once as root on each node, before
REFUSALS = (
    r"Permission denied|PermissionError|Errno 13|EACCES|EPERM|Operation not permitted|not permitted|readonly database"
    r"|unable to open database|UNHANDLED ERROR|Polkit|read only access|read-only|Read-only"
)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    result = yield
    if fixturedef.argname == AFTER and USER:
        module = request.module
        request.addfinalizer(lambda: _collect(module))  # first: a node that fails below is reported too
        conf = Path(DROPIN_FILE).read_text() if DROPIN_FILE else f"[Service]\nUser={USER}\nGroup={USER}\n"
        patch = Path(PATCH_FILE).read_text() if PATCH_FILE else ""
        for n in module.NODES:
            if patch:
                print(f"[lab] node {n}: " + module.on(n, "bash", "-c", patch).strip())
            module.on(
                n,
                "sh",
                "-c",
                "mkdir -p /etc/systemd/system/hyperlite.service.d && cat > /etc/systemd/system/hyperlite.service.d/"
                f"lab-api-user.conf <<'EOF'\n{conf}EOF\nsystemctl daemon-reload && systemctl restart hyperlite || true",
            )
            module.wait(f"node {n}'s API as {USER}", lambda n=n: _answers(module, n), timeout=120, every=2)
            print(
                f"[lab] node {n}: hyperlite.service as {USER}: "
                + module.on(n, "sh", "-c", "systemctl is-active hyperlite || true").strip()
            )
    return result


def _answers(module, n):
    """Whether node n's API answers /health (False while it restarts: a refused connection is not an answer)."""
    try:
        return module.api(n, "GET", "/health")[0] == 200
    except OSError:
        return False


def _collect(module):
    for n in module.NODES:
        try:
            text = module.on(
                n,
                "sh",
                "-c",
                "systemctl status hyperlite --no-pager 2>&1 | head -12; echo ---; "
                f"journalctl -u 'hyperlite*' --no-pager -o short-iso | grep -E '{REFUSALS}' | tail -400",
            )
        except Exception as e:  # a node that does not answer: what the others say still counts
            text = f"not read: {e}"
        out = module.DIR / f"n{n}" / "api-user.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"[lab] node {n}: {text.count(chr(10))} lines of refusals in {out}")
