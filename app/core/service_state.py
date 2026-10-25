"""What one of this node's services publishes for the others (docs/design/privilege-separation.md, section 14, 4c):
hyperlite-ha-crm's view of load balancing and of HA's dry run, which the API's pages show, and the API's requests to it
(load balancing resumed, a manual move's cool-down). A row per name in a local table, as JSON; never replicated."""


def _store():
    from app.repositories.sqlite.service_state import SqliteServiceStateStore

    return SqliteServiceStateStore()


def publish(name, data):
    _store().publish(name, data)


def read(name):
    """(data, when it was published as an ISO time), or (None, None)."""
    return _store().read(name)
