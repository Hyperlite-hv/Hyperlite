"""The shape of a list in API v1 (docs/design/api-v1.md, "Shape"): {"items": [...], "total": n}, every time, so a
client reads every list the same way and a list can later be paginated (total then counts beyond the page) without
changing its shape. A list that gathers several nodes adds "unreachable": the members that did not answer."""


def listing(items, total=None, **extra):
    items = list(items)
    return {"items": items, "total": len(items) if total is None else total, **extra}
