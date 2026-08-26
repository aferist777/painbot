"""Job: rewrite the texts that ship with a reel, leaving the video alone.

Both messages are edited in place — the point of the button is a second opinion
on the wording, not a second copy of everything in the chat.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from app.bot.keyboards import close_kb
from app.jobs import deliver
from app.jobs.progress import Progress
from app.jobs.worker import register
from app.llm import social

log = logging.getLogger("painbot.jobs.social")


async def _recaption(bot: Bot, row, script_id: int, caption: str) -> bool:
    """The video came as media or, when oversized, as plain text. Try both."""
    where = dict(chat_id=row["post_chat_id"], message_id=row["post_msg_id"])
    markup = deliver.video_kb(script_id, row["public_url"])
    try:
        await bot.edit_message_caption(caption=caption, reply_markup=markup, **where)
        return True
    except TelegramBadRequest:
        pass
    try:
        await bot.edit_message_text(caption, reply_markup=markup, **where)
        return True
    except TelegramBadRequest as exc:
        log.warning("script %s: подпись не отредактировалась: %s", script_id, exc)
        return False


@register("social.run")
async def social_run(payload: dict, progress: Progress, bot: Bot) -> None:
    script_id = int(payload["script_id"])
    threads_msg_id = payload.get("threads_msg_id")

    progress.title = "🔄 Переписываю тексты"
    await progress.update(0, 1, "подпись и три поста", force=True)
    texts = await asyncio.to_thread(social.generate, script_id)

    row = deliver.video_row(script_id)
    caption = deliver.caption_html(
        row["name"] if row else "", deliver.stats_note(row) if row else "", texts["ig"]
    )
    done = bool(row and row["post_chat_id"] and row["post_msg_id"]) and await _recaption(
        bot, row, script_id, caption
    )

    await progress.delete()
    if not progress.chat_id:
        return

    if not done:
        # The old message is gone or unreachable; the caption still has to arrive.
        await bot.send_message(progress.chat_id, caption, reply_markup=close_kb())

    body = deliver.threads_html(texts["threads"]) if texts["threads"] else ""
    if body and threads_msg_id:
        try:
            await bot.edit_message_text(
                body,
                chat_id=progress.chat_id,
                message_id=threads_msg_id,
                reply_markup=deliver.threads_kb(script_id),
            )
            return
        except TelegramBadRequest:
            pass
    await deliver.send_threads(
        bot, progress.chat_id, script_id, texts,
        reply_to=row["post_msg_id"] if row and done else None,
    )
