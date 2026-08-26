import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence

from app.config import DB_PATH

_conn: Optional[sqlite3.Connection] = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def _ensure_column(table: str, column: str, ddl: str) -> None:
    """schema.sql only creates; added columns need an ALTER on existing files."""
    existing = {row["name"] for row in conn().execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn().execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn().commit()


def init_db() -> None:
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    c = conn()
    c.executescript(sql)
    c.commit()
    _ensure_column("raw_items", "reject_reason", "TEXT")
    _ensure_column("articles", "blocks_json", "TEXT")
    _ensure_column("articles", "idea_variant", "INTEGER")
    _ensure_column("articles", "preview_chat_id", "INTEGER")
    _ensure_column("articles", "preview_msg_id", "INTEGER")
    _ensure_column("scripts", "task_no", "INTEGER")
    _ensure_column("scripts", "level", "TEXT")
    _ensure_column("scripts", "ig_caption", "TEXT")
    _ensure_column("scripts", "threads_json", "TEXT")
    _ensure_column("scripts", "post_chat_id", "INTEGER")
    _ensure_column("scripts", "post_msg_id", "INTEGER")
    _ensure_column("sources", "tier", "INTEGER DEFAULT 2")
    _ensure_column("sources", "stored_total", "INTEGER DEFAULT 0")
    _ensure_column("sources", "pains_total", "INTEGER DEFAULT 0")
    _ensure_column("pains", "kind", "TEXT DEFAULT 'pain'")


def q(sql: str, *args: Any) -> Sequence[sqlite3.Row]:
    return conn().execute(sql, args).fetchall()


def q1(sql: str, *args: Any) -> Optional[sqlite3.Row]:
    return conn().execute(sql, args).fetchone()


def x(sql: str, *args: Any) -> int:
    cur = conn().execute(sql, args)
    conn().commit()
    return cur.lastrowid or 0
