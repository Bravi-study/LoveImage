import asyncio
import io
import json

import asyncpg
import httpx
import pytest
from PIL import Image

from loveimage import handlers, imaging, prompts, store, worker

pytestmark = pytest.mark.anyio

RESULT_URL = "https://cdn.test/result.png"
UPLOAD = "/api/file-stream-upload"
CREATE = "/api/v1/jobs/createTask"
UPLOADED = {"code": 200, "data": {"downloadUrl": "https://files.test/up.jpg"}}


def result_image():
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), "purple").save(buf, "PNG")
    return buf.getvalue()


class Kie:
    """A scripted kie.ai: every endpoint answers from its own slot and records its calls."""

    def __init__(self):
        self.calls = []
        self.upload = httpx.Response(200, json=UPLOADED)
        self.create = lambda: httpx.Response(200, json={"code": 200, "data": {"taskId": "task-1"}})
        self.record = httpx.Response(200, json={"code": 200, "data": {"state": "generating"}})
        self.file = httpx.Response(200, content=result_image())

    def __call__(self, request):
        path = request.url.path
        self.calls.append(path)
        if path == UPLOAD:
            return self._answer(self.upload)
        if path == CREATE:
            return self._answer(self.create)
        if path == "/api/v1/jobs/recordInfo":
            return self._answer(self.record)
        if str(request.url) == RESULT_URL:
            return self._answer(self.file)
        raise AssertionError(f"unexpected request {request.url}")

    @staticmethod
    def _answer(answer):
        if callable(answer):
            answer = answer()
        if isinstance(answer, Exception):
            raise answer
        return answer

    def count(self, path):
        return self.calls.count(path)


def success(url=RESULT_URL):
    return httpx.Response(
        200,
        json={"code": 200, "data": {"state": "success", "resultJson": json.dumps({"resultUrls": [url]})}},
    )


@pytest.fixture
def kie():
    return Kie()


@pytest.fixture
def ctx(pool, settings, kie):
    return worker.Ctx(pool, httpx.AsyncClient(transport=httpx.MockTransport(kie)), settings)


def ctx_with(pool, settings, handler):
    return worker.Ctx(pool, httpx.AsyncClient(transport=httpx.MockTransport(handler)), settings)


async def job(pool):
    return await pool.fetchrow(
        """SELECT status, attempt, error, provider_job_id, credits, result_path, run_at IS NULL AS parked,
                  run_at - now() AS delay, deadline_at - now() AS left
           FROM jobs"""
    )


async def step(ctx):
    await handlers.handle_job(ctx, await store.claim_job(ctx.pool))


async def make_due(pool):
    await pool.execute("UPDATE jobs SET run_at = now() WHERE run_at IS NOT NULL")


async def test_claim_is_a_lease(pool, queued):
    await queued()
    row = await store.claim_job(pool)
    assert row["status"] == "queued"
    assert await store.claim_job(pool) is None
    assert 100 < (await job(pool))["delay"].total_seconds() <= 120


async def test_submit_records_the_task_and_its_price(pool, ctx, queued):
    await queued()
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["provider_job_id"], row["credits"]) == ("submitted", "task-1", 6)
    assert row["delay"].total_seconds() <= 3
    assert 410 < row["left"].total_seconds() <= 420


async def test_create_task_request_has_the_expected_shape(pool, settings, queued):
    seen = {}

    def handler(request):
        if request.url.path == CREATE:
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "t"}})
        return httpx.Response(200, json=UPLOADED)

    await queued()
    await step(ctx_with(pool, settings, handler))

    assert seen["auth"] == "Bearer test-kie"
    assert seen["body"] == {
        "model": "flare",
        "input": {
            "prompt": prompts.TEMPLATE.format(scene="a quiet evening walk in an old town"),
            "input_urls": ["https://files.test/up.jpg"] * 2,
            "aspect_ratio": "3:4",
            "resolution": "1K",
        },
    }


