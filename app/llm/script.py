"""Reel script for one training episode.

The model writes the middle; the greeting and the sign-off are constants, so
every episode opens and closes the same way and the reels read as one series.
"""
import json
import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.admin.state import tune
from app.db.base import q1, x
from app.db.repo import now
from app.llm import client as llm, show
from app.llm.article import context as idea_context

from app.llm.promptfile import load as prompt

log = logging.getLogger("painbot.script")


# Russian TTS at a normal pace lands near 15 characters per second. Real timings
# replace this once the voice track exists.
CHARS_PER_SECOND = 16.3  # measured at tempo 1.15
MIN_BEAT_SECONDS = 2.0
TARGET_RANGE = (80, 100)

Role = Literal["bet", "warmup", "task", "pain", "step", "trap"]

# Which frame each role gets. Only task and pain need a generated picture; the
# host frames are fixed files and the step frames are drawn from the beat list.
FRAME_OF_ROLE = {
    "greeting": "host_hello",
    "bet": "title",
    "warmup": "title",
    "task": "mockup",
    "pain": "mockup",
    "step": "steps",
    "trap": "host_warn",
    "closing": "host_bye",
}


class Beat(BaseModel):
    role: Role
    vo: str
    on_screen: str = ""
    visual_brief: str = ""
    keys: list[str] = Field(default_factory=list)


class Script(BaseModel):
    beats: list[Beat] = Field(default_factory=list)


def target_range() -> tuple:
    return tune("script.target_range", TARGET_RANGE)


def estimate_seconds(text: str) -> float:
    clean = re.sub(r"\s+", " ", text or "").strip()
    per_second = tune("script.chars_per_second", CHARS_PER_SECOND)
    return max(len(clean) / per_second, tune("script.min_beat", MIN_BEAT_SECONDS))


def _payload(row: Any) -> str:
    return json.dumps(
        {
            "продукт": row["name"],
            "one_liner": row["one_liner"],
            "боль": row["title_ru"],
            "описание_боли": row["summary"],
            "аудитория": row["audience"],
            "цитата": row["evidence_quote"],
            "why_now": row["why_now"] or "",
            "mvp": row["mvp_scope"],
            "часы": row["effort_hours"],
            "режем": row["cut_list"],
            "подвох": row["moat_note"],
        },
        ensure_ascii=False,
        indent=1,
    )


def _next_task_no() -> int:
    row = q1("SELECT COALESCE(MAX(task_no), 0) AS n FROM scripts")
    return (row["n"] if row else 0) + 1


def _assemble(generated: list[Beat], task_no: int, effort: int) -> list[dict]:
    """Wrap the generated middle in the constant opening and closing."""
    beats: list[dict] = []

    def add(role: str, vo: str, on_screen: str, brief: str = "") -> None:
        index = len(beats)
        beats.append(
            {
                "idx": index,
                "role": role,
                "vo": vo.strip(),
                "seconds": round(estimate_seconds(vo), 1),
                "visual_kind": FRAME_OF_ROLE[role],
                "visual_brief": brief.strip(),
                "on_screen": on_screen.strip(),
                "keys": [],
            }
        )

    add("greeting", show.greeting(), show.greeting_on_screen())
    seen_trap = False
    for beat in generated:
        role = beat.role
        if role == "trap":
            # two warnings in a row would show the same host frame twice
            frame_role = "trap" if not seen_trap else "warmup"
            seen_trap = True
        else:
            frame_role = role
        index = len(beats)
        beats.append(
            {
                "idx": index,
                "role": role,
                "vo": beat.vo.strip(),
                "seconds": round(estimate_seconds(beat.vo), 1),
                "visual_kind": FRAME_OF_ROLE[frame_role],
                "visual_brief": beat.visual_brief.strip(),
                "on_screen": beat.on_screen.strip(),
                "keys": [k.strip() for k in beat.keys if k.strip()][:3],
            }
        )
    add("closing", show.closing(task_no, effort), show.anchor())
    return beats


def generate(idea_id: int, job_id: Optional[int] = None) -> int:
    row = idea_context(idea_id)
    if row is None:
        raise ValueError(f"идея #{idea_id} не найдена")

    feasibility = int(row["solo_feasibility"] or 0)
    if feasibility < show.min_feasibility():
        raise ValueError(
            f"выполнимость {feasibility}/5 — такое задание нечестно выдавать "
            f"как тренировку, ролик не делаем"
        )

    # fourteen beats with key phrases outgrew the old 8k ceiling and the JSON
    # came back truncated
    result = llm.parse(
        Script, _payload(row), model=llm.WRITE_MODEL, system=prompt("script"),
        max_tokens=40000, job_id=job_id,
    )

    existing = q1("SELECT id, task_no FROM scripts WHERE idea_id=?", idea_id)
    task_no = (existing["task_no"] if existing and existing["task_no"] else None) \
        or _next_task_no()
    effort = int(row["effort_hours"] or 0)

    beats = _assemble(result.beats, task_no, effort)
    vo_text = " ".join(beat["vo"] for beat in beats)
    duration = round(sum(beat["seconds"] for beat in beats), 1)
    level = show.level_of(feasibility)
    hook = next((b["vo"] for b in beats if b["role"] == "bet"), show.greeting())
    payload = json.dumps(beats, ensure_ascii=False)

    if existing:
        script_id = existing["id"]
        x(
            "UPDATE scripts SET hook=?, beats_json=?, vo_text=?, duration_est=?, "
            "task_no=?, level=?, ig_caption=NULL, threads_json=NULL WHERE id=?",
            hook, payload, vo_text, duration, task_no, level, script_id,
        )
        x("DELETE FROM assets WHERE script_id=?", script_id)
    else:
        script_id = x(
            "INSERT INTO scripts(idea_id, hook, beats_json, vo_text, duration_est, "
            "task_no, level, created_at) VALUES(?,?,?,?,?,?,?,?)",
            idea_id, hook, payload, vo_text, duration, task_no, level, now(),
        )

    log.info("script %s: задание №%s, %s битов, ~%sс", script_id, task_no, len(beats), duration)
    return script_id


def load(script_id: int) -> tuple[Any, list[dict]]:
    row = q1("SELECT * FROM scripts WHERE id=?", script_id)
    if row is None:
        raise ValueError(f"сценарий #{script_id} не найден")
    return row, json.loads(row["beats_json"] or "[]")
