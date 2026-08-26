from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu(inbox_count: int = 0, approved_count: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=f"📥 Инбокс {inbox_count}", callback_data="pain:inbox"),
        InlineKeyboardButton(text=f"✅ Одобренные {approved_count}", callback_data="idea:list"),
    )
    kb.row(InlineKeyboardButton(text="📊 Стата", callback_data="stats:show"))
    kb.row(
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="set:open"),
        InlineKeyboardButton(text="💸 Расходы", callback_data="stats:costs"),
    )
    return kb.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"))
    return kb.as_markup()


def close_kb(extra: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    """Notifications stay until dismissed: the menu is one tap away anyway."""
    kb = InlineKeyboardBuilder()
    for button in extra or []:
        kb.row(button)
    kb.row(InlineKeyboardButton(text="✖️ Закрыть", callback_data="ui:close"))
    return kb.as_markup()
