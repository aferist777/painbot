"""Handing a finished reel over: the video with its Instagram caption, and the
three Threads posts hanging off it as a reply.

Both the one-button pipeline and a standalone re-render end here, so the two
paths cannot drift apart, and the rewrite job rebuilds the same two messages
from the same functions.
"""
import asyncio
import html
import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)

from app.bot.keyboards import close_kb
from app.config import FRAME_H, FRAME_W, TG_UPLOAD_LIMIT
from app.db.base import q1, x
from app.llm import social

log = logging.getLogger("painbot.deliver")

# Telegram counts visible characters, so the HTML tags around a block are free.
CAPTION_LIMIT = 1024


def _block(text: str) -> str:
    """A <pre> block: monospace, and one tap copies the whole thing."""
    return f"<pre>{html.escape(text)}</pre>"


def caption_html(name: str, note: str, ig: Optional[str]) -> str:
    """Stats outside the block, post inside — copying must not drag the stats along."""
    head = f"🎥 <b>{html.escape(name)}</b>\n<i>{html.escape(note)}</i>"
    if not ig:
        return head
    room = CAPTION_LIMIT - len(name) - len(note) - 8
    return head + "\n" + _block(social.clip(ig, room))


def threads_html(posts: list[dict]) -> str:
    lines = ["🧵 <b>Threads · три варианта</b>"]
    for index, post in enumerate(posts, start=1):
        lines.append(f"\n<b>{index} · {html.escape(post['format'])}</b>")
        lines.append(_block(post["text"]))
    return "\n".join(lines)


def video_kb(script_id: int, link: Optional[str]) -> InlineKeyboardMarkup:
    extra = []
    if link:
        extra.append(InlineKeyboardButton(text="⬇️ Скачать оригинал", url=link))
    extra.append(
        InlineKeyboardButton(text="🔁 Перемонтировать", callback_data=f"reel:render:{script_id}")
    )
    return close_kb(extra)


def threads_kb(script_id: int) -> InlineKeyboardMarkup:
    return close_kb(
        [
            InlineKeyboardButton(
                text="🔄 Переписать тексты", callback_data=f"social:redo:{script_id}"
            )
        ]
    )


def stats_note(row: Any) -> str:
    return f"{row['duration'] or 0:.0f} сек · {(row['size_bytes'] or 0) / 1024 / 1024:.1f} МБ"


def video_row(script_id: int) -> Optional[Any]:
    """Everything needed to rebuild the video message: name, stats, where it sits."""
    return q1(
        "SELECT i.name, s.post_chat_id, s.post_msg_id, "
        "r.duration, r.size_bytes, r.public_url "
        "FROM scripts s JOIN ideas i ON i.id = s.idea_id "
        "LEFT JOIN renders r ON r.script_id = s.id AND r.status='done' "
        "WHERE s.id=? ORDER BY r.created_at DESC",
        script_id,
    )


async def texts_for(script_id: int) -> Optional[dict]:
    """A failed text pass must never swallow a video that is already rendered."""
    try:
        return await asyncio.to_thread(social.ensure, script_id)
    except Exception as exc:
        log.warning("script %s: тексты не собрались: %s", script_id, exc)
        return None


async def send_threads(
    bot: Bot,
    chat_id: int,
    script_id: int,
    texts: Optional[dict],
    reply_to: Optional[int] = None,
) -> Optional[Message]:
    if not texts or not texts["threads"]:
        return None
    return await bot.send_message(
        chat_id,
        threads_html(texts["threads"]),
        reply_parameters=ReplyParameters(message_id=reply_to) if reply_to else None,
        reply_markup=threads_kb(script_id),
    )


async def send_reel(
    bot: Bot, chat_id: int, script_id: int, name: str, video: dict, note: str
) -> None:
    texts = await texts_for(script_id)
    ig = texts["ig"] if texts else None
    markup = video_kb(script_id, video.get("link"))

    if video["size"] <= TG_UPLOAD_LIMIT:
        sent = await bot.send_video(
            chat_id,
            FSInputFile(video["path"]),
            caption=caption_html(name, note, ig),
            width=FRAME_W,
            height=FRAME_H,
            supports_streaming=True,
            reply_markup=markup,
        )
    else:
        size_mb = video["size"] / 1024 / 1024
        sent = await bot.send_message(
            chat_id,
            caption_html(
                name, f"{note} · {size_mb:.1f} МБ, больше лимита Telegram — по ссылке", ig
            ),
            reply_markup=markup,
        )

    # Remembered so the rewrite button can edit this exact message later.
    x(
        "UPDATE scripts SET post_chat_id=?, post_msg_id=? WHERE id=?",
        sent.chat.id, sent.message_id, script_id,
    )
    await send_threads(bot, chat_id, script_id, texts, reply_to=sent.message_id)
