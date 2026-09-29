"""Signing in with an LDAP directory or Active Directory account.

The sign-in form stays the same. A name that belongs to a local account keeps its local password: a directory never
takes over a local account (the admin fallback account included). Any other name is looked up in the directory:
the service account searches for the user (the name is escaped into the filter), then Hyperlite binds as that user
with the typed password, which is the check itself. An empty password is refused before anything is sent: most
directories accept an empty password as an anonymous bind, which would let anyone in.

On success the account exists locally with auth_source 'ldap' and the directory entry's DN; its role is given by the
directory groups at each sign-in: a member of an administrator group is `admin`, anyone else `observateur` (scoped
rights then come from ACLs, as for any account). When allowed groups are set, a user in none of them is refused.
The local password of such an account is a random secret nobody knows: it can only sign in through the directory.
"""

import re
import secrets
import ssl

from ldap3 import NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from app.core import secrets_crypto
from app.core.database import get_conn

TIMEOUT_S = 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
CONFIG_COLUMNS = (
    "enabled",
    "url",
    "starttls",
    "verify_tls",
    "ca_cert",
    "bind_dn",
    "bind_password",
    "base_dn",
    "user_filter",
    "group_attribute",
    "admin_groups",
    "allowed_groups",
)


class LdapError(RuntimeError):
    """The directory could not be used (configuration, network, TLS); the message is for administrators."""


class LocalAccountConflict(Exception):
    """The name belongs to a local or SSO account: the directory does not take it over."""


def get_config():
    with get_conn() as db:
        row = db.execute("SELECT * FROM ldap_config WHERE id = 1").fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("bind_password"):
        try:
            d["bind_password"] = secrets_crypto.decrypt(d["bind_password"])
        except secrets_crypto.SecretUnreadable:
            d["bind_password"] = ""
    return d


def public_config():
    d = get_config() or {
        "enabled": 0,
        "url": "",
        "starttls": 0,
        "verify_tls": 1,
        "ca_cert": "",
        "bind_dn": "",
        "base_dn": "",
        "user_filter": "(&(objectClass=person)(|(uid={username})(sAMAccountName={username})))",
        "group_attribute": "memberOf",
        "admin_groups": "",
        "allowed_groups": "",
        "bind_password": "",
    }
    d["bind_password_set"] = bool(d.pop("bind_password", ""))
    d.pop("id", None)
    for key in ("enabled", "starttls", "verify_tls"):
        d[key] = bool(d[key])
    return d


def validate(fields):
    url = (fields.get("url") or "").strip()
    if not re.match(r"^ldaps?://[A-Za-z0-9.\[\]:-]+(:\d{1,5})?/?$", url):
        raise ValueError("The URL is ldap://host[:port] or ldaps://host[:port]")
    if url.startswith("ldaps://") and fields.get("starttls"):
        raise ValueError("StartTLS is for ldap:// URLs: an ldaps:// connection is already encrypted")
    for key in ("bind_dn", "base_dn"):
        value = (fields.get(key) or "").strip()
        if key == "base_dn" and not value:
            raise ValueError("The search base is required (for example dc=example,dc=org)")
        if value:
            try:
                parse_dn(value)
            except LDAPException:
                raise ValueError(f"Invalid DN: {value}") from None
    user_filter = (fields.get("user_filter") or "").strip()
    if "{username}" not in user_filter or not (user_filter.startswith("(") and user_filter.endswith(")")):
        raise ValueError("The user filter is an LDAP filter in parentheses that contains {username}")
    if not re.match(r"^[A-Za-z][A-Za-z0-9-]{0,63}$", fields.get("group_attribute") or ""):
        raise ValueError("Invalid group attribute")
    ca = (fields.get("ca_cert") or "").strip()
    if ca and "BEGIN CERTIFICATE" not in ca:
        raise ValueError("The CA certificate must be in PEM form")
    return {
        **{k: fields.get(k) for k in CONFIG_COLUMNS if k in fields},
        "url": url,
        "user_filter": user_filter,
        "ca_cert": ca,
    }


def set_config(fields):
    clean = validate(fields)
    password = clean.pop("bind_password", None)
    clean = {k: (1 if v else 0) if k in ("enabled", "starttls", "verify_tls") else (v or "") for k, v in clean.items()}
    if password:
        clean["bind_password"] = secrets_crypto.encrypt(password)
    cols = [c for c in CONFIG_COLUMNS if c in clean]
    with get_conn() as db:
        if db.execute("SELECT id FROM ldap_config WHERE id = 1").fetchone():
            sets = ", ".join(f"{c} = ?" for c in cols)
            # Column names come from CONFIG_COLUMNS only; the values are bound.
            db.execute(f"UPDATE ldap_config SET {sets} WHERE id = 1", [clean[c] for c in cols])  # noqa: S608
        else:
            db.execute(
                f"INSERT INTO ldap_config (id, {', '.join(cols)}) VALUES (1, {', '.join('?' * len(cols))})",  # noqa: S608
                [clean[c] for c in cols],
            )
        db.commit()
    return public_config()


# ---- Directory access ----


def _server(cfg):
    tls = None
    if cfg["url"].startswith("ldaps://") or cfg["starttls"]:
        tls = Tls(
            validate=ssl.CERT_REQUIRED if cfg["verify_tls"] else ssl.CERT_NONE,
            ca_certs_data=cfg["ca_cert"] or None,
            version=ssl.PROTOCOL_TLS_CLIENT,
        )
    return Server(
        cfg["url"], use_ssl=cfg["url"].startswith("ldaps://"), tls=tls, get_info=NONE, connect_timeout=TIMEOUT_S
    )


