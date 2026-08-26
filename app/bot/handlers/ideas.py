"""Approved topics and the product specs generated for them."""
import json
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.base import q, q1, x
from app.jobs import queue

router = Router()

STAGES = [
    ("idea", "💡"),
    ("article", "📄"),
    ("published", "🚀"),
    ("script", "🎬"),
    ("assets", "🎨"),
    ("video", "🎥"),
]
LEGEND = "💡 идея · 📄 разбор · 🚀 канал · 🎬 сценарий · 🎨 кадры · 🎥 ролик"


def _ideas_of(pain_id: int) -> list:
    return list(q("SELECT * FROM ideas WHERE pain_id=? ORDER BY variant_no, id", pain_id))


def _stages_of(pain_id: int) -> tuple[dict, str]:
    """How far through the cycle this topic has already gone."""
    idea = q1(
        "SELECT id, name FROM ideas WHERE pain_id=? ORDER BY variant_no LIMIT 1", pain_id
    )
    state = {key: False for key, _ in STAGES}
    title = ""
    if idea:
        state["idea"] = True
        title = idea["name"]
        article = q1(
            "SELECT blocks_json, channel_msg_id FROM articles WHERE idea_id=?", idea["id"]
        )
        state["article"] = bool(article and article["blocks_json"])
        state["published"] = bool(article and article["channel_msg_id"])
        script = q1("SELECT id FROM scripts WHERE idea_id=?", idea["id"])
        if script:
            state["script"] = True
            state["assets"] = bool(
                q1(
                    "SELECT 1 AS ok FROM assets WHERE script_id=? AND status='done' LIMIT 1",
                    script["id"],
                )
            )
            state["video"] = bool(
                q1(
                    "SELECT 1 AS ok FROM renders WHERE script_id=? AND status='done' LIMIT 1",
                    script["id"],
                )
            )
    return state, title


def _strip(state: dict) -> str:
    return "".join(icon if state[key] else "·" for key, icon in STAGES)


PAGE_SIZE = 10


