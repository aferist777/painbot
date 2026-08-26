import asyncio

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from app.jobs import queue
from app.jobs.progress import Progress
from app.jobs.worker import register

router = Router()


@register("debug.demo")
async def demo_job(payload: dict, progress: Progress, bot: Bot) -> None:
    """Smoke test for the whole queue → progress → result path."""
    total = int(payload.get("steps", 6))
    progress.title = "🧪 Тестовая задача"
    for i in range(1, total + 1):
        await asyncio.sleep(1.2)
        await progress.update(i, total, note=f"шаг {i}", force=(i == total))
    await progress.done("🧪 Тестовая задача\n\n✅ Очередь, воркер и прогресс работают.")


@router.callback_query(F.data == "dbg:job")
async def cb_demo(callback: CallbackQuery) -> None:
    if not callback.message:
        return
    msg = await callback.message.answer("🧪 Тестовая задача\n\nставлю в очередь…")
    queue.enqueue("debug.demo", {"steps": 6}, chat_id=msg.chat.id, message_id=msg.message_id)
    await callback.answer("В очереди")
