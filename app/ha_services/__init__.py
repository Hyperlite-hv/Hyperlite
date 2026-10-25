"""HA's two services (docs/design/privilege-separation.md, section 14, 4c), split as Proxmox VE splits pve-ha-lrm and
pve-ha-crm, so a hang or a crash of the cluster's decisions never stops the watchdog's feeding and resets a healthy node.

- hyperlite-ha-lrm, every node: the watchdog (ha_selffence), the monitoring of its own HA VMs (ha_monitor), the node's
  heartbeat (statd: the leader judges a member lost by it, so it stops with the feeding, never apart from it), and the
  load-balancing moves asked of this node (drs.carry_out).
- hyperlite-ha-crm, every node, acting on the leader: the recovery of a lost member's HA VMs (ha_recovery), the dry
  run outside a cluster (ha_watch), and load balancing's observation and evaluation (drs.evaluate). What the API's
  pages read of it is published (app/core/service_state.py).

An installed node runs both: their units and the API's set SEPARATE_ENV, and the API then starts none of these. A
checkout, the end-to-end backend and the test suite run them in the API, as before."""

import os

SEPARATE_ENV = "HYPERLITE_HA_SERVICE"
LRM, CRM = "ha-lrm", "ha-crm"


def separate():
    """Whether hyperlite-ha-lrm and hyperlite-ha-crm run HA rather than the API."""
    return os.environ.get(SEPARATE_ENV) == "1"


def here(role):
    """Whether this process is that service (or the API running everything, without the services)."""
    return not separate() or os.environ.get("HYPERLITE_SERVICE") == role


def start(role):
    """Start a service's loops in this process: LRM, CRM, or both in the API."""
    from app.core import drs, ha_monitor, ha_recovery, ha_selffence, ha_watch

    if role in (LRM, None):
        ha_selffence.start_selffence()
        ha_monitor.start()  # each node, its own HA VMs whose monitoring is on
        drs.start_carry_out()  # the moves asked of this node
    if role in (CRM, None):
        ha_watch.start_ha_watch()
        ha_recovery.start()  # acts on the leader only, with the quorum
        drs.start_evaluate()  # load balancing: acts on the leader only, with the quorum (docs/design/drs.md)
