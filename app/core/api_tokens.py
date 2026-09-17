"""Jetons API -- chantier 30 (2026-09-17). Mecanisme d'authentification
distinct des JWT de session : pense pour l'automatisation (scripts,
Terraform, cron...), pas d'expiration courte, revocable individuellement
sans affecter la session web. Comme un mot de passe : le jeton en clair
n'est JAMAIS stocke, seul son hash SHA-256 l'est -- impossible de le
retrouver apres sa creation, affiche UNE SEULE fois cote UI."""
import hashlib
import secrets
from datetime import datetime, timezone

from app.core.database import get_conn

TOKEN_PREFIX = "hlt_"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_token(username: str, name: str):
    """Retourne (id, jeton_en_clair) -- le jeton en clair n'est jamais
    recuperable une fois cette fonction retournee."""
    token = generate_token()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO api_tokens (username, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, name, _hash(token), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        token_id = cur.lastrowid
    return token_id, token


def list_tokens(username: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, last_used_at FROM api_tokens WHERE username = ? ORDER BY created_at DESC",
            (username,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_token(username: str, token_id: int) -> bool:
    """Scope a username : un utilisateur ne peut revoquer que ses propres
    jetons, meme un ID d'un autre compte devine ne fait rien."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM api_tokens WHERE id = ? AND username = ?", (token_id, username))
        conn.commit()
    return cur.rowcount > 0


def verify_token(token: str):
    """Utilisee par security.py::get_current_user en repli quand le jeton
    presente n'est pas un JWT valide. Retourne la ligne utilisateur complete
    (meme forme que security.get_user) ou None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    token_hash = _hash(token)
    with get_conn() as conn:
        row = conn.execute("SELECT username FROM api_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?",
            (datetime.now(timezone.utc).isoformat(), token_hash),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE username = ?", (row["username"],)).fetchone()
    return dict(user) if user else None
