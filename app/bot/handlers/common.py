"""Shared UI bits: dismissing a notification."""
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

router = Router()


@router.callback_query(F.data == "ui:close")
async def cb_close(callback: CallbackQuery) -> None:
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
