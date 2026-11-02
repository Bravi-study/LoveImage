import asyncio
import logging
import signal
from contextlib import suppress
from dataclasses import dataclass

import asyncpg
import httpx

from loveimage import db, files, handlers, store
from loveimage.settings import Settings

log = logging.getLogger(__name__)

CLEANUP_EVERY = 3600
CONCURRENCY = 10
HANDLER_TIMEOUT = 90
IDLE_DELAY = 1
SHUTDOWN_GRACE = 30


@dataclass
class Ctx:
    pool: asyncpg.Pool
    http: httpx.AsyncClient
    settings: Settings


async def _guarded(handler, on_crash, ctx, row):
    try:
        async with asyncio.timeout(HANDLER_TIMEOUT):
            await handler(ctx, row)
    except Exception:
        log.exception("%s crashed on %s", handler.__name__, row["id"])
        await on_crash(ctx, row)


async def _claim(ctx):
    if row := await store.claim_shoot(ctx.pool):
        return _guarded(handlers.analyze, handlers.analysis_crashed, ctx, row)
    if row := await store.claim_job(ctx.pool):
        return _guarded(handlers.handle_job, handlers.job_crashed, ctx, row)
    return None


async def run(ctx: Ctx, stop: asyncio.Event) -> None:
    tasks = set()
    stopping = asyncio.create_task(stop.wait())
    while not stop.is_set():
        if len(tasks) >= CONCURRENCY:
            await asyncio.wait({*tasks, stopping}, return_when=asyncio.FIRST_COMPLETED)
            continue
        try:
            work = await _claim(ctx)
        except (asyncpg.PostgresError, OSError):
            log.exception("cannot reach the database")
            work = None
        if work is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), IDLE_DELAY)
            continue
        task = asyncio.create_task(work)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    stopping.cancel()
    if tasks:
        # A task cut off here comes back when its lease runs out.
        await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def clean_up(ctx: Ctx) -> None:
    removed = await store.clean_up(ctx.pool)
    if removed:
        # Counts only: a wrong condition on the live site would otherwise leave no trace.
        log.info("cleanup removed %d shoots", len(removed))
    # shortcut: after the commit the ids exist only in memory, so a folder that fails to delete, or a crash before
    # the removal, leaves it on disk with no retry, compare folders with rows when the data folder grows or a
    # "cannot remove the folder" line shows in the log.
    for shoot_id in removed:
        try:
            await asyncio.to_thread(files.remove_folder, ctx.settings.shoot_dir(shoot_id))
        except OSError:
            log.exception("cannot remove the folder of %s", shoot_id)


async def clean_up_forever(ctx: Ctx, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await clean_up(ctx)
        except Exception:
            # Whatever breaks here must not stop the queue, the next hour tries again.
            log.exception("cleanup failed")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), CLEANUP_EVERY)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings()
    pool = await db.create_pool(settings.database_url)
    await db.migrate(pool)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient() as http:
        ctx = Ctx(pool, http, settings)
        cleaning = asyncio.create_task(clean_up_forever(ctx, stop))
        await run(ctx, stop)
        await cleaning
    try:
        await asyncio.wait_for(pool.close(), 5)
    except TimeoutError:
        pool.terminate()


if __name__ == "__main__":
    asyncio.run(main())
