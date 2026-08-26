"""Jobs for the reel: assets (voice + frames) and the finished cut."""
import logging

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton

from app.bot.keyboards import close_kb
from app.db.base import q1
from app.jobs import deliver
from app.jobs.progress import Progress
from app.jobs.steps import make_assets, make_video
from app.jobs.worker import register

log = logging.getLogger("painbot.jobs.reel")


def _name(script_id: int) -> str:
    row = q1(
        "SELECT i.name FROM scripts s JOIN ideas i ON i.id = s.idea_id WHERE s.id=?",
        script_id,
    )
    return row["name"] if row else f"#{script_id}"


@register("reel.assets")
async def reel_assets(payload: dict, progress: Progress, bot: Bot) -> None:
    script_id = int(payload["script_id"])
    progress.title = "🎨 Озвучка и кадры"

    async def tick(done: float, total: float, note: str) -> None:
        await progress.update(done, total, note)

    result = await make_assets(script_id, tick)
    await progress.done(
        f"🎨 <b>{_name(script_id)}</b>\n\n"
        f"Кадров: {result['frames']}/{result['total']} · голос: {result['voice']}\n"
        f"Реальная длина: {result['duration']:.0f} сек",
        close_kb(
            [
                InlineKeyboardButton(
                    text="🎥 Собрать ролик", callback_data=f"reel:render:{script_id}"
                )
            ]
        ),
    )

    if progress.chat_id:
        preview = [b["frame_path"] for b in result["beats"] if b.get("frame_path")][:2]
        for index, path in enumerate(preview):
            await bot.send_photo(
                progress.chat_id,
                FSInputFile(path),
                reply_markup=close_kb() if index == len(preview) - 1 else None,
            )


@register("reel.render")
async def reel_render(payload: dict, progress: Progress, bot: Bot) -> None:
    script_id = int(payload["script_id"])
    name = _name(script_id)
    progress.title = "🎥 Монтирую ролик"

    async def tick(done: float, total: float, note: str) -> None:
        await progress.update(done, total, note)

    result = await make_video(script_id, tick)
    size_mb = result["size"] / 1024 / 1024

    await progress.done(
        f"🎥 <b>{name}</b>\n\n"
        f"{result['duration']:.0f} сек · {size_mb:.1f} МБ · кадров {result['clips']}\n"
        f"{'🎵 с музыкой' if result['music'] else '🔇 без музыки — положи трек в data/music/'}",
        close_kb(),
    )
    if not progress.chat_id:
        return

    await deliver.send_reel(
        bot, progress.chat_id, script_id, name, result,
        f"{result['duration']:.0f} сек · кадров {result['clips']}",
    )
