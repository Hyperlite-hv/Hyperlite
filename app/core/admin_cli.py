"""hyperlite-admin's lockdown, as ESXi's lockdown mode (docs/design/admin-cli.md, 4.3): when an administrator turns it
on, root of a node no longer gets its session (root@<node>) from hyperlite-daemon, and a session it already holds is
refused, on every member (the setting is replicated). A named account's API token still works: every action is then
under a person's name. Off by default."""

KEY = "admin_cli_lockdown"
REASON = (
    "Lockdown is on (Administration > API): root of a node acts only as a named account, with that account's API "
    "token (hyperlite-admin --token-file)"
)


def _store():
    from app.repositories import registry

    return registry.settings().sync


def locked():
    return _store().app_setting(KEY) == "on"


def set_locked(on):
    _store().set_app_setting(KEY, "on" if on else "off")
    return bool(on)
