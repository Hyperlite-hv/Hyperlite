"""A root shell inside a running container, like `docker exec -it <container> sh`.

The web terminal of a system container goes through SSH as its user; an application (Docker) container has no SSH
server and often no account at all. This shell enters the container's namespaces from the host (nsenter, on the
process libvirt started as the container's PID 1), so it works for both kinds and needs nothing in the container
but a shell: bash when the image has one, else sh. A distroless image has neither, and says so in the terminal.

It is root inside the container with the host's capabilities (libvirt's LXC driver starts containers without a user
namespace), which is why only administrators may open it, like the host shell.
"""

from pathlib import Path

from app.core.container_builder import DEFAULT_PATH

# Tried in order inside the container; `exec` so that the shell is the process the terminal talks to.
_SHELL = "if [ -x /bin/bash ]; then exec /bin/bash -l; fi; exec /bin/sh -l"


def _ns_pid(pid, proc):
    """The pid numbers of `pid`, from the host's namespace down to its own (the NSpid line of its status)."""
    for line in (Path(proc) / str(pid) / "status").read_text().splitlines():
        if line.startswith("NSpid:"):
            return line.split()[1:]
    return []


def init_pid(controller_pid, proc="/proc"):
    """The host pid of the container's PID 1: the child of libvirt's LXC controller (whose pid libvirt reports as
    the domain's ID) that is pid 1 in its own namespace. None when it cannot be found (the container just stopped)."""
    try:
        children = (Path(proc) / str(controller_pid) / "task" / str(controller_pid) / "children").read_text().split()
    except OSError:
        return None
    for child in children:
        try:
            numbers = _ns_pid(child, proc)
        except OSError:
            continue
        if len(numbers) > 1 and numbers[-1] == "1":
            return int(child)
    return None


def command(pid):
    """nsenter into every namespace of the container's PID 1, its root directory and working directory."""
    return [
        "nsenter",
        f"--target={pid}",
        "--mount",
        "--uts",
        "--ipc",
        "--net",
        "--pid",
        "--root",
        "--wd",
        "--",
        "/bin/sh",
        "-c",
        _SHELL,
    ]


def environment(spec=None):
    """The shell's environment: the image's PATH for an application container (postgres keeps its tools in its own
    directory), a terminal type, and root's home."""
    path = ((spec or {}).get("env") or {}).get("PATH") or DEFAULT_PATH
    return {"PATH": path, "TERM": "xterm-256color", "HOME": "/root", "LANG": "C.UTF-8"}
