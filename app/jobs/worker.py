import asyncio
import logging
import traceback
from typing import Awaitable, Callable

from aiogram import Bot

from app.jobs import queue
from app.jobs.progress import Progress

log = logging.getLogger("painbot.worker")

JobHandler = Callable[[dict, Progress, Bot], Awaitable[None]]
_registry: dict[str, JobHandler] = {}

POLL_INTERVAL = 1.0

# Short jobs get their own lane so they are not stuck behind a long sweep.
FAST_KINDS = {"ideate.silent", "ideate.run", "debug.demo", "social.run"}


def register(kind: str) -> Callable[[JobHandler], JobHandler]:
    def deco(fn: JobHandler) -> JobHandler:
        _registry[kind] = fn
        return fn

    return deco


def known_kinds() -> list[str]:
    return sorted(_registry)


async def run_worker(bot: Bot, lane: str = "main") -> None:
    kinds = FAST_KINDS if lane == "fast" else None
    exclude = None if lane == "fast" else FAST_KINDS
    log.info("worker[%s] started", lane)
    while True:
        try:
            row = queue.claim(kinds=kinds, exclude=exclude)
        except Exception:
            log.exception("claim failed")
            await asyncio.sleep(POLL_INTERVAL)
            continue

        if row is None:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        kind = row["kind"]
        handler = _registry.get(kind)
        progress = Progress(bot, row["chat_id"], row["message_id"], kind)

        if handler is None:
            queue.fail(row["id"], 99, f"no handler for kind={kind}")
            await progress.failed(f"нет обработчика для {kind}")
            continue

        try:
            progress.start_ticker()
            await handler(queue.payload_of(row), progress, bot)
            queue.finish(row["id"])
        except Exception as exc:  # a failed job must never take the worker down
            log.error("job %s (%s) failed: %s", row["id"], kind, exc)
            log.debug(traceback.format_exc())
            will_retry = queue.fail(row["id"], row["attempts"], str(exc))
            if not will_retry:
                await progress.failed(str(exc))
        finally:
            await progress.stop_ticker()
