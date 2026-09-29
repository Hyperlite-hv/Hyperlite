"""CSV exports streamed straight from the database, so a complete export never has to
fit in memory nor go through the paginated JSON endpoints (capped at 1000 rows)."""

import csv
import io

from app.core.database import get_conn

BATCH = 1000

# A spreadsheet runs a cell starting with one of these as a formula (CSV injection), and
# these exports carry user-chosen names (VMs, users, resources).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def csv_lines(header, rows):
    """Yield the CSV text of `header` then of each row of `rows`, one line at a time."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue()
    for row in rows:
        buf.seek(0)
        buf.truncate()
        writer.writerow([_cell(v) for v in row])
        yield buf.getvalue()


def newest_first(table, columns, where_clauses, params, key="rowid"):
    """Rows of `table` matching the filters, newest first, read in batches (keyset on
    `key`, one short read per batch) rather than in one long query.

    `table`, `columns`, `key` and `where_clauses` must be fixed fragments chosen by the
    caller, never request data; every value goes through `params`."""
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
