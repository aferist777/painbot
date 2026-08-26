"""Job: write the reel script for an idea, then show it."""
import asyncio
import logging

from aiogram import Bot

from app.db.base import q1
from app.bot.keyboards import close_kb
from app.jobs.progress import Progress
from app.jobs.worker import register
from app.llm.script import generate

log = logging.getLogger("painbot.jobs.script")


@register("script.run")
async def script_run(payload: dict, progress: Progress, bot: Bot) -> None:
    idea_id = int(payload["idea_id"])
    idea = q1("SELECT name FROM ideas WHERE id=?", idea_id)
    name = idea["name"] if idea else f"#{idea_id}"

    progress.title = "🎬 Пишу сценарий"
    await progress.update(0, 1, note=name, force=True)

    script_id = await asyncio.to_thread(generate, idea_id, None)

    from app.bot.handlers.reel import card_kb, card_text

    if progress.attached:
        await progress.delete()
    if progress.chat_id:
        await bot.send_message(
            progress.chat_id, card_text(script_id), reply_markup=card_kb(script_id, idea_id)
        )
