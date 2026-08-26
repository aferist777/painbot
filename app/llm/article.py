"""Two-pass article generation: structure first, then each visual separately.

Splitting the passes keeps each prompt focused and makes a single picture
regenerable without touching the text.
"""
import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.db.base import q, q1, x
from app.db.repo import now
from app.llm import client as llm
from app.llm.promptfile import load as prompt
from app.llm.visual import VisualSpec, make as make_visual

log = logging.getLogger("painbot.article")

PROMPTS = Path(__file__).parent / "prompts"

BlockKind = Literal["heading", "para", "list", "quote", "code", "table", "visual", "divider"]


class ArticleBlock(BaseModel):
    kind: BlockKind
    text: str = ""
    level: int = 2
    items: list[str] = Field(default_factory=list)
    ordered: bool = False
    table_headers: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)
    visual_kind: Literal["mockup", "diagram", "none"] = "none"
    visual_brief: str = ""
    caption: str = ""

    @field_validator("visual_kind", mode="before")
    @classmethod
    def _no_picture(cls, value: Any) -> Any:
        """Gemini answers "" where it means "none" and sinks the whole plan."""
        return value if value in ("mockup", "diagram") else "none"


class ArticlePlan(BaseModel):
    title: str
    lead: str
    blocks: list[ArticleBlock]
    footer: str = ""


def context(idea_id: int) -> Optional[Any]:
    return q1(
        "SELECT i.*, p.title_ru, p.summary, p.audience, p.evidence_quote, p.era, "
        "p.why_now, p.severity, p.willingness_to_pay, p.saturation, "
        "p.solo_feasibility, "
        "r.url, r.body AS raw_body, s.kind AS source_kind, s.name AS source_name "
        "FROM ideas i JOIN pains p ON p.id = i.pain_id "
        "JOIN raw_items r ON r.id = p.raw_item_id "
        "JOIN sources s ON s.id = r.source_id WHERE i.id=?",
        idea_id,
    )


def _payload(row: Any) -> str:
    return json.dumps(
        {
            "боль": row["title_ru"],
            "описание_боли": row["summary"],
            "аудитория": row["audience"],
            "цитата": row["evidence_quote"],
            "источник": row["source_kind"] + "/" + row["source_name"],
            "ссылка": row["url"],
            "era": row["era"],
            "why_now": row["why_now"] or "",
            "продукт": row["name"],
            "one_liner": row["one_liner"],
            "mvp": row["mvp_scope"],
            "стек": json.loads(row["stack_json"] or "[]"),
            "интеграции": json.loads(row["integrations_json"] or "[]"),
            "схема_бд": row["db_sketch"],
            "часы": row["effort_hours"],
            "режем": row["cut_list"],
            "защита": row["moat_note"],
            "исходный_текст": (row["raw_body"] or "")[:1200],
        },
        ensure_ascii=False,
        indent=1,
    )


def plan(idea_id: int, job_id: Optional[int] = None) -> int:
    """Generate the article structure and queue its visuals. Returns article id."""
    row = context(idea_id)
    if row is None:
        raise ValueError(f"идея #{idea_id} не найдена")

    result = llm.parse(
        ArticlePlan,
        _payload(row),
        model=llm.WRITE_MODEL,
        system=prompt("article"),
        max_tokens=40000,
        job_id=job_id,
    )

    blocks = [block.model_dump() for block in result.blocks]
    payload = {"title": result.title, "lead": result.lead, "footer": result.footer,
               "blocks": blocks}

    existing = q1("SELECT id FROM articles WHERE idea_id=?", idea_id)
    if existing:
        article_id = existing["id"]
        x("UPDATE articles SET blocks_json=?, md_text=NULL WHERE id=?",
          json.dumps(payload, ensure_ascii=False), article_id)
        x("DELETE FROM article_assets WHERE article_id=?", article_id)
    else:
        article_id = x(
            "INSERT INTO articles(idea_id, blocks_json, created_at) VALUES(?,?,?)",
            idea_id, json.dumps(payload, ensure_ascii=False), now(),
        )

    for index, block in enumerate(blocks):
        if block["kind"] != "visual" or block["visual_kind"] == "none":
            continue
        x(
            "INSERT OR REPLACE INTO article_assets(article_id, block_idx, kind, "
            "brief, caption, status, created_at) VALUES(?,?,?,?,?, 'pending', ?)",
            article_id, index, block["visual_kind"], block["visual_brief"],
            block["caption"], now(),
        )

    log.info("article %s: %s blocks, %s visuals", article_id, len(blocks),
             len(q("SELECT id FROM article_assets WHERE article_id=?", article_id)))
    return article_id


def visual_code(article_id: int, block_idx: int, job_id: Optional[int] = None) -> VisualSpec:
    asset = q1(
        "SELECT * FROM article_assets WHERE article_id=? AND block_idx=?",
        article_id, block_idx,
    )
    if asset is None:
        raise ValueError("визуал не найден")
    article = q1("SELECT idea_id FROM articles WHERE id=?", article_id)
    row = context(article["idea_id"])

    spec = make_visual(
        asset["kind"],
        asset["brief"] or "",
        {
            "продукт": row["name"],
            "one_liner": row["one_liner"],
            "стек": json.loads(row["stack_json"] or "[]"),
            "схема_бд": row["db_sketch"],
        },
        fmt="wide",
        job_id=job_id,
    )
    x("UPDATE article_assets SET spec=? WHERE id=?", spec.code, asset["id"])
    return spec