def _list_text_kb(page: int = 1):
    rows = q(
        "SELECT id, title_ru, era, kind FROM pains WHERE state='approved' "
        "ORDER BY score DESC"
    )
    kb = InlineKeyboardBuilder()
    if not rows:
        kb.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"))
        return "✅ <b>Одобренные</b>\n\nПока пусто. Одобряй боли в ленте.", kb.as_markup()

    pages = max((len(rows) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = max(1, min(page, pages))
    first = (page - 1) * PAGE_SIZE
    chunk = rows[first : first + PAGE_SIZE]

    head = f"✅ <b>Одобренные · {len(rows)}</b>"
    if pages > 1:
        head += f"   <i>стр. {page}/{pages}</i>"
    lines = [head, ""]
    buttons = []
    for offset, row in enumerate(chunk):
        number = first + offset + 1
        state, name = _stages_of(row["id"])
        mark = {"request": "🙋", "idea": "💡"}.get(row["kind"] or "pain", "")
        era = "🕰" if row["era"] == "vintage" else ""
        label = name or row["title_ru"]
        lines.append(
            f"<code>{number:2}</code> {_strip(state)}  {mark}{era}{label[:40]}"
        )
        buttons.append(
            InlineKeyboardButton(
                text=str(number), callback_data=f"idea:show:{row['id']}:1"
            )
        )
    lines.append("")
    lines.append(f"<i>{LEGEND}</i>")

    for start_at in range(0, len(buttons), 5):
        kb.row(*buttons[start_at : start_at + 5])
    kb.row(InlineKeyboardButton(text="🧹 Очистить всё", callback_data="idea:clear"))
    if pages > 1:
        kb.row(
            InlineKeyboardButton(
                text="◀", callback_data=f"idea:list:{page - 1 if page > 1 else pages}"
            ),
            InlineKeyboardButton(text=f"{page}/{pages}", callback_data="noop"),
            InlineKeyboardButton(
                text="▶", callback_data=f"idea:list:{page + 1 if page < pages else 1}"
            ),
        )
    kb.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"))
    return "\n".join(lines), kb.as_markup()


def _card(pain: Any, idea: Any, index: int, total: int) -> str:
    stack = ", ".join(json.loads(idea["stack_json"] or "[]"))
    integrations = ", ".join(json.loads(idea["integrations_json"] or "[]"))

    lines = [
        f"💡 <b>{idea['name']}</b>  <i>{index}/{total}</i>",
        idea["one_liner"] or "",
        "",
        f"🔥 <i>Боль #{pain['id']}: {pain['title_ru']}</i>",
    ]
    if pain["why_now"]:
        lines.append(f"⚡ {pain['why_now']}")
    lines.append("")
    if idea["mvp_scope"]:
        lines.append("<b>Что в MVP</b>\n" + idea["mvp_scope"])
    if stack:
        lines.append(f"\n🧱 <b>Стек:</b> {stack}")
    if integrations:
        lines.append(f"🔌 <b>Интеграции:</b> {integrations}")
    if idea["db_sketch"]:
        lines.append(f"\n🗄 <b>База</b>\n<code>{idea['db_sketch']}</code>")
    if idea["effort_hours"]:
        lines.append(f"\n⏱ <b>MVP:</b> ~{idea['effort_hours']} ч")
    if idea["cut_list"]:
        lines.append(f"✂️ <b>Режем:</b> {idea['cut_list']}")
    if idea["moat_note"]:
        lines.append(f"🛡 <b>Защита:</b> {idea['moat_note']}")
    return "\n".join(part for part in lines if part is not None)


def _card_kb(pain_id: int, idea_id: int, index: int, total: int, state: dict, reel_data: str):
    kb = InlineKeyboardBuilder()
    nav = []
    if index > 1:
        nav.append(
            InlineKeyboardButton(text="◀", callback_data=f"idea:show:{pain_id}:{index-1}")
        )
    nav.append(
        InlineKeyboardButton(text="🔁 Ещё вариант", callback_data=f"idea:more:{pain_id}")
    )
    if index < total:
        nav.append(
            InlineKeyboardButton(text="▶", callback_data=f"idea:show:{pain_id}:{index+1}")
        )
    kb.row(*nav)
    kb.row(
        InlineKeyboardButton(
            text="⚡ Весь цикл: разбор + ролик", callback_data=f"pipe:run:{idea_id}"
        )
    )
    if state["published"]:
        article_label = "✅ Опубликовано · ещё раз"
    elif state["article"]:
        article_label = "🚀 Опубликовать разбор"
    else:
        article_label = "📄 Написать разбор"
    kb.row(InlineKeyboardButton(text=article_label, callback_data=f"art:make:{idea_id}"))
    kb.row(
        InlineKeyboardButton(
            text="🎬 Сценарий" if state["script"] else "🎬 Сценарий рилса",
            callback_data=reel_data,
        )
    )
    kb.row(
        InlineKeyboardButton(text="🔗 Источник", callback_data=f"idea:src:{pain_id}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"idea:del:{idea_id}"),
    )
    kb.row(InlineKeyboardButton(text="◀️ К списку", callback_data="idea:list"))
    return kb.as_markup()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("idea:list"))
async def cb_list(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    text, kb = _list_text_kb(page)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("idea:show:"))
async def cb_show(callback: CallbackQuery) -> None:
    _, _, raw_pain, raw_index = callback.data.split(":")
    pain_id, index = int(raw_pain), int(raw_index)
    pain = q1("SELECT * FROM pains WHERE id=?", pain_id)
    ideas = _ideas_of(pain_id)

    if pain is None:
        await callback.answer("Боль пропала", show_alert=True)
        return

    if not ideas:
        kb = InlineKeyboardBuilder()
        kb.row(
            InlineKeyboardButton(text="💡 Придумать", callback_data=f"idea:more:{pain_id}")
        )
        kb.row(InlineKeyboardButton(text="◀️ К списку", callback_data="idea:list"))
        if callback.message:
            await callback.message.edit_text(
                f"🔥 <b>{pain['title_ru']}</b>\n\n{pain['summary'] or ''}\n\n"
                f"<i>Идеи ещё не готовы.</i>",
                reply_markup=kb.as_markup(),
            )
        await callback.answer()
        return

    index = max(1, min(index, len(ideas)))
    idea_id = ideas[index - 1]["id"]
    state, _ = _stages_of(pain_id)
    script = q1("SELECT id FROM scripts WHERE idea_id=?", idea_id)
    reel_data = f"reel:show:{script['id']}" if script else f"reel:make:{idea_id}"

    if callback.message:
        await callback.message.edit_text(
            _card(pain, ideas[index - 1], index, len(ideas)),
            reply_markup=_card_kb(pain_id, idea_id, index, len(ideas), state, reel_data),
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("pipe:run:"))
async def cb_pipeline(callback: CallbackQuery) -> None:
    idea_id = int(callback.data.rsplit(":", 1)[1])
    if callback.message:
        placeholder = await callback.message.answer("⚙️ Полный цикл\n\nставлю в очередь…")
        queue.enqueue(
            "pipeline.run",
            {"idea_id": idea_id},
            chat_id=placeholder.chat.id,
            message_id=placeholder.message_id,
        )
    await callback.answer("Запустил весь цикл")


@router.callback_query(F.data.startswith("idea:more:"))
async def cb_more(callback: CallbackQuery) -> None:
    pain_id = int(callback.data.rsplit(":", 1)[1])
    has_any = bool(_ideas_of(pain_id))
    if callback.message:
        placeholder = await callback.message.answer("💡 Придумываю решение\n\nв очередь…")
        queue.enqueue(
            "ideate.run",
            {"pain_id": pain_id, "extra": has_any},
            chat_id=placeholder.chat.id,
            message_id=placeholder.message_id,
        )
    await callback.answer("Думаю")


@router.callback_query(F.data.startswith("idea:src:"))
async def cb_source(callback: CallbackQuery) -> None:
    from app.bot.keyboards import close_kb

    pain_id = int(callback.data.rsplit(":", 1)[1])
    row = q1(
        "SELECT r.url, p.evidence_quote FROM pains p "
        "JOIN raw_items r ON r.id = p.raw_item_id WHERE p.id=?",
        pain_id,
    )
    if row is None:
        await callback.answer("Нет источника", show_alert=True)
        return
    quote = f"«{row['evidence_quote']}»\n\n" if row["evidence_quote"] else ""
    await callback.answer()
    if callback.message:
        await callback.message.answer(f"{quote}{row['url']}", reply_markup=close_kb())


@router.callback_query(F.data == "idea:clear")
async def cb_clear_ask(callback: CallbackQuery) -> None:
    left = q1("SELECT COUNT(*) AS n FROM pains WHERE state='approved'")["n"]
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, очистить всё", callback_data="idea:clear:yes"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="idea:list"),
    )
    if callback.message:
        await callback.message.edit_text(
            "🧹 <b>Очистить одобренные?</b>\n\n"
            f"Удалю {left} тем со всем, что к ним привязано: идеи, разборы, "
            f"сценарии и записи о роликах.\n\n"
            "<i>Уже опубликованные посты в канале останутся, файлы на диске тоже.</i>",
            reply_markup=kb.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data == "idea:clear:yes")
async def cb_clear_do(callback: CallbackQuery) -> None:
    # foreign keys cascade: ideas, articles, scripts, assets and renders go too
    x("DELETE FROM pains WHERE state='approved'")
    text, kb = _list_text_kb()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("Очищено")


@router.callback_query(F.data.startswith("idea:del:"))
async def cb_delete_ask(callback: CallbackQuery) -> None:
    idea_id = int(callback.data.rsplit(":", 1)[1])
    idea = q1("SELECT name, pain_id FROM ideas WHERE id=?", idea_id)
    if idea is None:
        await callback.answer("Уже удалено", show_alert=True)
        return
    siblings = len(_ideas_of(idea["pain_id"]))
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"idea:delyes:{idea_id}"),
        InlineKeyboardButton(
            text="✖️ Отмена", callback_data=f"idea:show:{idea['pain_id']}:1"
        ),
    )
    note = (
        "Это последний вариант — тема тоже уйдёт из одобренных."
        if siblings <= 1
        else f"Останется вариантов: {siblings - 1}."
    )
    if callback.message:
        await callback.message.edit_text(
            f"🗑 <b>Удалить «{idea['name']}»?</b>\n\n{note}\n"
            "Вместе с ним удалятся его разбор и сценарий.",
            reply_markup=kb.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("idea:delyes:"))
async def cb_delete_do(callback: CallbackQuery) -> None:
    idea_id = int(callback.data.rsplit(":", 1)[1])
    idea = q1("SELECT pain_id FROM ideas WHERE id=?", idea_id)
    pain_id = idea["pain_id"] if idea else None
    x("DELETE FROM ideas WHERE id=?", idea_id)
    if pain_id and not _ideas_of(pain_id):
        x("UPDATE pains SET state='rejected' WHERE id=?", pain_id)
    await callback.answer("Удалено")
    text, kb = _list_text_kb()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)
