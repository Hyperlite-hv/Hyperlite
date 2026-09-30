"""Environment variables some well-known images refuse to start without.

An image's configuration lists the variables it sets, never the ones it expects: the official postgres image exits
at once with "superuser password is not specified" when POSTGRES_PASSWORD is missing, and nothing in the image says
so beforehand. This table names them for common images, so the creation form asks for them (and can generate a
password) and the API refuses a container that would stop at once.

Each requirement is a list of variables of which one must be set: postgres also accepts
POSTGRES_HOST_AUTH_METHOD=trust instead of a password. The first variable is the one the form asks for.
"""

import re

# name: (description, secret, generated). A generated variable gets a random value in the form, which the user can
# change; it is a secret shown once, like a password typed at creation.
_VARS = {
    "POSTGRES_PASSWORD": ("Password of the PostgreSQL superuser", True, True),
    "POSTGRES_HOST_AUTH_METHOD": ("'trust' to allow connections without a password (tests only)", False, False),
    "POSTGRES_USER": ("Superuser name (default: postgres)", False, False),
    "POSTGRES_DB": ("Database created at first start (default: the user name)", False, False),
    "MYSQL_ROOT_PASSWORD": ("Password of the MySQL root account", True, True),
    "MYSQL_ALLOW_EMPTY_PASSWORD": ("'yes' for a root account without a password (tests only)", False, False),
    "MYSQL_RANDOM_ROOT_PASSWORD": ("'yes' to generate a root password, printed in the container's log", False, False),
    "MYSQL_DATABASE": ("Database created at first start", False, False),
    "MYSQL_USER": ("Account created at first start, owner of MYSQL_DATABASE", False, False),
    "MYSQL_PASSWORD": ("Password of MYSQL_USER", True, False),
    "MARIADB_ROOT_PASSWORD": ("Password of the MariaDB root account", True, True),
    "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD": ("'yes' for a root account without a password (tests only)", False, False),
    "MARIADB_RANDOM_ROOT_PASSWORD": ("'yes' to generate a root password, printed in the container's log", False, False),
    "MARIADB_DATABASE": ("Database created at first start", False, False),
    "MARIADB_USER": ("Account created at first start, owner of MARIADB_DATABASE", False, False),
    "MARIADB_PASSWORD": ("Password of MARIADB_USER", True, False),
    "MONGO_INITDB_ROOT_USERNAME": ("Administrator created at first start (none: no authentication)", False, False),
    "MONGO_INITDB_ROOT_PASSWORD": ("Password of that administrator", True, False),
    "POSTGRESQL_PASSWORD": ("Password of the PostgreSQL user", True, True),
    "ALLOW_EMPTY_PASSWORD": ("'yes' to allow an empty password (tests only)", False, False),
    "MSSQL_SA_PASSWORD": (
        "Password of the SA account: 8 characters or more, with 3 of upper, lower, digit, symbol",
        True,
        True,
    ),
    "ACCEPT_EULA": ("'Y' to accept the SQL Server licence (required)", False, False),
    "ORACLE_PASSWORD": ("Password of SYS and SYSTEM", True, True),
    "ORACLE_RANDOM_PASSWORD": ("'yes' to generate the password, printed in the container's log", False, False),
    "RABBITMQ_DEFAULT_USER": ("Administrator name (default: guest, usable from the container only)", False, False),
    "RABBITMQ_DEFAULT_PASS": ("Password of that administrator", True, False),
    "MINIO_ROOT_USER": ("Administrator name (default: minioadmin)", False, False),
    "MINIO_ROOT_PASSWORD": ("Administrator password, 8 characters or more (default: minioadmin)", True, False),
    "GF_SECURITY_ADMIN_PASSWORD": ("Password of Grafana's admin account (default: admin)", True, False),
    "WORDPRESS_DB_HOST": ("Address of the MySQL/MariaDB server", False, False),
    "WORDPRESS_DB_USER": ("Database account", False, False),
    "WORDPRESS_DB_PASSWORD": ("Password of that account", True, False),
    "WORDPRESS_DB_NAME": ("Database name", False, False),
    "NEXTCLOUD_ADMIN_USER": ("Administrator created at first start", False, False),
    "NEXTCLOUD_ADMIN_PASSWORD": ("Password of that administrator", True, False),
    "TZ": ("Time zone, for example Europe/Paris", False, False),
}

