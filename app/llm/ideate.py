"""Turn an approved pain into one to three buildable product specs."""
import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.db.base import q, q1, x
from app.db.repo import now
from app.llm import client as llm
from app.llm.promptfile import load as prompt

log = logging.getLogger("painbot.ideate")


MORE = (
    "\n\nПридумай РОВНО один новый вариант. Он обязан отличаться формой поставки, "
    "а не только названием: если выше уже есть веб-редактор — предложи CLI, демон, "
    "расширение браузера, телеграм-бот или библиотеку. Если честного нового угла "
    "нет, скажи это прямо в moat_note."
)


class Idea(BaseModel):
    name: str
    one_liner: str
    mvp_scope: str
    stack: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    db_sketch: str = ""
    effort_hours: int = 0
    cut_list: str = ""
    moat_note: str = ""


class Ideas(BaseModel):
    ideas: list[Idea]


def pain_context(pain_id: int) -> Optional[Any]:
    return q1(
        "SELECT p.*, r.url, r.title AS raw_title, r.body AS raw_body, "
        "r.created_utc, s.kind AS source_kind, s.name AS source_name "
        "FROM pains p JOIN raw_items r ON r.id = p.raw_item_id "
        "JOIN sources s ON s.id = r.source_id WHERE p.id=?",
        pain_id,
    )


def _prompt(row: Any, existing: list) -> str:
    payload = {
        "тип": row["kind"] or "pain",
        "боль": row["title_ru"],
        "описание": row["summary"],
        "аудитория": row["audience"],
        "цитата_из_источника": row["evidence_quote"],
        "источник": row["source_kind"] + "/" + row["source_name"],
        "era": row["era"],
        "why_now": row["why_now"] or "",
        "оценки": {
            "severity": row["severity"],
            "willingness_to_pay": row["willingness_to_pay"],
            "solo_feasibility": row["solo_feasibility"],
            "saturation": row["saturation"],
        },
        "исходный_текст": (row["raw_body"] or row["raw_title"] or "")[:1500],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if existing:
        # Names alone were not enough: the model re-served an existing approach
        # under a new name. It has to see what was actually proposed.
        listed = "\n".join(
            "- " + r["name"] + ": " + (r["one_liner"] or "") for r in existing
        )
        text += "\n\nУЖЕ ПРЕДЛОЖЕНО:\n" + listed + MORE
    return text


def generate(pain_id: int, extra: bool = False, job_id: Optional[int] = None) -> int:
    """Write ideas for a pain. Returns how many were stored."""
    row = pain_context(pain_id)
    if row is None:
        raise ValueError(f"боль #{pain_id} не найдена")

    existing = list(
        q("SELECT name, one_liner FROM ideas WHERE pain_id=? ORDER BY variant_no", pain_id)
    )
    if not extra and existing:
        return 0  # already has ideas; only "another variant" adds more

    result = llm.parse(
        Ideas,
        _prompt(row, existing if extra else []),
        model=llm.IDEATE_MODEL,
        system=prompt("ideate"),
        max_tokens=40000,
        job_id=job_id,
    )

    if q1("SELECT id FROM pains WHERE id=?", pain_id) is None:
        # Cleared from the inbox while the model was writing: the insert would
        # only fail on the foreign key and then be retried three times.
        log.info("боль %s удалена, идеи выбрасываю", pain_id)
        return 0

    start = q1(
        "SELECT COALESCE(MAX(variant_no), 0) AS n FROM ideas WHERE pain_id=?", pain_id
    )
    variant = (start["n"] if start else 0) + 1

    ideas = result.ideas[:1] if extra else result.ideas[:3]
    for idea in ideas:
        x(
            "INSERT INTO ideas(pain_id, variant_no, name, one_liner, mvp_scope, "
            "stack_json, integrations_json, db_sketch, effort_hours, cut_list, "
            "moat_note, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            pain_id,
            variant,
            idea.name.strip(),
            idea.one_liner.strip(),
            idea.mvp_scope.strip(),
            json.dumps(idea.stack, ensure_ascii=False),
            json.dumps(idea.integrations, ensure_ascii=False),
            idea.db_sketch.strip(),
            max(idea.effort_hours, 0),
            idea.cut_list.strip(),
            idea.moat_note.strip(),
            now(),
        )
        variant += 1

    log.info("pain %s: stored %s ideas", pain_id, len(ideas))
    return len(ideas)
