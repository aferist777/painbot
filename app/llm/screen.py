"""Batch screening: a cheap model decides what is a real, solo-buildable IT pain."""
import datetime as dt
import json
import logging
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field

from app.collect import defaults as src
from app.db.base import q, q1, x
from app.db.repo import now
from app.llm import client as llm
from app.llm.promptfile import load as prompt

log = logging.getLogger("painbot.screen")



class Verdict(BaseModel):
    id: int
    is_it: bool
    kind: Literal["pain", "request", "idea", "no"]
    title_ru: str = ""
    summary: str = ""
    audience: str = ""
    evidence_quote: str = ""
    severity: int = Field(default=1, ge=1, le=5)
    willingness_to_pay: int = Field(default=1, ge=1, le=5)
    solo_feasibility: int = Field(default=1, ge=1, le=5)
    saturation: int = Field(default=5, ge=1, le=5)
    why_now: str = ""
    tags: list[str] = Field(default_factory=list)


class Batch(BaseModel):
    items: list[Verdict]


def score_of(v: Verdict) -> int:
    """Five-point scales, weighted, mapped onto 20..100. Saturation is inverted."""
    weighted = (
        0.30 * v.severity
        + 0.30 * v.willingness_to_pay
        + 0.25 * v.solo_feasibility
        + 0.15 * (6 - v.saturation)
    )
    return round(weighted * 20)


def _era(row: Any) -> str:
    try:
        return json.loads(row["raw_json"] or "{}").get("era", "fresh")
    except json.JSONDecodeError:
        return "fresh"


def pending_count() -> int:
    row = q1("SELECT COUNT(*) AS n FROM raw_items WHERE state='new'")
    return row["n"] if row else 0


def _batches(limit: int) -> list[list]:
    rows = q(
        "SELECT r.*, s.kind AS source_kind, s.name AS source_name "
        "FROM raw_items r JOIN sources s ON s.id = r.source_id "
        # FIFO, not by score: upvotes measure popularity, and the most popular
        # posts are the least likely to describe a chore worth automating.
        "WHERE r.state='new' ORDER BY r.id ASC LIMIT ?",
        limit,
    )
    return [list(rows[i : i + src.screen_batch()]) for i in range(0, len(rows), src.screen_batch())]


def _render(rows: list) -> str:
    parts = []
    for row in rows:
        created = row["created_utc"]
        year = dt.datetime.utcfromtimestamp(created).year if created else 0
        kind = row["source_kind"]
        name = row["source_name"]
        parts.append(
            json.dumps(
                {
                    "id": row["id"],
                    "source": kind + "/" + name,
                    "era": _era(row),
                    "year": year,
                    "score": row["score"],
                    "title": row["title"],
                    "body": (row["body"] or "")[:src.batch_chars()],
                },
                ensure_ascii=False,
            )
        )
    return "Оцени каждый элемент:\n\n" + "\n".join(parts)


def _save(row: Any, verdict: Verdict, total: int) -> None:
    x("UPDATE raw_items SET state='screened' WHERE id=?", row["id"])
    x(
        "UPDATE sources SET pains_total = COALESCE(pains_total, 0) + 1 WHERE id=?",
        row["source_id"],
    )
    x(
        "INSERT OR IGNORE INTO pains(raw_item_id, title_ru, summary, audience, "
        "evidence_quote, is_it, era, why_now, severity, willingness_to_pay, "
        "solo_feasibility, saturation, score, tags_json, screened_by, kind, state, "
        "created_at) VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,'inbox',?)",
        row["id"],
        verdict.title_ru.strip() or (row["title"] or "")[:120],
        verdict.summary.strip(),
        verdict.audience.strip(),
        verdict.evidence_quote.strip(),
        _era(row),
        verdict.why_now.strip(),
        verdict.severity,
        verdict.willingness_to_pay,
        verdict.solo_feasibility,
        verdict.saturation,
        total,
        json.dumps(verdict.tags[:4], ensure_ascii=False),
        llm.SCREEN_MODEL,
        verdict.kind,
        now(),
    )


def _judge(rows: list, job_id: Optional[int]) -> Batch:
    """Half the batch on the second try: a truncated answer is a size problem."""
    try:
        return llm.parse(
            Batch, _render(rows), model=llm.SCREEN_MODEL, system=prompt("screen"),
            max_tokens=40000, job_id=job_id,
        )
    except Exception:
        half = max(len(rows) // 2, 1)
        log.warning("retrying the batch at %s items", half)
        return llm.parse(
            Batch, _render(rows[:half]), model=llm.SCREEN_MODEL, system=prompt("screen"),
            max_tokens=40000, job_id=job_id,
        )


def screen(
    limit: int = 200,
    job_id: Optional[int] = None,
    on_batch: Optional[Callable[[int, int, dict], None]] = None,
) -> dict:
    stats = {"seen": 0, "kept": 0, "rejected": 0, "batches": 0}
    batches = _batches(limit)

    for index, rows in enumerate(batches, start=1):
        try:
            result = _judge(rows, job_id)
        except Exception as exc:
            # A batch that will not parse must not take the whole run down, and
            # it must not stay pending either or the loop picks it up forever.
            log.warning("batch %s unusable (%s), dropping it", index, str(exc)[:160])
            for row in rows:
                x(
                    "UPDATE raw_items SET state='rejected', reject_reason=? WHERE id=?",
                    "screen_failed", row["id"],
                )
            stats["rejected"] += len(rows)
            stats["batches"] = index
            continue
        by_id = {row["id"]: row for row in rows}
        for verdict in result.items:
            row = by_id.get(verdict.id)
            if row is None:
                continue
            stats["seen"] += 1
            total = score_of(verdict)
            reason = None
            if not verdict.is_it:
                reason = "not_it"
            elif verdict.kind == "no":
                reason = "not_useful"
            elif total < src.score_threshold():
                reason = f"low_score_{total}"
            if reason:
                x(
                    "UPDATE raw_items SET state='rejected', reject_reason=? WHERE id=?",
                    reason,
                    row["id"],
                )
                stats["rejected"] += 1
                stats.setdefault("reasons", {})
                stats["reasons"][reason.split("_")[0]] = (
                    stats["reasons"].get(reason.split("_")[0], 0) + 1
                )
                continue
            _save(row, verdict, total)
            stats["kept"] += 1

        stats["batches"] = index
        if on_batch is not None:
            on_batch(index, len(batches), stats)

    return stats
