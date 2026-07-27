"""
Reads the boss's Excel sheet.

Expected shape (column names are matched loosely, order doesn't matter):
    Report Name | Our URL | Competitor 1 | Competitor 2 | Competitor 3

Falls back gracefully: if headers don't match, it treats the first text column
as the report name and every column containing http(s) links as a URL column.
"""
import re

import pandas as pd

URL_RE = re.compile(r"^https?://", re.I)

OWN_HINTS = ["our", "own", "self", "my", "company", "internal", "primary", "main"]
NAME_HINTS = ["report", "name", "title", "topic", "keyword", "page", "subject"]


def _is_url(v):
    return isinstance(v, str) and bool(URL_RE.match(v.strip()))


def read_excel(file) -> pd.DataFrame:
    if hasattr(file, "name") and str(file.name).lower().endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


def analyze(df: pd.DataFrame):
    """Guess which column is what. Returns (name_col, own_col, competitor_cols)."""
    cols = list(df.columns)
    lower = {c: str(c).lower().strip() for c in cols}

    url_cols = [
        c for c in cols
        if df[c].dropna().astype(str).str.match(URL_RE).mean() > 0.4
    ]

    name_col = None
    for c in cols:
        if c in url_cols:
            continue
        if any(h in lower[c] for h in NAME_HINTS):
            name_col = c
            break
    if name_col is None:
        non_url = [c for c in cols if c not in url_cols]
        name_col = non_url[0] if non_url else None

    own_col = None
    for c in url_cols:
        if any(h in lower[c] for h in OWN_HINTS):
            own_col = c
            break
    if own_col is None and url_cols:
        own_col = url_cols[0]

    comp_cols = [c for c in url_cols if c != own_col]
    return name_col, own_col, comp_cols


def import_df(df, name_col, own_col, comp_cols, replace=False):
    """Write rows into the pages table. Returns (imported, skipped)."""
    from . import db

    db.init()
    if replace:
        db.clear_all_pages()

    imported, skipped = 0, 0
    for idx, row in df.iterrows():
        group = str(row[name_col]).strip() if name_col and pd.notna(row.get(name_col)) \
            else f"Report {idx + 1}"

        if own_col and _is_url(str(row.get(own_col, ""))):
            db.upsert_page(group, "own", "Our page", str(row[own_col]).strip())
            imported += 1
        elif own_col:
            skipped += 1

        for i, c in enumerate(comp_cols, 1):
            val = str(row.get(c, "")).strip()
            if _is_url(val):
                label = str(c).strip() if not str(c).lower().startswith("unnamed") \
                    else f"Competitor {i}"
                db.upsert_page(group, "competitor", label, val)
                imported += 1
            elif val and val.lower() != "nan":
                skipped += 1

    return imported, skipped


def sample_template() -> pd.DataFrame:
    return pd.DataFrame({
        "Report Name": [
            "Global EV Battery Market",
            "Cloud Security Market",
            "Industrial IoT Market",
        ],
        "Our URL": [
            "https://example.com/reports/ev-battery",
            "https://example.com/reports/cloud-security",
            "https://example.com/reports/industrial-iot",
        ],
        "Competitor 1": [
            "https://competitor-a.com/ev-battery-market",
            "https://competitor-a.com/cloud-security-market",
            "https://competitor-a.com/iiot-market",
        ],
        "Competitor 2": [
            "https://competitor-b.com/reports/ev-battery",
            "https://competitor-b.com/reports/cloud-security",
            "https://competitor-b.com/reports/industrial-iot",
        ],
        "Competitor 3": [
            "https://competitor-c.com/market/ev-battery",
            "https://competitor-c.com/market/cloud-security",
            "https://competitor-c.com/market/iiot",
        ],
    })
