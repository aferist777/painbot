"""Turn a stored article into Telegram rich-message blocks.

Bot API 10.1 posts carry real headings, lists, tables and inline images, so the
old trick of slicing a long text into 4096-character messages is gone.
"""
import json
import logging
from pathlib import Path
from typing import Any

from aiogram.types import (
    FSInputFile,
    InputMediaPhoto,
    InputRichBlockBlockQuotation,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockList,
    InputRichBlockListItem,
    InputRichBlockParagraph,
    InputRichBlockPhoto,
    InputRichBlockPreformatted,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichMessage,
    RichBlockCaption,
    RichBlockTableCell,
)

from app.db.base import q, q1

log = logging.getLogger("painbot.post")

# Heading size runs 1 (largest) to 6; the article's own level 2/3 maps onto 2/4
# so the post title stays visibly bigger than its sections.
HEADING_SIZE = {2: 2, 3: 4}


def _cell(text: str, header: bool = False) -> RichBlockTableCell:
    return RichBlockTableCell(
        align="left", valign="middle", text=text or "", is_header=header or None
    )


def _list(block: dict) -> InputRichBlockList:
    items = []
    for number, raw in enumerate(block.get("items") or [], start=1):
        item = InputRichBlockListItem(blocks=[InputRichBlockParagraph(text=str(raw))])
        if block.get("ordered"):
            item.value = number
            item.type = "1"
        items.append(item)
    return InputRichBlockList(items=items)


def _table(block: dict) -> InputRichBlockTable:
    cells = []
    headers = block.get("table_headers") or []
    if headers:
        cells.append([_cell(h, header=True) for h in headers])
    for row in block.get("table_rows") or []:
        cells.append([_cell(str(value)) for value in row])
    return InputRichBlockTable(cells=cells, is_bordered=True, is_striped=True)


def build(article_id: int) -> InputRichMessage:
    article = q1("SELECT * FROM articles WHERE id=?", article_id)
    if article is None:
        raise ValueError(f"разбор #{article_id} не найден")
    payload = json.loads(article["blocks_json"] or "{}")

    images = {
        row["block_idx"]: row
        for row in q(
            "SELECT * FROM article_assets WHERE article_id=? AND status='done'",
            article_id,
        )
    }

    blocks: list[Any] = [
        InputRichBlockSectionHeading(text=payload.get("title", "Разбор"), size=1)
    ]
    if payload.get("lead"):
        blocks.append(InputRichBlockParagraph(text=payload["lead"]))
    blocks.append(InputRichBlockDivider())

    for index, block in enumerate(payload.get("blocks") or []):
        kind = block.get("kind")

        if kind == "heading":
            blocks.append(
                InputRichBlockSectionHeading(
                    text=block.get("text", ""),
                    size=HEADING_SIZE.get(int(block.get("level") or 2), 3),
                )
            )
        elif kind == "para" and block.get("text"):
            blocks.append(InputRichBlockParagraph(text=block["text"]))
        elif kind == "list" and block.get("items"):
            blocks.append(_list(block))
        elif kind == "quote" and block.get("text"):
            blocks.append(
                InputRichBlockBlockQuotation(
                    blocks=[InputRichBlockParagraph(text=block["text"])]
                )
            )
        elif kind == "code" and block.get("text"):
            blocks.append(InputRichBlockPreformatted(text=block["text"]))
        elif kind == "table" and (block.get("table_rows") or block.get("table_headers")):
            blocks.append(_table(block))
        elif kind == "divider":
            blocks.append(InputRichBlockDivider())
        elif kind == "visual":
            asset = images.get(index)
            if asset is None or not asset["path"] or not Path(asset["path"]).exists():
                continue  # a picture that failed to render must not block the post
            caption = asset["caption"] or block.get("caption") or ""
            blocks.append(
                InputRichBlockPhoto(
                    photo=InputMediaPhoto(media=FSInputFile(asset["path"])),
                    caption=RichBlockCaption(text=caption) if caption else None,
                )
            )

    if payload.get("footer"):
        blocks.append(InputRichBlockDivider())
        blocks.append(InputRichBlockFooter(text=payload["footer"]))

    return InputRichMessage(blocks=blocks)