_POSTGRES = {
    "requises": [["POSTGRES_PASSWORD", "POSTGRES_HOST_AUTH_METHOD"]],
    "utiles": ["POSTGRES_USER", "POSTGRES_DB", "TZ"],
}
_MYSQL = {
    "requises": [["MYSQL_ROOT_PASSWORD", "MYSQL_RANDOM_ROOT_PASSWORD", "MYSQL_ALLOW_EMPTY_PASSWORD"]],
    "utiles": ["MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "TZ"],
}
_MARIADB = {
    # The mariadb image also reads the MYSQL_ names.
    "requises": [
        [
            "MARIADB_ROOT_PASSWORD",
            "MARIADB_RANDOM_ROOT_PASSWORD",
            "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD",
            "MYSQL_ROOT_PASSWORD",
            "MYSQL_RANDOM_ROOT_PASSWORD",
            "MYSQL_ALLOW_EMPTY_PASSWORD",
        ]
    ],
    "utiles": ["MARIADB_DATABASE", "MARIADB_USER", "MARIADB_PASSWORD", "TZ"],
}

# Repository (without registry, "library/" or tag) -> requirements.
KNOWN = {
    "postgres": _POSTGRES,
    "postgis/postgis": _POSTGRES,
    "timescale/timescaledb": _POSTGRES,
    "timescale/timescaledb-ha": _POSTGRES,
    "pgvector/pgvector": _POSTGRES,
    "mysql": _MYSQL,
    "mariadb": _MARIADB,
    "bitnami/postgresql": {"requises": [["POSTGRESQL_PASSWORD", "ALLOW_EMPTY_PASSWORD"]], "utiles": ["TZ"]},
    "bitnami/mysql": {"requises": [["MYSQL_ROOT_PASSWORD", "ALLOW_EMPTY_PASSWORD"]], "utiles": ["MYSQL_DATABASE"]},
    "bitnami/mariadb": {
        "requises": [["MARIADB_ROOT_PASSWORD", "ALLOW_EMPTY_PASSWORD"]],
        "utiles": ["MARIADB_DATABASE"],
    },
    "mcr.microsoft.com/mssql/server": {"requises": [["ACCEPT_EULA"], ["MSSQL_SA_PASSWORD"]], "utiles": ["TZ"]},
    "gvenzl/oracle-xe": {"requises": [["ORACLE_PASSWORD", "ORACLE_RANDOM_PASSWORD"]], "utiles": []},
    "gvenzl/oracle-free": {"requises": [["ORACLE_PASSWORD", "ORACLE_RANDOM_PASSWORD"]], "utiles": []},
    "mongo": {"requises": [], "utiles": ["MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD", "TZ"]},
    "rabbitmq": {"requises": [], "utiles": ["RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS"]},
    "minio/minio": {"requises": [], "utiles": ["MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"]},
    "grafana/grafana": {"requises": [], "utiles": ["GF_SECURITY_ADMIN_PASSWORD", "TZ"]},
    "wordpress": {
        "requises": [],
        "utiles": ["WORDPRESS_DB_HOST", "WORDPRESS_DB_USER", "WORDPRESS_DB_PASSWORD", "WORDPRESS_DB_NAME"],
    },
    "nextcloud": {"requises": [], "utiles": ["NEXTCLOUD_ADMIN_USER", "NEXTCLOUD_ADMIN_PASSWORD"]},
}

_DOCKER_HUB = ("docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com")
# Mirrors people use for the same official images.
_ALIASES = {"public.ecr.aws/docker/library/": "", "mirror.gcr.io/library/": "", "mirror.gcr.io/": ""}
_REGISTRY = re.compile(r"^[^/]+[.:][^/]*/|^localhost/")


def repository(image):
    """'docker.io/library/postgres:16-alpine@sha256:...' -> 'postgres'; other registries keep their host."""
    ref = (image or "").strip().lower()
    ref = ref.split("@", 1)[0]
    last = ref.rsplit("/", 1)
    if ":" in last[-1]:
        ref = ref[: ref.rfind(":")]
    for prefix, repl in _ALIASES.items():
        if ref.startswith(prefix):
            ref = repl + ref[len(prefix) :]
    host = ref.split("/", 1)[0]
    if host in _DOCKER_HUB and "/" in ref:
        ref = ref.split("/", 1)[1]
    elif _REGISTRY.match(ref):
        return ref
    return ref[len("library/") :] if ref.startswith("library/") else ref


def _describe(name):
    description, secret, generated = _VARS.get(name, ("", False, False))
    return {"nom": name, "description": description, "secret": secret, "generer": generated}


def hints(image):
    """{"image", "requises": [[var, ...], ...], "utiles": [var, ...]} with each variable described."""
    known = KNOWN.get(repository(image), {"requises": [], "utiles": []})
    return {
        "image": repository(image),
        "requises": [[_describe(v) for v in group] for group in known["requises"]],
        "utiles": [_describe(v) for v in known["utiles"]],
    }


def missing(image, env):
    """Error messages for the requirements `env` does not meet (empty list when it does)."""
    env = env or {}
    errors = []
    for group in KNOWN.get(repository(image), {"requises": []})["requises"]:
        if not any(str(env.get(v, "")).strip() for v in group):
            first = _describe(group[0])
            alternatives = f" (or {', '.join(group[1:])})" if len(group) > 1 else ""
            errors.append(
                f"Image '{repository(image)}' does not start without {group[0]}{alternatives}: "
                f"{first['description']}. Add it to the environment variables"
            )
    return errors
