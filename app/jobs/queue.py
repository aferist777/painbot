import json
import sqlite3
import time
from typing import Any, Optional

from app.db.base import q1, x

RETRY_DELAYS = [15, 60, 300]  # seconds, per attempt


def reclaim_orphans() -> int:
    """A job that was running when the process died stays running forever,
    because claim() only ever looks at queued rows. Put them back on startup."""
    stuck = q1("SELECT COUNT(*) AS n FROM jobs WHERE status='running'")
    count = stuck["n"] if stuck else 0
    if count:
        x(
            "UPDATE jobs SET status='queued', run_after=? WHERE status='running'",
            int(time.time()) + 5,
        )
    return count


def enqueue(
    kind: str,
    payload: Optional[dict] = None,
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
) -> int:
    return x(
        "INSERT INTO jobs(kind, payload_json, status, chat_id, message_id, created_at) "
        "VALUES(?, ?, 'queued', ?, ?, ?)",
        kind,
        json.dumps(payload or {}, ensure_ascii=False),
        chat_id,
        message_id,
        int(time.time()),
    )


def claim(kinds: Optional[set] = None, exclude: Optional[set] = None) -> Optional[sqlite3.Row]:
    """Take the oldest runnable job and mark it running.

    The filters let a second, light lane pick up quick jobs while a long one
    holds the main lane — otherwise approving a pain during a forty-minute
    sweep would leave its ideas queued until the sweep ended.
    """
    where = "status='queued' AND (run_after IS NULL OR run_after <= ?)"
    args: list = [int(time.time())]
    if kinds:
        where += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args += sorted(kinds)
    if exclude:
        where += " AND kind NOT IN (%s)" % ",".join("?" * len(exclude))
        args += sorted(exclude)
    row = q1(f"SELECT * FROM jobs WHERE {where} ORDER BY id LIMIT 1", *args)
    if row is None:
        return None
    x(
        "UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
        int(time.time()),
        row["id"],
    )
    return q1("SELECT * FROM jobs WHERE id=?", row["id"])


def finish(job_id: int) -> None:
    x("UPDATE jobs SET status='done', finished_at=?, error=NULL WHERE id=?", int(time.time()), job_id)


def fail(job_id: int, attempts: int, error: str) -> bool:
    """Reschedule with backoff, or mark failed. Returns True if it will retry."""
    error = error[:2000]
    if attempts <= len(RETRY_DELAYS):
        delay = RETRY_DELAYS[attempts - 1]
        x(
            "UPDATE jobs SET status='queued', run_after=?, error=? WHERE id=?",
            int(time.time()) + delay,
            error,
            job_id,
        )
        return True
    x(
        "UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",
        int(time.time()),
        error,
        job_id,
    )
    return False


def payload_of(row: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return {}