def _connect(cfg, server, user, password):
    conn = Connection(server, user=user, password=password, receive_timeout=TIMEOUT_S, raise_exceptions=False)
    conn.open()
    if cfg["starttls"] and not conn.start_tls():
        raise LdapError(f"StartTLS failed: {conn.result.get('description')}")
    return conn


def _names(values):
    """Group values as DNs and their first RDN value (the CN), lowercased, to compare with what was configured."""
    out = set()
    for v in values or []:
        v = str(v).strip().lower()
        out.add(v)
        try:
            out.add(parse_dn(v)[0][1].lower())
        except (LDAPException, IndexError):
            continue
    return out


def _configured(text):
    return {x.strip().lower() for x in re.split(r"[\n;]", text or "") if x.strip()}


def _find_user(cfg, server, username):
    service = _connect(cfg, server, cfg["bind_dn"] or None, cfg["bind_password"] or None)
    try:
        if cfg["bind_dn"] and not service.bind():
            raise LdapError(f"The service account could not bind: {service.result.get('description')}")
        search_filter = cfg["user_filter"].replace("{username}", escape_filter_chars(username))
        service.search(cfg["base_dn"], search_filter, SUBTREE, attributes=[cfg["group_attribute"]], size_limit=2)
        entries = [e for e in service.response or [] if e.get("type") == "searchResEntry"]
        if len(entries) != 1:
            return None
        return entries[0]["dn"], entries[0].get("attributes", {}).get(cfg["group_attribute"], [])
    finally:
        service.unbind()


def check(cfg, username, password):
    """(dn, groups) if the directory accepts this user and password, None if not; LdapError when it cannot be
    asked (configuration, network)."""
    if not password or not USERNAME_RE.match(username or ""):
        return None
    server = _server(cfg)
    try:
        found = _find_user(cfg, server, username)
        if found is None:
            return None
        dn, groups = found
        user_conn = _connect(cfg, server, dn, password)
        try:
            if not user_conn.bind():
                return None
        finally:
            user_conn.unbind()
    except LDAPException as e:
        raise LdapError(f"The directory could not be reached: {e}") from e
    return dn, list(groups) if isinstance(groups, list | tuple) else [groups]


def role_for(cfg, groups):
    """'admin', 'observateur', or None when allowed groups are set and the user is in none of them."""
    have = _names(groups)
    allowed = _configured(cfg["allowed_groups"])
    admins = _configured(cfg["admin_groups"])
    if admins & have:
        return "admin"
    if allowed and not allowed & have:
        return None
    return "observateur"


def provision(username, dn, role):
    from app.core.security import hash_password  # security imports this module for the sign-in

    with get_conn() as db:
        row = db.execute("SELECT username, auth_source FROM users WHERE username = ?", (username,)).fetchone()
        if row is not None and row["auth_source"] != "ldap":
            raise LocalAccountConflict(username)
        if row is None:
            db.execute(
                "INSERT INTO users (username, hashed_password, role, auth_source, ldap_dn) VALUES (?, ?, ?, 'ldap', ?)",
                (username, hash_password(secrets.token_hex(32)), role, dn),
            )
        else:
            db.execute("UPDATE users SET role = ?, ldap_dn = ? WHERE username = ?", (role, dn, username))
        db.commit()
        return dict(db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


def authenticate(username, password):
    """The signed-in user (a dict of the users table) or None. Only for names that are not local or SSO accounts."""
    cfg = get_config()
    if not cfg or not cfg["enabled"]:
        return None
    result = check(cfg, username, password)
    if result is None:
        return None
    dn, groups = result
    role = role_for(cfg, groups)
    if role is None:
        return None
    return provision(username, dn, role)


def test(fields_override=None, username=None, password=None):
    """For the settings page: can the service account bind and search, and (optionally) what a given user would get."""
    cfg = get_config() or {}
    if fields_override:
        cfg = {**cfg, **validate({**cfg, **fields_override})}
        if not fields_override.get("bind_password"):
            cfg["bind_password"] = (get_config() or {}).get("bind_password", "")
    if not cfg.get("url"):
        raise LdapError("Save the directory's address first")
    server = _server(cfg)
    try:
        service = _connect(cfg, server, cfg["bind_dn"] or None, cfg["bind_password"] or None)
        try:
            if cfg["bind_dn"] and not service.bind():
                raise LdapError(f"The service account could not bind: {service.result.get('description')}")
            service.search(cfg["base_dn"], "(objectClass=*)", SUBTREE, attributes=[], size_limit=1)
            if service.result.get("result") not in (0, 4):  # 4: size limit exceeded, the base exists
                raise LdapError(f"The search base could not be read: {service.result.get('description')}")
        finally:
            service.unbind()
    except LDAPException as e:
        raise LdapError(f"The directory could not be reached: {e}") from e
    out = {"connexion": True}
    if username:
        result = check(cfg, username, password)
        if result is None:
            out["utilisateur"] = {"accepte": False}
        else:
            dn, groups = result
            out["utilisateur"] = {
                "accepte": True,
                "dn": dn,
                "groupes": [str(g) for g in groups],
                "role": role_for(cfg, groups),
            }
    return out
