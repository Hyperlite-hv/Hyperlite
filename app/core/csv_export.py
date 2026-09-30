"""CSV exports streamed row by row, so a complete export never has to fit in memory nor go through the paginated
JSON endpoints (capped at 1000 rows). The rows come from the repositories (app/repositories/sqlite/export.py)."""

import csv
import io

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
