"""Jobs: write the breakdown, draw its visuals, preview it, publish it."""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import MEDIA_DIR
from app.db.base import q, q1, x
from app.bot.keyboards import close_kb
from app.db.repo import now, sget
from app.jobs.progress import Progress
from app.jobs.worker import register
from app.llm.article import plan, visual_code
from app.media import post as post_builder
from app.media.render import render_diagram, render_mockup

log = logging.getLogger("painbot.jobs.article")


def _render_asset(article_id: int, block_idx: int) -> None:
    spec = visual_code(article_id, block_idx)
    out = MEDIA_DIR / "articles" / str(article_id) / f"block-{block_idx:02d}.png"
    if spec.kind == "diagram":
        render_diagram(spec.code, out)
    else:
        render_mockup(spec.code, out)
    x(
        "UPDATE article_assets SET path=?, status='done' WHERE article_id=? AND block_idx=?",
        str(out), article_id, block_idx,
    )


@register("article.run")
async def article_run(payload: dict, progress: Progress, bot: Bot) -> None:
    idea_id = int(payload["idea_id"])
    idea = q1("SELECT name FROM ideas WHERE id=?", idea_id)
    name = idea["name"] if idea else f"#{idea_id}"

    progress.title = "📄 Пишу разбор"
    await progress.update(0, 3, note=name, force=True)

    article_id = await asyncio.to_thread(plan, idea_id, None)
    assets = q("SELECT block_idx FROM article_assets WHERE article_id=?", article_id)
    total = 1 + len(assets)
    await progress.update(1, total, note="текст готов, рисую картинки")

    for done, asset in enumerate(assets, start=1):
        try:
            await asyncio.to_thread(_render_asset, article_id, asset["block_idx"])
        except Exception as exc:  # a broken picture must not kill the post
            log.warning("visual %s failed: %s", asset["block_idx"], exc)
            x(
                "UPDATE article_assets SET status='failed' WHERE article_id=? AND block_idx=?",
                article_id, asset["block_idx"],
            )
        await progress.update(1 + done, total, note=f"картинка {done}/{len(assets)}")

    ok = q1(
        "SELECT COUNT(*) AS n FROM article_assets WHERE article_id=? AND status='done'",
        article_id,
    )["n"]

    # The preview below is the result; the bar has nothing left to say.
    await progress.delete()

    if not progress.chat_id:
        return

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🚀 Опубликовать в канал", callback_data=f"art:pub:{article_id}"
        )
    )
    kb.row(
        InlineKeyboardButton(text="🔁 Переписать", callback_data=f"art:redo:{idea_id}"),
        InlineKeyboardButton(text="✖️ Закрыть", callback_data="ui:close"),
    )
    preview = await bot.send_rich_message(
        chat_id=progress.chat_id,
        rich_message=post_builder.build(article_id),
        reply_markup=kb.as_markup(),
    )
    x(
        "UPDATE articles SET preview_chat_id=?, preview_msg_id=? WHERE id=?",
        preview.chat.id, preview.message_id, article_id,
    )
    log.info("article %s previewed (%s visuals ok of %s)", article_id, ok, len(assets))


@register("article.publish")
async def article_publish(payload: dict, progress: Progress, bot: Bot) -> None:
    article_id = int(payload["article_id"])
    channel_id = sget("channel_id")
    if not channel_id:
        await progress.done(
            "🚀 Канал не привязан.\n\n"
            "Добавь бота в канал администратором — я привяжусь сам.",
            close_kb(),
        )
        return

    message = await bot.send_rich_message(
        chat_id=int(channel_id), rich_message=post_builder.build(article_id)
    )
    x(
        "UPDATE articles SET channel_msg_id=?, published_at=? WHERE id=?",
        message.message_id, now(), article_id,
    )

    await progress.done(f"🚀 Опубликовано в «{sget('channel_title') or 'канал'}».")

    # Show the confirmation briefly, then clear both it and the local preview:
    # the post now lives in the channel and the chat should not keep copies.
    await asyncio.sleep(3)
    row = q1(
        "SELECT preview_chat_id, preview_msg_id FROM articles WHERE id=?", article_id
    )
    if row and row["preview_chat_id"] and row["preview_msg_id"]:
        try:
            await bot.delete_message(row["preview_chat_id"], row["preview_msg_id"])
        except TelegramBadRequest:
            pass
        x(
            "UPDATE articles SET preview_chat_id=NULL, preview_msg_id=NULL WHERE id=?",
            article_id,
        )
    await progress.delete()
