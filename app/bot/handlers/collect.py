"""Manual collection trigger and source overview."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.collect.seed import seed_sources
from app.db.base import q
from app.jobs import queue

router = Router()


async def _start_collect(message: Message, kinds: list[str] | None = None) -> None:
    seed_sources()
    placeholder = await message.answer("📡 Сбор болей\n\nставлю в очередь…")
    queue.enqueue(
        "collect.run",
        {"kinds": kinds} if kinds else {},
        chat_id=placeholder.chat.id,
        message_id=placeholder.message_id,
    )


@router.callback_query(F.data == "collect:now")
async def cb_collect(callback: CallbackQuery) -> None:
    if callback.message:
        await _start_collect(callback.message)
    await callback.answer("Запустил")


@router.callback_query(F.data == "collect:vintage")
async def cb_collect_vintage(callback: CallbackQuery) -> None:
    if callback.message:
        await _start_collect(callback.message, kinds=["hn_vintage"])
    await callback.answer("Копаю архивы")


@router.message(Command("collect"))
async def cmd_collect(message: Message) -> None:
    await _start_collect(message)


@router.callback_query(F.data == "set:sources")
async def cb_sources(callback: CallbackQuery) -> None:
    seed_sources()
    rows = q(
        "SELECT kind, COUNT(*) AS n, SUM(enabled) AS on_count, "
        "SUM(COALESCE(stored_total,0)) AS stored, SUM(COALESCE(pains_total,0)) AS pains "
        "FROM sources GROUP BY kind ORDER BY MIN(tier), kind"
    )
    lines = ["📡 <b>Источники</b>", ""]
    for row in rows:
        lines.append(
            f"<b>{row['kind']}</b> — {row['on_count']}/{row['n']} вкл · "
            f"собрано {row['stored'] or 0} · болей {row['pains'] or 0}"
        )

    top = q(
        "SELECT name, kind, COALESCE(pains_total,0) AS pains, "
        "COALESCE(stored_total,0) AS stored FROM sources "
        "WHERE COALESCE(stored_total,0) > 0 ORDER BY pains DESC, stored DESC LIMIT 8"
    )
    if top:
        lines.append("")
        lines.append("<b>Кто приносит боли</b>")
        for index, row in enumerate(top, start=1):
            hit = (row["pains"] / row["stored"] * 100) if row["stored"] else 0
            lines.append(
                f"<code>{index:2}</code> {row['name'][:22]:22} "
                f"{row['pains']:>3} из {row['stored']:>4} ({hit:.0f}%)"
            )
        lines.append("")
        lines.append("<i>Кто выше — того и парсим первым.</i>")

    failed = q(
        "SELECT kind, name, last_error FROM sources "
        "WHERE last_error IS NOT NULL ORDER BY name LIMIT 6"
    )
    if failed:
        lines.append("")
        lines.append("⚠️ <b>С ошибками</b>")
        for row in failed:
            lines.append(f"{row['name']}: <code>{row['last_error'][:60]}</code>")

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📡 Собрать всё", callback_data="collect:now"),
        InlineKeyboardButton(text="🕰 Только архивы", callback_data="collect:vintage"),
    )
    kb.row(InlineKeyboardButton(text="◀️ Настройки", callback_data="set:open"))
    if callback.message:
        await callback.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    await callback.answer()