async def test_both_photos_reach_input_urls_in_order_whichever_upload_ends_first(pool, settings, queued, photos):
    seen = {}

    async def handler(request):
        if request.url.path == CREATE:
            seen["urls"] = json.loads(request.content)["input"]["input_urls"]
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "t"}})
        first = photos[0] in request.content
        assert first or photos[1] in request.content
        if first:
            await asyncio.sleep(0.05)
        name = "first" if first else "second"
        return httpx.Response(200, json={"code": 200, "data": {"downloadUrl": f"https://files.test/{name}.jpg"}})

    await queued()
    await step(ctx_with(pool, settings, handler))
    assert seen["urls"] == ["https://files.test/first.jpg", "https://files.test/second.jpg"]


async def test_status_is_committed_before_the_create_request_leaves(pool, settings, queued):
    seen = []

    async def handler(request):
        if request.url.path == CREATE:
            # A separate pool connection sees only what is already committed.
            seen.append(await pool.fetchval("SELECT status FROM jobs"))
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "t"}})
        return httpx.Response(200, json=UPLOADED)

    await queued()
    await step(ctx_with(pool, settings, handler))
    assert seen == ["submitting"]


async def test_crash_after_the_request_left_never_repeats_it(pool, ctx, kie, queued):
    """The transport records a POST and the process dies before any answer is stored."""
    await queued()

    class Died(BaseException):
        pass

    def die():
        raise Died

    kie.create = die
    with pytest.raises(Died):
        await step(ctx)
    assert (await job(pool))["status"] == "submitting"

    await make_due(pool)
    await step(ctx)

    row = await job(pool)
    assert (row["status"], row["error"], row["parked"]) == ("failed", "submit_unknown", True)
    assert kie.count(CREATE) == 1


async def test_restarted_worker_loop_does_not_repeat_a_job_at_submitting(pool, ctx, kie, queued):
    await queued()
    assert await store.job_to_submitting(pool, await store.claim_job(pool))
    await make_due(pool)

    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run(ctx, stop))
    async with asyncio.timeout(5):
        while (await job(pool))["status"] != "failed":
            await asyncio.sleep(0.02)
    stop.set()
    await loop

    assert (await job(pool))["error"] == "submit_unknown"
    assert kie.calls == []


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("x"), httpx.ConnectTimeout("x"), httpx.PoolTimeout("x")],
)
async def test_request_that_never_left_is_repeated(pool, ctx, kie, queued, error):
    await queued()
    kie.create = error
    await step(ctx)
    row = await job(pool)
    assert row["status"] == "queued"
    assert row["delay"].total_seconds() <= 5

    kie.create = httpx.Response(200, json={"code": 200, "data": {"taskId": "task-2"}})
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["provider_job_id"] == "task-2"


async def test_rate_limit_code_is_repeated(pool, ctx, kie, queued):
    await queued()
    kie.create = httpx.Response(200, json={"code": 429, "msg": "slow down"})
    await step(ctx)
    assert (await job(pool))["status"] == "queued"


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ReadTimeout("x"),
        httpx.ReadError("x"),
        httpx.WriteError("x"),
        httpx.RemoteProtocolError("x"),
        httpx.Response(502),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"code": 200, "data": {}}),
        httpx.Response(200, json={"code": 200}),
        httpx.Response(200, json={"code": 777}),
    ],
)
async def test_unknown_outcome_is_never_repeated(pool, ctx, kie, queued, answer):
    await queued()
    kie.create = answer
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["error"], row["parked"]) == ("failed", "submit_unknown", True)
    assert await store.claim_job(pool) is None
    assert kie.count(CREATE) == 1


@pytest.mark.parametrize(
    "code, error",
    [(401, "provider_auth"), (402, "no_credits"), (422, "provider_rejected"), (500, "provider_rejected")],
)
async def test_provider_refusal_is_not_repeated_by_itself(pool, ctx, kie, queued, code, error):
    await queued()
    kie.create = httpx.Response(200, json={"code": code, "msg": "no"})
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["error"], row["parked"]) == ("failed", error, True)
    assert await store.claim_job(pool) is None
    assert kie.count(CREATE) == 1


