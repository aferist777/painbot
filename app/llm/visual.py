"""One visual generator for two shapes: wide article pictures and 9:16 frames."""
import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.llm import client as llm
from app.llm.promptfile import load as prompt

log = logging.getLogger("painbot.visual")


Format = Literal["wide", "vertical"]


class VisualSpec(BaseModel):
    kind: Literal["mockup", "diagram"]
    code: str


def make(
    kind: str,
    brief: str,
    product: dict[str, Any],
    fmt: Format = "wide",
    on_screen: str = "",
    job_id: Optional[int] = None,
) -> VisualSpec:
    payload = {
        "kind": kind,
        "format": fmt,
        "что_показать": brief,
        "надпись_на_кадре": on_screen,
        **product,
    }
    return llm.parse(
        VisualSpec,
        json.dumps(payload, ensure_ascii=False, indent=1),
        model=llm.IDEATE_MODEL,
        system=prompt("visual"),
        max_tokens=40000,
        job_id=job_id,
    )
