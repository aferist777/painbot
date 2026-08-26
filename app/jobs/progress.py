import asyncio
import time
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

BAR_CELLS = 14
MIN_EDIT_INTERVAL = 1.6  # Telegram punishes rapid edits of the same message
TICK_SECONDS = 5.0


def _bar(done: float, total: float) -> str:
    total = max(total, 1)
    filled = round(BAR_CELLS * min(done, total) / total)
    return "▓" * filled + "░" * (BAR_CELLS - filled)


def _clock(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Progress:
    """Edits one message in place: bar, counter, elapsed and ETA.

    A background ticker keeps the clock moving during long model calls, when no
    step boundary is reached for half a minute at a time.
    """

    def __init__(self, bot: Bot, chat_id: Optional[int], message_id: Optional[int], title: str):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.title = title
        self.started = time.monotonic()
        self._last_edit = 0.0
        self._last_text = ""
        self._done = 0.0
        self._total = 1.0
        self._note = ""
        self._ticker: Optional[asyncio.Task] = None

    @property
    def attached(self) -> bool:
        return bool(self.chat_id and self.message_id)

    # ------------------------------------------------------------------ ticker

    def start_ticker(self, interval: float = TICK_SECONDS) -> None:
        if self._ticker is not None or not self.attached:
            return
        self._ticker = asyncio.create_task(self._tick(interval))

    async def stop_ticker(self) -> None:
        task, self._ticker = self._ticker, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _tick(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            await self._render(force=True)

    # ------------------------------------------------------------------ render

    def _text(self) -> str:
        elapsed = time.monotonic() - self.started
        eta = ""
        if 0 < self._done < self._total:
            eta = f" · осталось ~{_clock(elapsed / self._done * (self._total - self._done))}"
        counter = f"{int(self._done)}/{int(self._total)}"
        text = (
            f"<b>{self.title}</b>\n\n"
            f"{_bar(self._done, self._total)}  {counter}\n"
            f"⏱ {_clock(elapsed)}{eta}"
        )
        if self._note:
            text += f"\n<i>{self._note}</i>"
        return text

    async def _render(self, force: bool = False) -> None:
        if not self.attached:
            return
        now = time.monotonic()
        if not force and now - self._last_edit < MIN_EDIT_INTERVAL:
            return
        text = self._text()
        if text == self._last_text:
            return
        try:
            await self.bot.edit_message_text(
                text, chat_id=self.chat_id, message_id=self.message_id
            )
            self._last_text = text
            self._last_edit = now
        except TelegramBadRequest:
            pass  # deleted, or identical content; not worth crashing over

    async def update(
        self, done: float, total: float, note: str = "", force: bool = False
    ) -> None:
        self._done, self._total, self._note = done, max(total, 1), note
        await self._render(force=force)

    # ------------------------------------------------------------------ finish

    async def done(
        self, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> None:
        await self.stop_ticker()
        if not self.attached:
            return
        try:
            await self.bot.edit_message_text(
                text, chat_id=self.chat_id, message_id=self.message_id,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest:
            pass

    async def delete(self) -> None:
        """Used when the result speaks for itself and the bar is just noise."""
        await self.stop_ticker()
        if not self.attached:
            return
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except TelegramBadRequest:
            pass
        self.message_id = None

    async def failed(self, error: str) -> None:
        from app.bot.keyboards import close_kb

        await self.done(
            f"<b>{self.title}</b>\n\n❌ Ошибка: <code>{error[:300]}</code>", close_kb()
        )