async def test_passing_upload_failure_is_repeated_without_a_create(pool, ctx, kie, queued):
    await queued()
    kie.upload = httpx.ConnectError("x")
    await step(ctx)
    assert (await job(pool))["status"] == "queued"
    assert kie.count(CREATE) == 0


async def test_upload_refusal_fails_the_job(pool, ctx, kie, queued):
    await queued()
    kie.upload = httpx.Response(200, json={"code": 401, "msg": "bad key"})
    await step(ctx)
    assert (await job(pool))["error"] == "provider_auth"
    assert kie.count(CREATE) == 0


async def test_queued_job_past_its_deadline_fails_without_any_request(pool, ctx, kie, queued):
    await queued()
    await pool.execute("UPDATE jobs SET deadline_at = now() - interval '1 second'")
    await step(ctx)
    assert (await job(pool))["error"] == "deadline"
    assert kie.calls == []


async def test_failure_after_create_keeps_the_money_trail(pool, ctx, kie, queued):
    await queued()
    await step(ctx)
    kie.record = httpx.Response(200, json={"code": 200, "data": {"state": "fail", "failCode": "501"}})
    await make_due(pool)
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["error"], row["credits"], row["parked"]) == ("failed", "provider_failed", 6, True)


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("x"),
        httpx.ReadTimeout("x"),
        httpx.Response(503),
        httpx.Response(429),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"code": 429}),
        httpx.Response(200, json={"code": 200, "data": {"state": "waiting"}}),
        httpx.Response(200, json={"code": 200, "data": {"state": "brand-new-state"}}),
    ],
)
async def test_poll_keeps_going_on_anything_passing(pool, ctx, kie, queued, answer):
    await queued()
    await step(ctx)
    kie.record = answer
    await make_due(pool)
    await step(ctx)
    row = await job(pool)
    assert row["status"] == "submitted"
    assert 0 < row["delay"].total_seconds() <= 10
    assert kie.count(CREATE) == 1


async def test_poll_slows_down_with_age(pool, ctx, queued):
    await queued()
    await step(ctx)
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["delay"].total_seconds() <= 3

    await pool.execute("UPDATE jobs SET deadline_at = now() + interval '350 seconds', run_at = now()")
    await step(ctx)
    assert 3 < (await job(pool))["delay"].total_seconds() <= 5

    await pool.execute("UPDATE jobs SET deadline_at = now() + interval '200 seconds', run_at = now()")
    await step(ctx)
    assert 5 < (await job(pool))["delay"].total_seconds() <= 10


async def test_poll_stops_at_the_deadline(pool, ctx, queued):
    await queued()
    await step(ctx)
    await pool.execute("UPDATE jobs SET deadline_at = now() - interval '1 second', run_at = now()")
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["error"]) == ("failed", "deadline")


async def test_crash_while_polling_keeps_the_paid_task_until_its_deadline(pool, ctx, queued):
    await queued()
    await step(ctx)
    await make_due(pool)
    await store.job_crashed(pool, await store.claim_job(pool))
    row = await job(pool)
    assert (row["status"], row["parked"]) == ("submitted", False)

    await pool.execute("UPDATE jobs SET deadline_at = now() - interval '1 second', run_at = now()")
    await store.job_crashed(pool, await store.claim_job(pool))
    row = await job(pool)
    assert (row["status"], row["error"], row["parked"]) == ("failed", "internal", True)


async def test_crash_at_submitting_counts_as_an_unknown_outcome(pool, queued):
    await queued()
    await pool.execute("UPDATE jobs SET status = 'submitting', run_at = now()")
    await store.job_crashed(pool, await store.claim_job(pool))
    row = await job(pool)
    assert (row["status"], row["error"]) == ("failed", "submit_unknown")


