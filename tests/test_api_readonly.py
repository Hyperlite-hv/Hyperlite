"""What the API still asked of libvirt with a connection that may write, outside HA (#333): the dashboard's default
pool, made and read again, and the guests' shutdown before a reboot, now hyperlite-daemon's
(docs/design/privilege-separation.md, step 4: the API keeps a read-only connection)."""

from app.core import privileged
from app.routers import dashboard


class Pool:
    def info(self):
        return (1, 100 * 1024**3, 0, 40 * 1024**3)


class Conn:
    def getHostname(self):
        return "hv1"

    def getType(self):
        return "QEMU"

    def isAlive(self):
        return 1

    def listAllDomains(self):
        return []

    def storagePoolLookupByName(self, name):
        assert name == "default"
        return Pool()

    def close(self):
        pass


def test_the_dashboard_reads_and_the_daemon_makes_and_refreshes_the_default_pool(client, auth_headers, monkeypatch):
    asked = []

    def readonly(node=None, readonly=False):
        assert readonly, "the dashboard only reads"
        return Conn()

    monkeypatch.setattr(dashboard, "open_conn", readonly)
    monkeypatch.setattr(privileged, "run", lambda op, **args: asked.append((op, args.get("pool"))))
    body = client.get("/dashboard", headers=auth_headers("alice")).json()
    assert asked == [("storage.ensure_default_pool", None), ("storage.pool_refresh", "default")]
    assert body["hypervisor"]["name"] == "hv1"
