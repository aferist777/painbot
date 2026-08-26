import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import config
from app.bot.keyboards import back_to_menu
from app.db.repo import sdel, sget, sget_int, sset

log = logging.getLogger("painbot.settings")
router = Router()


class Bind(StatesGroup):
    waiting_channel = State()


ASK_CHANNEL = (
    "<b>Привязка канала</b>\n\n"
    "Перешли сюда любой пост из канала — я возьму его id из пересылки.\n\n"
    "Если у канала скрыт источник пересылки, пришли <code>@username</code> "
    "или числовой id вида <code>-1001234567890</code>.\n\n"
    "Бот должен быть админом канала с правом публикации."
)


def _flag(ok: bool) -> str:
    return "✅" if ok else "—"


def _settings_text() -> str:
    channel_title = sget("channel_title")
    channel_id = sget("channel_id")
    channel = f"{channel_title} (<code>{channel_id}</code>)" if channel_id else "не привязан"
    return (
        "<b>Настройки</b>\n\n"
        f"📢 Канал: {channel}\n\n"
        "<b>Ключи</b>\n"
        f"{_flag(bool(config.TG_TOKEN))} Telegram\n"
        f"{_flag(bool(config.ANTHROPIC_API_KEY))} Anthropic\n"
        f"{_flag(bool(config.REPLICATE_API_TOKEN))} Replicate\n"
        f"{_flag(config.R2_READY)} Cloudflare R2"
        f"{' · публичный домен' if config.R2_PUBLIC_BASE else ' · presigned-ссылки' if config.R2_READY else ''}"
    )


def _voice_label() -> str:
    from app.media import tts

    return "🎙 Голос: " + ("ElevenLabs" if tts.provider() == "eleven" else "Edge TTS")


def _settings_kb():
    kb = InlineKeyboardBuilder()
    if sget("channel_id"):
        kb.row(
            InlineKeyboardButton(text="🔄 Сменить канал", callback_data="set:channel"),
            InlineKeyboardButton(text="🔌 Отвязать", callback_data="set:channel_off"),
        )
        kb.row(InlineKeyboardButton(text="📨 Тестовый пост", callback_data="set:channel_test"))
    else:
        kb.row(InlineKeyboardButton(text="📢 Привязать канал", callback_data="set:channel"))
    kb.row(InlineKeyboardButton(text=_voice_label(), callback_data="set:voice"))
    kb.row(
        InlineKeyboardButton(text="📡 Источники", callback_data="set:sources"),
        InlineKeyboardButton(text="🧪 Тест", callback_data="dbg:job"),
    )
    kb.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:open"))
    return kb.as_markup()


async def _show(target: Message | CallbackQuery) -> None:
    if isinstance(target, CallbackQuery) and target.message:
        await target.message.edit_text(_settings_text(), reply_markup=_settings_kb())
        await target.answer()
    elif isinstance(target, Message):
        await target.answer(_settings_text(), reply_markup=_settings_kb())


@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show(message)


@router.callback_query(F.data == "set:open")
async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(callback)


@router.callback_query(F.data == "set:channel")
async def cb_bind(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Bind.waiting_channel)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✖️ Отмена", callback_data="set:open"))
    if callback.message:
        await callback.message.edit_text(ASK_CHANNEL, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data == "set:channel_off")
async def cb_unbind(callback: CallbackQuery) -> None:
    sdel("channel_id")
    sdel("channel_title")
    if callback.message:
        await callback.message.edit_text(_settings_text(), reply_markup=_settings_kb())
    await callback.answer("Отвязан")


def _chat_from_forward(message: Message):
    """Bot API 7.0 moved forward info into forward_origin; support both shapes.

    Private channels and channels with protected content forward without an
    origin chat at all, which is why binding cannot rely on this alone.
    """
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None:
            return chat
    return getattr(message, "forward_from_chat", None)


def _target_from_text(text: str):
    """Accept @name, t.me/name, https://t.me/name or a numeric id."""
    value = (text or "").strip()
    if not value:
        return None
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    for prefix in ("t.me/", "telegram.me/", "telegram.dog/"):
        if value.startswith(prefix):
            value = value[len(prefix):].split("/")[0].split("?")[0]
            break
    if value.startswith("+") or value.startswith("joinchat"):
        return None  # invite link: no public handle behind it
    if value.lstrip("-").isdigit():
        return int(value)
    value = value.lstrip("@")
    if value and all(c.isalnum() or c == "_" for c in value):
        return "@" + value
    return None


