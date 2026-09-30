"""Who may read the API's interactive documentation (Administration > API in the dashboard).

The documentation is served inside the dashboard, to signed-in users only: its schema comes from an authenticated
endpoint and its "Try it out" calls carry the reader's own session, so they are limited by that user's rights and
audited like any other call. An administrator chooses who may open it: nobody, administrators (the default) or every
signed-in user. The public /docs pages (HYPERLITE_API_DOCS, app/core/http_headers.py) stay for development only.
"""

from app.core.database import get_conn

ACCESS = ("desactive", "admins", "tous")
DEFAULT = "admins"
_KEY = "api_docs_access"


def access():
    with get_conn() as db:
        row = db.execute("SELECT valeur FROM app_settings WHERE cle = ?", (_KEY,)).fetchone()
    value = row["valeur"] if row else DEFAULT
    return value if value in ACCESS else DEFAULT


def set_access(value):
    if value not in ACCESS:
        raise ValueError(f"Access must be one of {', '.join(ACCESS)}")
    with get_conn() as db:
        db.execute(
            "INSERT INTO app_settings (cle, valeur) VALUES (?, ?) ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (_KEY, value),
        )
        db.commit()
    return value


def allowed(user, value=None):
    value = value or access()
    return value == "tous" or (value == "admins" and user.get("role") == "admin")
