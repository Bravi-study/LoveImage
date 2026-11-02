import asyncio
import logging

from loveimage import files, imaging, kie, llm, prompts, store

log = logging.getLogger(__name__)

LLM_IMAGE_SIDE = 768
RETRY_DELAY = 5
RESULT_SIDE = 4096
PHOTOS = ("photo1.jpg", "photo2.jpg")


async def _read_photos(ctx, shoot_id) -> list[bytes]:
    folder = ctx.settings.shoot_dir(shoot_id)
    return [await asyncio.to_thread((folder / name).read_bytes) for name in PHOTOS]


async def analyze(ctx, row):
    shoot_id = row["id"]
    if row["expired"]:
        await store.finish_shoot(ctx.pool, shoot_id, "failed")
        return
    small = [await imaging.normalize(jpeg, max_side=LLM_IMAGE_SIDE) for jpeg in await _read_photos(ctx, shoot_id)]
    try:
        verdict = await llm.analyze(ctx.http, ctx.settings, small[0], small[1], row["wish"])
    except llm.Transient as e:
        log.warning("analysis of %s will be repeated: %s", shoot_id, e)
        await store.retry_shoot_later(ctx.pool, shoot_id, RETRY_DELAY)
    except llm.Fatal as e:
        log.error("analysis of %s failed: %s", shoot_id, e)
        await store.finish_shoot(ctx.pool, shoot_id, "failed")
    else:
        reason = verdict.reject_reason(row["wish"])
        if reason:
            await store.finish_shoot(ctx.pool, shoot_id, "rejected", reason)
            # After the database: a crash between the two leaves a spare folder, not a broken shoot.
            await asyncio.to_thread(files.remove_folder, ctx.settings.shoot_dir(shoot_id))
            return
        # An empty wish means no scene: each variant then draws its own from the presets.
        scene = verdict.scene_en if row["wish"] else None
        await store.approve_shoot(
            ctx.pool,
            shoot_id,
            scene,
            ctx.settings.kie_model,
            prompts.build(scene),
            ctx.settings.daily_generation_limit,
            ctx.settings.personal_daily_limit,
        )


async def analysis_crashed(ctx, row):
    await store.finish_shoot(ctx.pool, row["id"], "failed")


async def handle_job(ctx, row):
    if row["status"] == "queued":
        await _submit(ctx, row)
    elif row["status"] == "submitting":
        # The previous run died between committing this status and storing the answer.
        log.error("job %s attempt %s: outcome of createTask is unknown", row["id"], row["attempt"])
        await store.job_failed(ctx.pool, row, "submitting", "submit_unknown")
    else:
        await _poll(ctx, row)


async def job_crashed(ctx, row):
    await store.job_crashed(ctx.pool, row)


async def _submit(ctx, row):
    if row["expired"]:
        await store.job_failed(ctx.pool, row, "queued", "deadline")
        return
    photos = await _read_photos(ctx, row["shoot_id"])
    try:
        # Together: their timeouts must fit in the worker's limit along with createTask.
        # shortcut: a repeated round uploads both photos again and leaves more copies at kie.ai, keep the urls on the row if upload errors turn out common.
        image_urls = await asyncio.gather(*(kie.upload(ctx.http, ctx.settings, jpeg) for jpeg in photos))
    except kie.Retry as e:
        log.warning("job %s: upload will be repeated: %s", row["id"], e)
        await store.job_back_to_queue(ctx.pool, row, "queued", RETRY_DELAY)
        return
    except kie.Rejected as e:
        log.error("job %s: upload refused: %s", row["id"], e)
        await store.job_failed(ctx.pool, row, "queued", e.error)
        return

    # Committed before the request leaves: a crash after this point must not pay twice.
    if not await store.job_to_submitting(ctx.pool, row):
        return
    try:
        task_id = await kie.create_task(ctx.http, ctx.settings, row["model"], row["prompt"], image_urls)
    except kie.Retry as e:
        log.warning("job %s: createTask will be repeated: %s", row["id"], e)
        await store.job_back_to_queue(ctx.pool, row, "submitting", RETRY_DELAY)
    except kie.Unknown as e:
        log.error("job %s attempt %s: outcome of createTask is unknown: %s", row["id"], row["attempt"], e)
        await store.job_failed(ctx.pool, row, "submitting", "submit_unknown")
    except kie.Rejected as e:
        log.error("job %s: createTask refused: %s", row["id"], e)
        await store.job_failed(ctx.pool, row, "submitting", e.error)
    else:
        log.info("job %s attempt %s: created task %s", row["id"], row["attempt"], task_id)
        await store.job_submitted(ctx.pool, row, task_id, prompts.CREDITS[ctx.settings.kie_resolution])


async def _poll(ctx, row):
    deadline_error = "deadline"
    try:
        url = await kie.poll_task(ctx.http, ctx.settings, row["provider_job_id"])
        if url is not None:
            deadline_error = "download_failed"
            await _collect(ctx, row, url)
            return
    except (kie.Retry, imaging.NotDecoded) as e:
        log.warning("job %s: poll will be repeated: %s", row["id"], e)
    except (kie.Rejected, imaging.BadImage) as e:
        log.error("job %s task %s failed: %s", row["id"], row["provider_job_id"], e)
        await store.job_failed(ctx.pool, row, "submitted", getattr(e, "error", "provider_failed"))
        return
    if row["expired"]:
        log.error("job %s task %s ran out of time: %s", row["id"], row["provider_job_id"], deadline_error)
        await store.job_failed(ctx.pool, row, "submitted", deadline_error)
    else:
        await store.job_poll_later(ctx.pool, row, _poll_delay(row["seconds_left"]))


async def _collect(ctx, row, url):
    jpeg = await imaging.normalize(await kie.download(ctx.http, ctx.settings, url), max_side=RESULT_SIDE)
    relative = f"shoots/{row['shoot_id']}/v{row['variant']}-{row['attempt']}.jpg"
    await asyncio.to_thread(imaging.write_atomic, ctx.settings.data_dir / relative, jpeg)
    await store.job_succeeded(ctx.pool, row, relative)


def _poll_delay(seconds_left):
    age = store.GENERATION_DEADLINE - seconds_left
    return 3 if age < 30 else 5 if age < 120 else 10
