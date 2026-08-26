"""The screen you live in: one pain at a time, approve or skip."""
import json
import time
from typing import Any, Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.base import q, q1, x
from app.db.repo import now
from app.jobs import queue

router = Router()

SEV = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}


def _ago(ts: Optional[int]) -> str:
    if not ts:
        return "давно"
    delta = max(int(time.time()) - int(ts), 0)
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    days = delta // 86400
    if days < 30:
        return f"{days} дн назад"
    years = days // 365
    return f"{years} г назад" if years else f"{days // 30} мес назад"


def _fetch_pain(pain_id: Optional[int] = None) -> Optional[Any]:
    sql = (
        "SELECT p.*, r.url, r.score AS raw_score, r.comments, r.created_utc, "
        "s.kind AS source_kind, s.name AS source_name "
        "FROM pains p "
        "JOIN raw_items r ON r.id = p.raw_item_id "
        "JOIN sources s ON s.id = r.source_id "
    )
    if pain_id is not None:
        return q1(sql + "WHERE p.id=?", pain_id)
    return q1(sql + "WHERE p.state='inbox' ORDER BY p.score DESC, p.id ASC LIMIT 1")


def _card(row: Any) -> str:
    tags = ""
    try:
        parsed = json.loads(row["tags_json"] or "[]")
        if parsed:
            tags = " · ".join("#" + t for t in parsed)
    except json.JSONDecodeError:
        pass

    era_mark = "🕰 " if row["era"] == "vintage" else ""
    source = row["source_kind"] + "/" + row["source_name"]
    kind = row["kind"] or "pain"
    is_request = kind in ("request", "idea")
    head = {
        "request": "🙋 <b>Запрос</b>",
        "idea": "💡 <b>Идея</b>",
    }.get(kind, "🔥 <b>Боль</b>")

    lines = [
        f"{head} #{row['id']} · score {row['score']} · {era_mark}{source}",
        "",
        f"<b>{row['title_ru']}</b>",
    ]
    if row["summary"]:
        lines.append(row["summary"])
    if row["evidence_quote"]:
        lines.append(f"\n💬 <i>«{row['evidence_quote']}»</i>")
    if row["audience"]:
        lines.append(f"👤 {row['audience']}")
    if row["why_now"]:
        lines.append(f"\n⚡ <b>Почему сейчас:</b> {row['why_now']}")

    lines.append(
        f"\n💥 боль {SEV[row['severity']]}/5 · 💰 платёж {SEV[row['willingness_to_pay']]}/5"
        f" · 🔨 соло {SEV[row['solo_feasibility']]}/5 · 🌊 рынок {SEV[row['saturation']]}/5"
    )
    lines.append(
        f"{row['raw_score']} ↑ · {row['comments']} 💬 · {_ago(row['created_utc'])}"
    )
    if tags:
        lines.append(tags)
    return "\n".join(lines)


def _card_kb(row: Any):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ В базу", callback_data=f"pain:ok:{row['id']}"),
        InlineKeyboardButton(text="❌ Скип", callback_data=f"pain:no:{row['id']}"),
    )
    kb.row(
        InlineKeyboardButton(text="🔗 Источник", url=row["url"] or "https://news.ycombinator.com"),
        InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"),
    )
    kb.row(InlineKeyboardButton(text="🧹 Очистить и собрать заново", callback_data="pain:clear"))
    return kb.as_markup()


EMPTY = (
    "📭 <b>Инбокс пуст</b>\n\n"
    "Запусти сбор в настройках или дождись ночного прогона."
)


def _empty_kb():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📡 Собрать сейчас", callback_data="collect:now"))
    kb.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"))
    return kb.as_markup()


@router.callback_query(F.data == "pain:clear")
async def cb_clear_ask(callback: CallbackQuery) -> None:
    left = q1("SELECT COUNT(*) AS n FROM pains WHERE state='inbox'")["n"]
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, очистить", callback_data="pain:clear:yes"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="pain:next"),
    )
    if callback.message:
        await callback.message.edit_text(
            "🧹 <b>Очистить инбокс?</b>\n\n"
            f"Уйдут в отсев {left} болей — обратно они не вернутся.\n"
            "Сразу после этого запущу сбор заново.",
            reply_markup=kb.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data == "pain:clear:yes")
