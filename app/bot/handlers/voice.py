"""Voice settings: provider, ElevenLabs key, voice id, and a listen test."""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards import close_kb
from app.config import MEDIA_DIR
from app.db.repo import sdel, sget, sset
from app.media import tts

log = logging.getLogger("painbot.voice")
router = Router()

SAMPLE = (
    "Восемь часов даунтайма, потому что снапшот базы никто ни разу не поднимал."
)
EDGE_NAMES = {
    "ru-RU-DmitryNeural": "Дмитрий",
    "ru-RU-SvetlanaNeural": "Светлана",
}


class Ask(StatesGroup):
    key = State()
    voice = State()


def _text() -> str:
    current = tts.provider()
    key = tts.eleven_key()
    masked = f"{key[:4]}…{key[-4:]}" if len(key) > 10 else ("задан" if key else "нет")
    edge = EDGE_NAMES.get(tts.voice_id() if current == "edge" else "", "")

    lines = ["🎙 <b>Голос</b>", ""]
    lines.append(
        f"Сейчас: <b>{'ElevenLabs' if current == 'eleven' else 'Edge TTS'}</b>"
    )
    if current == "edge":
        voice = sget("edge_voice") or "ru-RU-DmitryNeural"
        lines.append(f"Голос: {EDGE_NAMES.get(voice, voice)} · скорость {tts.rate()}")
        lines.append("\n<i>Бесплатно, без ключа. Читает по-русски прилично.</i>")
    else:
        lines.append(f"Voice ID: <code>{sget('eleven_voice') or 'не задан'}</code>")
        lines.append(f"Модель: <code>{tts.eleven_model()}</code>")
    lines.append("")
    lines.append(f"🔑 Ключ ElevenLabs: <code>{masked}</code>")
    return "\n".join(lines)


def _kb():
    current = tts.provider()
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔄 Переключить на " + ("Edge" if current == "eleven" else "ElevenLabs"),
            callback_data="voice:swap",
        )
    )
    kb.row(
        InlineKeyboardButton(text="🔑 Ввести ключ", callback_data="voice:key"),
        InlineKeyboardButton(text="🗣 Voice ID", callback_data="voice:id"),
    )
    if tts.eleven_key():
        kb.row(InlineKeyboardButton(text="📋 Список голосов", callback_data="voice:list"))
    if current == "edge":
        kb.row(InlineKeyboardButton(text="👤 Сменить диктора", callback_data="voice:edge"))
    kb.row(InlineKeyboardButton(text="🔊 Послушать пробу", callback_data="voice:test"))
    kb.row(InlineKeyboardButton(text="◀️ Настройки", callback_data="set:open"))
    return kb.as_markup()


