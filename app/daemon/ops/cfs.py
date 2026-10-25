"""The copy between SQLite and hyperlite-cfs (app/repositories/cfs: shadow.py out, inbound.py in), done by
hyperlite-daemon from 4e (docs/design/privilege-separation.md, section 14.4): the copy in writes every replicated table,
the permissions' file among them, which only root writes, and the copy out writes the cluster's tree, which the API must
not be able to write (a row it wrote there would reach every member's permissions). Both loops run in the daemon
(app/daemon/__main__.py), and the API asks for the two things a request needs at once.

Neither operation takes anything from the caller but outbox ids: each copies what this node's database already holds,
so anyone may ask (no proof: a sign-in's own changes are confirmed too)."""

from typing import Annotated

from pydantic import Field

from app.daemon.needs import PUBLIC
from app.daemon.registry import Args, DaemonError, NoArgs, operation

# A request confirms the outbox entries of its own writes: a few, a few hundred for the largest.
MAX_IDS = 10000


@operation("cfs.catch_up", NoArgs, needs=PUBLIC)
def catch_up(args, ctx):
    """Before a request changes something: this node's own changes copied out first, then the cluster's applied, so the
    request starts from them. A daemon that does not answer is the confirmation's to report: {} either way."""
    from app.repositories.cfs import shadow

    shadow.catch_up_here()
    return {}


class Confirm(Args):
    ids: list[Annotated[int, Field(ge=1)]] = Field(min_length=1, max_length=MAX_IDS)


@operation("cfs.confirm", Confirm, needs=PUBLIC)
def confirm(args, ctx):
    """The request's outbox entries copied, once the cluster took them all: {"changed": []}, or the paths whose other
    node's version the cluster kept (shadow.Changed, answered rather than raised: the API's 409). Not taken in time, or
    refused: "unavailable", with the fixed sentence the API answers (shadow.NotConfirmed)."""
    from app.repositories.cfs import shadow

    try:
        shadow.confirm_here(args.ids)
    except shadow.Changed as e:
        return {"changed": e.paths}
    except shadow.NotConfirmed as e:
        raise DaemonError("unavailable", str(e)) from None
    return {"changed": []}


# --- Administration > Replicated configuration (app/routers/cfs.py): administrators only ---


@operation("cfs.report", NoArgs)
def report(args, ctx):
    """Shadow mode's state and what differs between SQLite and the cluster's tree."""
    from app.repositories.cfs import shadow

    return shadow.report()


@operation("cfs.seed", NoArgs)
def seed(args, ctx):
    """This node's configuration copied whole into the tree: {"written", "deleted"}. Shadow mode off: "in_use"; the
    daemon does not answer: "unavailable", with the reason."""
    from app.core.cfs_client import CfsError
    from app.repositories.cfs import shadow

    try:
        return shadow.seed()
    except shadow.ShadowDisabled:
        raise DaemonError("in_use", shadow.DISABLED) from None
    except (CfsError, OSError) as e:
        raise DaemonError("unavailable", shadow.describe(e)) from None


@operation("cfs.enable", NoArgs)
def enable(args, ctx):
    """hyperlite-cfs started, shadow mode on, the first copy made: {"written", "deleted"}."""
    from app.repositories.cfs import shadow

    try:
        return shadow.turn_on()
    except shadow.ShadowError as e:
        raise DaemonError("unavailable", str(e)) from None


@operation("cfs.disable", NoArgs)
def disable(args, ctx):
    """Shadow mode off and hyperlite-cfs stopped. Forced on by the node's env file: "in_use"."""
    from app.repositories.cfs import shadow

    try:
        shadow.turn_off()
    except shadow.ShadowError as e:
        raise DaemonError("in_use" if shadow.forced() else "unavailable", str(e)) from None
    return {"active": False}