async def cb_clear_do(callback: CallbackQuery) -> None:
    # the raw items are marked too, so the same batch does not come back
    x(
        "UPDATE raw_items SET state='rejected', reject_reason='cleared' "
        "WHERE id IN (SELECT raw_item_id FROM pains WHERE state='inbox')"
    )
    x("DELETE FROM pains WHERE state='inbox'")
    await callback.answer("Инбокс очищен")
    if callback.message:
        placeholder = await callback.message.answer("📡 Сбор болей\n\nставлю в очередь…")
        queue.enqueue(
            "collect.run", {},
            chat_id=placeholder.chat.id, message_id=placeholder.message_id,
        )
        await callback.message.delete()


async def _render_next(message: Message) -> None:
    row = _fetch_pain()
    if row is None:
        await message.edit_text(EMPTY, reply_markup=_empty_kb())
        return
    await message.edit_text(_card(row), reply_markup=_card_kb(row), disable_web_page_preview=True)


@router.callback_query(F.data.in_({"pain:next", "pain:inbox"}))
async def cb_next(callback: CallbackQuery) -> None:
    if callback.message:
        await _render_next(callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("pain:ok:"))
async def cb_approve(callback: CallbackQuery) -> None:
    pain_id = int(callback.data.rsplit(":", 1)[1])
    x("UPDATE pains SET state='approved' WHERE id=?", pain_id)
    # Fire and forget: the approve/skip rhythm must not wait on a model call.
    queue.enqueue("ideate.silent", {"pain_id": pain_id})
    await callback.answer("✅ В базе, думаю над решением")
    if callback.message:
        await _render_next(callback.message)


@router.callback_query(F.data.startswith("pain:no:"))
async def cb_reject(callback: CallbackQuery) -> None:
    pain_id = int(callback.data.rsplit(":", 1)[1])
    x("UPDATE pains SET state='rejected' WHERE id=?", pain_id)
    await callback.answer("Скип")
    if callback.message:
        await _render_next(callback.message)


@router.callback_query(F.data.in_({"stats:show", "stats:costs"}))
async def cb_stats(callback: CallbackQuery) -> None:
    raw = q1("SELECT COUNT(*) AS n FROM raw_items")
    new = q1("SELECT COUNT(*) AS n FROM raw_items WHERE state='new'")
    dup = q1("SELECT COUNT(*) AS n FROM raw_items WHERE state='duplicate'")
    inbox = q1("SELECT COUNT(*) AS n FROM pains WHERE state='inbox'")
    approved = q1("SELECT COUNT(*) AS n FROM pains WHERE state='approved'")
    vintage = q1("SELECT COUNT(*) AS n FROM pains WHERE era='vintage'")
    requests = q1("SELECT COUNT(*) AS n FROM pains WHERE kind='request'")
    ideas_raw = q1("SELECT COUNT(*) AS n FROM pains WHERE kind='idea'")
    spend = q1("SELECT COALESCE(SUM(usd), 0) AS usd, COUNT(*) AS calls FROM costs")
    day = q1(
        "SELECT COALESCE(SUM(usd), 0) AS usd FROM costs WHERE created_at > ?",
        now() - 86400,
    )
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Собрано постов: {raw['n']}\n"
        f"Ждут скрининга: {new['n']} · дублей: {dup['n']}\n\n"
        f"📥 Инбокс: {inbox['n']}\n"
        f"✅ Одобрено: {approved['n']}\n"
        f"🕰 Из архивов: {vintage['n']}\n"
        f"🙋 Прямых запросов: {requests['n']}\n"
        f"💡 Чужих замыслов: {ideas_raw['n']}\n\n"
        f"💸 Потрачено всего: ${spend['usd']:.2f} за {spend['calls']} вызовов\n"
        f"💸 За сутки: ${day['usd']:.2f}"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"))
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()
