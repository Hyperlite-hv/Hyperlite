"""Rows read in batches, newest first (keyset pagination), for the CSV exports: a complete export never has to fit
in memory nor go through the JSON endpoints capped at 1000 rows."""

from app.core.database import get_conn

BATCH = 1000


def newest_first(table, columns, where_clauses, params, key="rowid"):
    """`table`, `columns`, `key` and `where_clauses` are fixed fragments chosen by the repository, never request
    data; every value goes through `params`."""
    last = None
    while True:
        clauses = list(where_clauses) + ([f"{key} < ?"] if last is not None else [])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args = [*params, *([last] if last is not None else []), BATCH]
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT {key} AS _key, {', '.join(columns)} FROM {table} {where} ORDER BY {key} DESC LIMIT ?",  # noqa: S608 -- fixed fragments only, values are bound
                args,
            ).fetchall()
        for row in rows:
            yield [row[c] for c in columns]
        if len(rows) < BATCH:
            return
        last = rows[-1]["_key"]
