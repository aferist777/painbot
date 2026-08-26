"""One button, whole cycle: breakdown, script, voice, frames, cut.

Each stage owns a slice of a single 0-100 bar, so the user sees one moving
line instead of five bars appearing and disappearing.
"""
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton

from app.bot.keyboards import close_kb
from app.db.base import q1, x
from app.jobs import deliver
from app.jobs.progress import Progress
from app.jobs.steps import make_article, make_assets, make_script, make_video
from app.jobs.worker import register
from app.media import post as post_builder

log = logging.getLogger("painbot.jobs.pipeline")

# (label, share of the bar) — measured roughly on a real run
STAGES = [
    ("Разбор", 30),
    ("Сценарий", 8),
    ("Озвучка и кадры", 37),
    ("Монтаж", 25),
]


class Bar:
    """Scales a stage's own counter into its slice of the overall bar."""

    def __init__(self, progress: Progress):
        self.progress = progress
        self.base = 0.0
        self.span = 1.0
        self.label = ""

    def stage(self, index: int) -> None:
        self.base = sum(share for _, share in STAGES[:index])
        self.label, self.span = STAGES[index][0], STAGES[index][1]
        self.progress.title = f"⚙️ {self.label} · шаг {index + 1}/{len(STAGES)}"

    async def tick(self, done: float, total: float, note: str) -> None:
        fraction = min(max(done / max(total, 1), 0.0), 1.0)
        await self.progress.update(self.base + self.span * fraction, 100, note)


@register("pipeline.run")
async def pipeline_run(payload: dict, progress: Progress, bot: Bot) -> None:
    idea_id = int(payload["idea_id"])
    idea = q1("SELECT name, pain_id FROM ideas WHERE id=?", idea_id)
    if idea is None:
        raise ValueError(f"идея #{idea_id} не найдена")
    name = idea["name"]

    bar = Bar(progress)
    progress.start_ticker(5)

    bar.stage(0)
    await progress.update(0, 100, "поехали", force=True)
    article_id, visuals_ok, visuals_total = await make_article(idea_id, bar.tick)

    bar.stage(1)
    script_id = await make_script(idea_id, bar.tick)

    bar.stage(2)
    assets = await make_assets(script_id, bar.tick)

    bar.stage(3)
    video = await make_video(script_id, bar.tick)

    await progress.delete()
    if not progress.chat_id:
        return

    # The breakdown first, then the reel: same order the channel will see them.
    preview = await bot.send_rich_message(
        chat_id=progress.chat_id,
        rich_message=post_builder.build(article_id),
        reply_markup=close_kb(
            [
                InlineKeyboardButton(
                    text="🚀 Опубликовать в канал", callback_data=f"art:pub:{article_id}"
                )
            ]
        ),
    )
    x(
        "UPDATE articles SET preview_chat_id=?, preview_msg_id=? WHERE id=?",
        preview.chat.id, preview.message_id, article_id,
    )

    note = (
        f"{video['duration']:.0f} сек · картинок в разборе "
        f"{visuals_ok}/{visuals_total} · кадров {assets['frames']}/{assets['total']}"
    )
    await deliver.send_reel(bot, progress.chat_id, script_id, name, video, note)
