"""Texts that travel with the finished reel: an Instagram caption and three
Threads posts.

They are written from the stored script, not from the idea: a caption written
from the idea alone starts promising things the voice-over never said. The reel,
the caption and the Threads post have three different jobs — the reel hooks a
stranger, the caption converts someone who already stopped, the Threads post has
to earn a reply on its own — so they are three texts, not three rewordings of one.
"""
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.admin.state import tune
from app.db.base import q1, x
from app.llm import client as llm, show

from app.llm.promptfile import load as prompt

log = logging.getLogger("painbot.social")


# Threads cuts a post at 500 characters; the prompt asks for 480 so one extra
# word does not cost the closing question.
THREADS_LIMIT = 500
# Telegram allows 1024 in a caption and the header above the block eats part of
# it. 600 leaves room for a long product name.
IG_LIMIT = 600

FORMATS = ("вызов", "наблюдение", "список")


class Post(BaseModel):
    # Deliberately not a Literal: this provider answers enum fields with an
    # empty string often enough that it would cost all three posts at once.
    # The order they arrive in is the order they were asked for.
    format: str = ""
    text: str


class Social(BaseModel):
    ig: str
    threads: list[Post] = Field(default_factory=list)


def _context(script_id: int) -> Optional[Any]:
    return q1(
        "SELECT s.id, s.hook, s.beats_json, s.task_no, s.level, "
        "i.name, i.one_liner, i.effort_hours, i.cut_list, i.moat_note, "
        "p.title_ru, p.audience, p.summary "
        "FROM scripts s JOIN ideas i ON i.id = s.idea_id "
        "JOIN pains p ON p.id = i.pain_id WHERE s.id=?",
        script_id,
    )


def _payload(row: Any) -> str:
    beats = json.loads(row["beats_json"] or "[]")

    def voice(*roles: str) -> list[str]:
        return [b["vo"] for b in beats if b["role"] in roles]

    return json.dumps(
        {
            "номер_задания": row["task_no"],
            "уровень": row["level"],
            "продукт": row["name"],
            "one_liner": row["one_liner"],
            "аудитория": row["audience"],
            "пари_из_ролика": row["hook"],
            "разогрев": voice("warmup"),
            "задание": voice("task"),
            "боль": voice("pain"),
            "шаги": voice("step"),
            "подводные_камни": voice("trap"),
            "часы": row["effort_hours"],
            "что_режем": row["cut_list"],
            "подвох": row["moat_note"],
        },
        ensure_ascii=False,
        indent=1,
    )


def clip(text: str, limit: int) -> str:
    """Trim to the last whole sentence that fits, never mid-word."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("."), head.rfind("?"), head.rfind("!"), head.rfind("\n"))
    if cut > limit // 2:
        return head[: cut + 1].strip()
    return head.rsplit(" ", 1)[0].strip()


def _caption(text: str) -> str:
    """The call to action is ours, not the model's: it must not drift."""
    cta = show.cta()
    body = clip(text, tune("social.ig_limit", IG_LIMIT) - len(cta) - 2)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if cta and cta.lower() in body.lower():
        return body
    return (body + "\n\n" + cta).strip()


def _by_position(index: int, taken: dict) -> str:
    """Unlabelled post: it is the n-th one asked for, minus what is taken."""
    free = [name for name in FORMATS if name not in taken]
    if index < len(FORMATS) and FORMATS[index] not in taken:
        return FORMATS[index]
    return free[0] if free else ""


def _posts(posts: list[Post]) -> list[dict]:
    """One post per format, in a fixed order, so the numbering means something."""
    by_format: dict[str, str] = {}
    for index, post in enumerate(posts):
        text = clip(post.text, tune("social.threads_limit", THREADS_LIMIT))
        name = post.format if post.format in FORMATS else _by_position(index, by_format)
        if text and name and name not in by_format:
            by_format[name] = text
    picked = [
        {"format": name, "text": by_format[name]} for name in FORMATS if name in by_format
    ]
    if len(picked) < len(posts):
        log.warning("пришло %s постов, годных %s", len(posts), len(picked))
    return picked


def load(script_id: int) -> Optional[dict]:
    row = q1("SELECT ig_caption, threads_json FROM scripts WHERE id=?", script_id)
    if row is None or not row["ig_caption"]:
        return None
    return {"ig": row["ig_caption"], "threads": json.loads(row["threads_json"] or "[]")}


def generate(script_id: int, job_id: Optional[int] = None) -> dict:
    row = _context(script_id)
    if row is None:
        raise ValueError(f"сценарий #{script_id} не найден")

    result = llm.parse(
        Social, _payload(row), model=llm.WRITE_MODEL, system=prompt("social"),
        max_tokens=8000, job_id=job_id,
    )
    texts = {"ig": _caption(result.ig), "threads": _posts(result.threads)}
    x(
        "UPDATE scripts SET ig_caption=?, threads_json=? WHERE id=?",
        texts["ig"], json.dumps(texts["threads"], ensure_ascii=False), script_id,
    )
    log.info(
        "script %s: подпись %s символов, постов %s",
        script_id, len(texts["ig"]), len(texts["threads"]),
    )
    return texts


def ensure(script_id: int, job_id: Optional[int] = None) -> dict:
    """Reels written before this existed get their texts on first delivery."""
    return load(script_id) or generate(script_id, job_id)