async def _show(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text(_text(), reply_markup=_kb())


@router.callback_query(F.data == "set:voice")
async def cb_open(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(callback)
    await callback.answer()


@router.callback_query(F.data == "voice:swap")
async def cb_swap(callback: CallbackQuery) -> None:
    nxt = "edge" if tts.provider() == "eleven" else "eleven"
    if nxt == "eleven" and not tts.eleven_key():
        await callback.answer("Сначала введи ключ ElevenLabs", show_alert=True)
        return
    sset("tts_provider", nxt)
    await _show(callback)
    await callback.answer("Переключено")


@router.callback_query(F.data == "voice:edge")
async def cb_edge(callback: CallbackQuery) -> None:
    current = sget("edge_voice") or "ru-RU-DmitryNeural"
    nxt = (
        "ru-RU-SvetlanaNeural"
        if current == "ru-RU-DmitryNeural"
        else "ru-RU-DmitryNeural"
    )
    sset("edge_voice", nxt)
    await _show(callback)
    await callback.answer(EDGE_NAMES[nxt])


ASK_KEY = (
    "🔑 <b>Ключ ElevenLabs</b>\n\n"
    "Пришли ключ одним сообщением. Я проверю его на живом запросе, сохраню в базу "
    "и <b>сразу удалю твоё сообщение</b>, чтобы ключ не остался в переписке.\n\n"
    "Взять можно в профиле ElevenLabs → API Keys."
)
ASK_VOICE = (
    "🗣 <b>Voice ID</b>\n\n"
    "Пришли идентификатор голоса — это строка вроде "
    "<code>21m00Tcm4TlvDq8ikWAM</code> со страницы голоса в ElevenLabs.\n\n"
    "Или нажми «Список голосов», чтобы выбрать из своих."
)


def _cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✖️ Отмена", callback_data="set:voice"))
    return kb.as_markup()


@router.callback_query(F.data == "voice:key")
async def cb_ask_key(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Ask.key)
    if callback.message:
        await callback.message.edit_text(ASK_KEY, reply_markup=_cancel_kb())
    await callback.answer()


@router.message(Ask.key)
async def got_key(message: Message, state: FSMContext) -> None:
    key = (message.text or "").strip()
    # The key must not linger in the chat history, valid or not.
    try:
        await message.delete()
    except Exception:
        pass

    ok, note = await asyncio.to_thread(tts.check_key, key)
    if not ok:
        await state.clear()
        await message.answer(
            f"❌ Ключ не принят: {note}", reply_markup=close_kb()
        )
        return

    sset("eleven_key", key)
    await state.clear()
    await message.answer(
        f"✅ Ключ сохранён, голосов на аккаунте: {note}.\n"
        f"Сообщение с ключом удалено.",
        reply_markup=_kb(),
    )


@router.callback_query(F.data == "voice:id")
async def cb_ask_voice(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Ask.voice)
    if callback.message:
        await callback.message.edit_text(ASK_VOICE, reply_markup=_cancel_kb())
    await callback.answer()


@router.message(Ask.voice)
async def got_voice(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().split()[0] if message.text else ""
    if not value or len(value) < 8:
        await message.answer("Не похоже на Voice ID. Пришли строку со страницы голоса.")
        return
    sset("eleven_voice", value)
    await state.clear()
    await message.answer(f"✅ Voice ID сохранён: <code>{value}</code>", reply_markup=_kb())


@router.callback_query(F.data == "voice:list")
async def cb_list(callback: CallbackQuery) -> None:
    await callback.answer("Загружаю")
    try:
        voices = await asyncio.to_thread(tts.list_voices)
    except Exception as exc:
        if callback.message:
            await callback.message.answer(
                f"Не вышло: <code>{str(exc)[:200]}</code>", reply_markup=close_kb()
            )
        return

    current = sget("eleven_voice")
    kb = InlineKeyboardBuilder()
    lines = ["🗣 <b>Твои голоса</b>", ""]
    for voice in voices[:12]:
        mark = "✅ " if voice["id"] == current else ""
        labels = ", ".join(str(v) for v in list(voice["labels"].values())[:2])
        lines.append(f"{mark}<b>{voice['name']}</b> — <i>{labels}</i>")
        kb.row(
            InlineKeyboardButton(
                text=f"{mark}{voice['name']}", callback_data=f"voice:set:{voice['id']}"
            )
        )
    kb.row(InlineKeyboardButton(text="◀️ Голос", callback_data="set:voice"))
    if callback.message:
        await callback.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith("voice:set:"))
async def cb_pick(callback: CallbackQuery) -> None:
    sset("eleven_voice", callback.data.rsplit(":", 1)[1])
    sset("tts_provider", "eleven")
    await _show(callback)
    await callback.answer("Голос выбран")


@router.callback_query(F.data == "voice:test")
async def cb_test(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer("Синтезирую")
    out = MEDIA_DIR / "_voice" / f"probe-{tts.provider()}.mp3"
    try:
        result = await tts.speak(SAMPLE, out)
    except Exception as exc:
        if callback.message:
            await callback.message.answer(
                f"Не вышло: <code>{str(exc)[:250]}</code>", reply_markup=close_kb()
            )
        return
    if callback.message:
        await callback.message.answer_voice(
            FSInputFile(result["path"]),
            caption=f"🔊 {tts.provider()} · {result['duration']:.1f} сек",
            reply_markup=close_kb(),
        )
