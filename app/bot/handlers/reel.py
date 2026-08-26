"""The reel script: beat list, voice-over text, rewrite."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards import close_kb
from app.db.base import q1
from app.jobs import queue
from app.llm.script import load, target_range

router = Router()

ICON = {
    "title": "🔤", "mockup": "🖥", "diagram": "🔀", "photo": "📷",
    "steps": "📋", "host_hello": "🙋", "host_warn": "☝️", "host_bye": "👋",
}


def card_text(script_id: int) -> str:
    script, beats = load(script_id)
    idea = q1("SELECT name FROM ideas WHERE id=?", script["idea_id"])
    duration = script["duration_est"] or 0

    warn = ""
    low, high = target_range()
    if duration < low:
        warn = "  ⚠️ коротковато"
    elif duration > high:
        warn = "  ⚠️ длинновато"

    task_no = script["task_no"] or "?"
    level = script["level"] or ""
    lines = [
        f"🎬 <b>Задание №{task_no}</b> · {level}",
        f"<i>{idea['name'] if idea else ''}</i> · ~{duration:.0f} сек · "
        f"{len(beats)} кадров{warn}",
        "",
        f"🪝 <b>{script['hook']}</b>",
        "",
    ]
    for beat in beats:
        icon = ICON.get(beat["visual_kind"], "•")
        vo = beat["vo"]
        if len(vo) > 64:
            vo = vo[:63] + "…"
        lines.append(f"<code>{beat['idx'] + 1:2}</code> {icon} {beat['seconds']:>4.1f}с  {vo}")
    return "\n".join(lines)


def card_kb(script_id: int, idea_id: int):
    from app.db.base import q1 as _q1

    ready = _q1(
        "SELECT COUNT(*) AS n FROM assets WHERE script_id=? AND status='done'", script_id
    )["n"]
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f"🎨 Кадры и озвучка ({ready})" if ready else "🎨 Собрать кадры и озвучку",
            callback_data=f"reel:assets:{script_id}",
        )
    )
    if ready:
        kb.row(
            InlineKeyboardButton(
                text="🎥 Собрать ролик", callback_data=f"reel:render:{script_id}"
            )
        )
    kb.row(
        InlineKeyboardButton(text="📝 Текст закадра", callback_data=f"reel:vo:{script_id}"),
        InlineKeyboardButton(text="🔁 Переписать", callback_data=f"reel:make:{idea_id}"),
    )
    kb.row(InlineKeyboardButton(text="◀️ К идее", callback_data="idea:list"))
    return kb.as_markup()


@router.callback_query(F.data.startswith("reel:make:"))
async def cb_make(callback: CallbackQuery) -> None:
    idea_id = int(callback.data.rsplit(":", 1)[1])
    if callback.message:
        placeholder = await callback.message.answer("🎬 Пишу сценарий…")
        queue.enqueue(
            "script.run",
            {"idea_id": idea_id},
            chat_id=placeholder.chat.id,
            message_id=placeholder.message_id,
        )
    await callback.answer("Пишу")


@router.callback_query(F.data.startswith("reel:assets:"))
async def cb_assets(callback: CallbackQuery) -> None:
    script_id = int(callback.data.rsplit(":", 1)[1])
    if callback.message:
        placeholder = await callback.message.answer("🎨 Озвучка и кадры…")
        queue.enqueue(
            "reel.assets",
            {"script_id": script_id},
            chat_id=placeholder.chat.id,
            message_id=placeholder.message_id,
        )
    await callback.answer("Собираю")


@router.callback_query(F.data.startswith("reel:render:"))
async def cb_render(callback: CallbackQuery) -> None:
    script_id = int(callback.data.rsplit(":", 1)[1])
    if callback.message:
        placeholder = await callback.message.answer("🎥 Монтирую ролик…")
        queue.enqueue(
            "reel.render",
            {"script_id": script_id},
            chat_id=placeholder.chat.id,
            message_id=placeholder.message_id,
        )
    await callback.answer("Монтирую")


@router.callback_query(F.data.startswith("social:redo:"))
async def cb_social_redo(callback: CallbackQuery) -> None:
    """The texts are cheap to redo; the video they hang on is not touched."""
    script_id = int(callback.data.rsplit(":", 1)[1])
    if callback.message:
        placeholder = await callback.message.answer("🔄 Переписываю тексты…")
        queue.enqueue(
            "social.run",
            {"script_id": script_id, "threads_msg_id": callback.message.message_id},
            chat_id=placeholder.chat.id,
            message_id=placeholder.message_id,
        )
    await callback.answer("Переписываю")


@router.callback_query(F.data.startswith("reel:show:"))
async def cb_show(callback: CallbackQuery) -> None:
    script_id = int(callback.data.rsplit(":", 1)[1])
    script, _ = load(script_id)
    if callback.message:
        await callback.message.edit_text(
            card_text(script_id), reply_markup=card_kb(script_id, script["idea_id"])
        )
    await callback.answer()


@router.callback_query(F.data.startswith("reel:vo:"))
async def cb_vo(callback: CallbackQuery) -> None:
    script_id = int(callback.data.rsplit(":", 1)[1])
    script, beats = load(script_id)
    body = "\n\n".join(f"{beat['idx'] + 1}. {beat['vo']}" for beat in beats)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"📝 <b>Закадровый текст</b>\n\n{body}",
            reply_markup=close_kb(),
        )
