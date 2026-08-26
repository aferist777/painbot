import asyncio
import contextlib
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage

from app import config
from app.admin import server as admin
from app.bot.handlers import (
    article,
    collect,
    common,
    debug,
    ideas,
    pains,
    reel,
    settings,
    start,
    voice,
)
from app.bot.middlewares import OwnerOnly
from app.collect.seed import seed_sources
from app.db.base import init_db
from app.jobs import (  # noqa: F401  (register job kinds)
    handlers_article,
    handlers_collect,
    handlers_ideate,
    handlers_pipeline,
    handlers_reel,
    handlers_script,
    handlers_social,
)
from app.jobs import queue
from app.jobs.scheduler import start_scheduler
from app.jobs.worker import run_worker

# The console scrolls away and cannot be read after the fact; every line also
# goes to data/bot.log so a crash can be looked up later.
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_LOG_FILE = config.DATA_DIR / "bot.log"
_file = RotatingFileHandler(_LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_file.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_console, _file])
log = logging.getLogger("painbot")


async def main() -> None:
    if not config.TG_TOKEN:
        log.error("TG_TOKEN is empty — put it into painbot/.env (see .env.example)")
        sys.exit(1)

    init_db()
    log.info("db ready at %s", config.DB_PATH)
    log.info("лог пишется в %s", _LOG_FILE)

    bot = Bot(config.TG_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(OwnerOnly())
    dp.callback_query.middleware(OwnerOnly())
    dp.include_routers(
        common.router,
        start.router,
        settings.router,
        voice.router,
        collect.router,
        pains.router,
        ideas.router,
        article.router,
        reel.router,
        debug.router,
    )

    worker = None
    fast = None
    scheduler = None
    panel = None
    try:
        # Verify the token before starting anything that would need shutting down.
        me = await bot.me()
        log.info("bot @%s is up", me.username)

        seed_sources()
        orphans = queue.reclaim_orphans()
        if orphans:
            log.info("вернул в очередь %s недоделанных задач", orphans)
        worker = asyncio.create_task(run_worker(bot, lane="main"))
        fast = asyncio.create_task(run_worker(bot, lane="fast"))
        scheduler = start_scheduler(bot)
        try:
            panel = await admin.start()
        except OSError as exc:
            # A busy port must not cost the bot: the panel is a convenience.
            log.warning("админка не поднялась на %s: %s", admin.URL, exc)

        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        log.error(
            "Telegram отверг токен. Обычно это значит, что его отозвали в "
            "BotFather. Возьми свежий: BotFather -> /mybots -> твой бот -> "
            "API Token, и впиши его в painbot/.env как TG_TOKEN."
        )
    finally:
        if panel is not None:
            await panel.cleanup()
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        for task in (worker, fast):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
