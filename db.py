import os
import sqlite3
from contextlib import closing
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DB_PATH = os.getenv("DB_PATH", "summaries.db")
TRACKING_PARAMS = {"fbclid", "gclid"}


def normalize_url(url: str) -> str:
    """Make equivalent URLs identical so they share one cache entry."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.startswith("utm_") and k not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), "")
    )


def _connect() -> sqlite3.Connection:
    # One connection per call: sqlite connections can't be shared across threads,
    # and FastAPI runs sync endpoints in a thread pool.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                url        TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                summary    TEXT NOT NULL,
                model      TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def get_summary(url: str) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT url, title, summary, model, created_at FROM summaries WHERE url = ?",
            (normalize_url(url),),
        ).fetchone()
    return dict(row) if row else None


def save_summary(url: str, title: str, summary: str, model: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO summaries (url, title, summary, model) "
            "VALUES (?, ?, ?, ?)",
            (normalize_url(url), title, summary, model),
        )