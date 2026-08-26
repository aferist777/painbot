"""Buttons that drive the breakdown: write, preview, publish."""
from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.db.base import q1
from app.jobs import queue

router = Router()


async def _enqueue(callback: CallbackQuery, kind: str, payload: dict, note: str) -> None:
    if not callback.message:
        return
    placeholder = await callback.message.answer(note)
    queue.enqueue(
        kind, payload, chat_id=placeholder.chat.id, message_id=placeholder.message_id
    )


@router.callback_query(F.data.startswith("art:make:"))
async def cb_make(callback: CallbackQuery) -> None:
    idea_id = int(callback.data.rsplit(":", 1)[1])
    existing = q1("SELECT id FROM articles WHERE idea_id=?", idea_id)
    if existing and q1("SELECT blocks_json FROM articles WHERE id=?", existing["id"])["blocks_json"]:
        await _enqueue(
            callback, "article.publish", {"article_id": existing["id"]}, "🚀 Публикую…"
        )
        await callback.answer("Разбор уже есть — публикую")
        return
    await _enqueue(callback, "article.run", {"idea_id": idea_id}, "📄 Пишу разбор…")
    await callback.answer("Пишу")


@router.callback_query(F.data.startswith("art:redo:"))
async def cb_redo(callback: CallbackQuery) -> None:
    idea_id = int(callback.data.rsplit(":", 1)[1])
    await _enqueue(callback, "article.run", {"idea_id": idea_id}, "📄 Переписываю разбор…")
    await callback.answer("Переписываю")


@router.callback_query(F.data.startswith("art:pub:"))
async def cb_publish(callback: CallbackQuery) -> None:
    article_id = int(callback.data.rsplit(":", 1)[1])
    await _enqueue(callback, "article.publish", {"article_id": article_id}, "🚀 Публикую…")
    await callback.answer("Публикую")