async def test_a_result_the_child_did_not_finish_is_fetched_again(pool, ctx, kie, queued, monkeypatch):
    await queued()
    await step(ctx)
    kie.record = success()
    await make_due(pool)

    async def killed(*args, **kwargs):
        raise imaging.NotDecoded("killed by signal 9")

    monkeypatch.setattr(imaging, "normalize", killed)
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["parked"]) == ("submitted", False)


@pytest.mark.parametrize("code, given_back", [(402, True), (401, True), (422, False)])
async def test_only_an_account_problem_gives_the_limit_back(pool, ctx, kie, queued, code, given_back):
    await queued()
    kie.create = lambda: httpx.Response(200, json={"code": code})
    await step(ctx)
    assert (await job(pool))["status"] == "failed"
    used = 0 if given_back else 1
    assert await pool.fetchval("SELECT generations FROM daily_usage") == used
    assert await pool.fetchval("SELECT generations FROM user_usage") == used


async def test_auth_problem_while_polling_fails(pool, ctx, kie, queued):
    await queued()
    await step(ctx)
    kie.record = httpx.Response(200, json={"code": 401})
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["error"] == "provider_auth"


async def test_success_downloads_and_stores_a_clean_jpeg(pool, ctx, kie, queued, settings):
    shoot_id = await queued()
    await step(ctx)
    kie.record = success()
    await make_due(pool)
    await step(ctx)
    row = await job(pool)
    assert (row["status"], row["parked"], row["error"]) == ("succeeded", True, None)
    assert row["result_path"] == f"shoots/{shoot_id}/v1-1.jpg"
    assert Image.open(settings.data_dir / row["result_path"]).format == "JPEG"


async def test_success_after_the_deadline_is_still_collected(pool, ctx, kie, queued):
    await queued()
    await step(ctx)
    kie.record = success()
    await pool.execute("UPDATE jobs SET deadline_at = now() - interval '1 minute', run_at = now()")
    await step(ctx)
    assert (await job(pool))["status"] == "succeeded"


async def test_crash_after_download_gives_one_success(pool, ctx, kie, queued, settings, monkeypatch):
    await queued()
    await step(ctx)
    kie.record = success()

    class Died(BaseException):
        pass

    real = store.job_succeeded

    async def die(*args):
        raise Died

    monkeypatch.setattr(store, "job_succeeded", die)
    await make_due(pool)
    with pytest.raises(Died):
        await step(ctx)
    assert (await job(pool))["status"] == "submitted"

    monkeypatch.setattr(store, "job_succeeded", real)
    await make_due(pool)
    await step(ctx)

    row = await job(pool)
    assert row["status"] == "succeeded"
    assert kie.count(CREATE) == 1
    assert [p.name for p in (settings.data_dir / row["result_path"]).parent.glob("v*")] == ["v1-1.jpg"]


@pytest.mark.parametrize(
    "result_json",
    ["not json", "{}", '{"resultUrls": []}', '{"resultUrls": [5]}', None],
)
async def test_broken_result_json_fails(pool, ctx, kie, queued, result_json):
    await queued()
    await step(ctx)
    kie.record = httpx.Response(200, json={"code": 200, "data": {"state": "success", "resultJson": result_json}})
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["error"] == "provider_failed"


async def test_plain_http_result_url_is_refused(pool, ctx, kie, queued):
    await queued()
    await step(ctx)
    kie.record = success("http://169.254.169.254/latest")
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["error"] == "provider_failed"


async def test_result_that_is_not_an_image_fails(pool, ctx, kie, queued):
    await queued()
    await step(ctx)
    kie.record = success()
    kie.file = httpx.Response(200, content=b"<html>not an image</html>")
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["error"] == "provider_failed"


async def test_oversized_result_fails(pool, ctx, kie, queued, monkeypatch):
    monkeypatch.setattr("loveimage.kie.MAX_RESULT", 100)
    await queued()
    await step(ctx)
    kie.record = success()
    kie.file = httpx.Response(200, content=b"x" * 101)
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["error"] == "provider_failed"