async def bind_chat(bot: Bot, chat_id) -> tuple[bool, str]:
    """Verify the bot can post there, then remember the channel."""
    try:
        info = await bot.get_chat(chat_id)
        me = await bot.me()
        member = await bot.get_chat_member(info.id, me.id)
    except Exception as exc:
        return False, f"не смог прочитать канал: {str(exc)[:150]}"
    if member.status not in ("administrator", "creator"):
        return False, "бот в канале есть, но не администратор"
    sset("channel_id", info.id)
    sset("channel_title", info.title or info.full_name or str(info.id))
    return True, info.title or str(info.id)


@router.message(Bind.waiting_channel)
async def got_channel(message: Message, state: FSMContext, bot: Bot) -> None:
    chat = _chat_from_forward(message)
    log.info(
        "bind attempt: forward_origin=%s forward_chat=%s text=%r",
        type(getattr(message, "forward_origin", None)).__name__,
        getattr(chat, "id", None),
        (message.text or "")[:60],
    )
    target: str | int | None = chat.id if chat else None

    if target is None and message.text:
        target = _target_from_text(message.text)

    if target is None:
        await message.answer(
            "Не вижу источник пересылки. Пришли <code>@username</code> канала "
            "или его числовой id."
        )
        return

    ok, note = await bind_chat(bot, target)
    if not ok:
        await message.answer(
            f"Не вышло: {note}.\n\n"
            "Проще всего добавить бота в канал администратором — я привяжусь сам."
        )
        return

    await state.clear()
    await message.answer(f"✅ Канал привязан: <b>{note}</b>")
    await message.answer(_settings_text(), reply_markup=_settings_kb())


@router.my_chat_member()
async def on_membership_change(event: ChatMemberUpdated, bot: Bot) -> None:
    """The reliable path: binding happens the moment the bot is made admin."""
    if event.chat.type != "channel":
        return
    owner = sget_int("owner_id")
    status = event.new_chat_member.status

    if status in ("administrator", "creator"):
        ok, note = await bind_chat(bot, event.chat.id)
        log.info("added to channel %s (%s): %s", event.chat.id, status, note)
        if owner:
            text = (
                f"✅ Канал привязан: <b>{note}</b>"
                if ok
                else f"⚠️ Добавили в «{event.chat.title}», но {note}"
            )
            await bot.send_message(owner, text)
        return

    if str(sget("channel_id") or "") == str(event.chat.id):
        sdel("channel_id")
        sdel("channel_title")
        if owner:
            await bot.send_message(owner, f"🔌 Канал «{event.chat.title}» отвязан.")


@router.channel_post()
async def on_channel_post(message: Message, bot: Bot) -> None:
    """Fallback: any post in a channel where the bot sits binds it."""
    if sget("channel_id"):
        return
    ok, note = await bind_chat(bot, message.chat.id)
    owner = sget_int("owner_id")
    if ok and owner:
        await bot.send_message(owner, f"✅ Канал привязан: <b>{note}</b>")


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """The panel lives on this machine only, so the link is just the port."""
    from app.admin.server import URL

    await message.answer(
        f"🎛 <b>Админка</b>\n\n<code>{URL}</code>\n\n"
        "Открывается только на этом компьютере. Ключи там под звёздочками "
        "и показываются по клику.",
        reply_markup=back_to_menu(),
    )


@router.message(Command("owner"))
async def cmd_owner(message: Message) -> None:
    """Hand the bot to someone else without touching a single idea."""
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Передача бота: <code>/owner @username</code>\n\n"
            "Данные остаются на месте — меняется одна строка в настройках. "
            "Новый владелец жмёт /start, и бот привязывается к нему."
        )
        return
    target = parts[1].lstrip("@").strip()
    if not target or not all(c.isalnum() or c == "_" for c in target):
        await message.answer("Не похоже на юзернейм.")
        return
    sset("owner_username", target)
    sdel("owner_id")
    await message.answer(
        f"Бот отвязан от тебя и ждёт @{target}.\n\n"
        f"Пусть он напишет /start. До этого момента бот никого не слушает.\n"
        f"Все идеи, разборы и ролики на месте."
    )


@router.callback_query(F.data == "set:channel_test")
async def cb_test_post(callback: CallbackQuery, bot: Bot) -> None:
    channel_id = sget("channel_id")
    if not channel_id:
        await callback.answer("Канал не привязан", show_alert=True)
        return
    try:
        await bot.send_message(int(channel_id), "🤖 painbot на связи. Это тестовый пост.")
        await callback.answer("Отправлено")
    except Exception as exc:
        await callback.answer(f"Не вышло: {str(exc)[:150]}", show_alert=True)
