from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu
from app.db.base import q1

router = Router()

GREETING = (
    "<b>painbot</b> — лента болей → идеи → разбор → рилс\n\n"
    "Сбор идёт по расписанию, скрининг отсеивает не-IT и шум. "
    "Ты только жмёшь ✅ или ❌."
)


def _counts() -> tuple[int, int]:
    inbox = q1("SELECT COUNT(*) AS n FROM pains WHERE state='inbox'")
    approved = q1("SELECT COUNT(*) AS n FROM pains WHERE state='approved'")
    return (inbox["n"] if inbox else 0), (approved["n"] if approved else 0)


async def show_menu(target: Message | CallbackQuery) -> None:
    inbox, approved = _counts()
    kb = main_menu(inbox, approved)
    if isinstance(target, CallbackQuery) and target.message:
        await target.message.edit_text(GREETING, reply_markup=kb)
        await target.answer()
    elif isinstance(target, Message):
        await target.answer(GREETING, reply_markup=kb)


@router.message(Command("start", "menu"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_menu(message)


@router.callback_query(F.data == "menu:open")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_menu(callback)