async def test_failed_download_is_repeated_until_the_deadline(pool, ctx, kie, queued):
    await queued()
    await step(ctx)
    kie.record = success()
    kie.file = httpx.Response(503)
    await make_due(pool)
    await step(ctx)
    assert (await job(pool))["status"] == "submitted"

    await pool.execute("UPDATE jobs SET deadline_at = now() - interval '1 second', run_at = now()")
    await step(ctx)
    assert (await job(pool))["error"] == "download_failed"


async def test_stale_attempt_changes_nothing(pool, ctx, kie, queued):
    """Attempt 1 wakes up after the job was already failed and restarted as attempt 2."""
    await queued()
    stale = await store.claim_job(pool)
    await pool.execute(
        """UPDATE jobs SET status = 'queued', attempt = 2, run_at = now() + interval '1 hour',
                           deadline_at = now() + interval '1 hour'"""
    )

    await handlers.handle_job(ctx, stale)

    row = await job(pool)
    assert (row["status"], row["attempt"], row["provider_job_id"]) == ("queued", 2, None)
    assert kie.count(CREATE) == 0


async def test_fence_stops_a_worker_that_lost_the_job_during_the_upload(pool, settings, queued):
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        await pool.execute("UPDATE jobs SET attempt = 2")
        return httpx.Response(200, json=UPLOADED)

    await queued()
    await step(ctx_with(pool, settings, handler))
    assert CREATE not in calls
    assert (await job(pool))["status"] == "queued"


async def test_stale_worker_cannot_write_over_a_newer_attempt(pool, queued):
    await queued()
    stale = await store.claim_job(pool)
    await store.job_to_submitting(pool, stale)
    await pool.execute("UPDATE jobs SET attempt = 2, status = 'submitted', provider_job_id = 'new', run_at = now()")

    assert not await store.job_submitted(pool, stale, "old", 6)
    assert not await store.job_failed(pool, stale, "submitting", "submit_unknown")
    assert not await store.job_back_to_queue(pool, stale, "submitting", 5)
    assert not await store.job_succeeded(pool, stale, "old.jpg")

    row = await job(pool)
    assert (row["status"], row["provider_job_id"], row["error"], row["credits"]) == ("submitted", "new", None, 0)


async def test_unexpected_error_in_the_loop_fails_the_job_without_a_create(pool, ctx, kie, queued, settings):
    shoot_id = await queued()
    (settings.shoot_dir(shoot_id) / "photo2.jpg").unlink()
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run(ctx, stop))
    async with asyncio.timeout(5):
        while (await job(pool))["status"] != "failed":
            await asyncio.sleep(0.02)
    stop.set()
    await loop
    row = await job(pool)
    assert (row["error"], row["parked"]) == ("internal", True)
    assert kie.count(CREATE) == 0


async def spend(pool):
    return await pool.fetchrow("SELECT generations, credits FROM daily_usage WHERE day = CURRENT_DATE")


async def test_submit_adds_its_price_to_the_day(pool, ctx, queued):
    await queued()
    await step(ctx)
    assert tuple(await spend(pool)) == (1, 6)


async def test_job_ordered_yesterday_is_paid_for_today(pool, ctx, queued):
    await queued()
    await pool.execute("DELETE FROM daily_usage")
    await step(ctx)
    assert tuple(await spend(pool)) == (0, 6)


async def test_stale_submit_adds_nothing(pool, queued):
    await queued()
    row = await store.claim_job(pool)
    assert await store.job_submitted(pool, row, "task-1", 6) is False
    assert (await spend(pool))["credits"] == 0


async def test_status_and_spend_commit_together(pool, queued):
    await queued()
    row = await store.claim_job(pool)
    assert await store.job_to_submitting(pool, row)
    await pool.execute("UPDATE daily_usage SET credits = 2147483647")
    with pytest.raises(asyncpg.NumericValueOutOfRangeError):
        await store.job_submitted(pool, row, "task-1", 6)
    assert (await job(pool))["status"] == "submitting"
