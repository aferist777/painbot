import json
import time
from typing import Any, Optional

from app.db.base import q1, x

# ---------------------------------------------------------------- settings


def sget(key: str, default: Optional[str] = None) -> Optional[str]:
    row = q1("SELECT value FROM settings WHERE key=?", key)
    return row["value"] if row else default


def sset(key: str, value: Any) -> None:
    x(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key,
        "" if value is None else str(value),
    )


def sdel(key: str) -> None:
    x("DELETE FROM settings WHERE key=?", key)


def sget_int(key: str, default: Optional[int] = None) -> Optional[int]:
    raw = sget(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def sget_json(key: str, default: Any) -> Any:
    raw = sget(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def sset_json(key: str, value: Any) -> None:
    sset(key, json.dumps(value, ensure_ascii=False))


def now() -> int:
    return int(time.time())
