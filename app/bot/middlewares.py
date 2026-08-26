import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.db.repo import sdel, sget, sget_int, sset

log = logging.getLogger("painbot.auth")


class OwnerOnly(BaseMiddleware):
    """Single-user bot.

    Ownership is one row in settings, so handing the bot to someone else never
    touches the ideas. Telegram will not resolve a @username a bot has never
    talked to, so a handover is stored as an expected username and claimed by
    whoever shows up with it.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        owner_id = sget_int("owner_id")
        expected = (sget("owner_username") or "").lstrip("@").lower()

        if owner_id is None:
            username = (user.username or "").lower()
            if expected and username != expected:
                log.info("отклонён %s (%s): ждём @%s", user.id, username, expected)
                return await self._deny(event, f"Бот ждёт @{expected}.")
            sset("owner_id", user.id)
            sdel("owner_username")
            log.info("владелец назначен: %s (@%s)", user.id, user.username)
            owner_id = user.id

        if user.id != owner_id:
            return await self._deny(event, "Это личный бот.")

        return await handler(event, data)

    @staticmethod
    async def _deny(event: TelegramObject, text: str) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
        return None
