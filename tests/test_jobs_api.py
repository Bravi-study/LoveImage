import asyncio
import uuid

import pytest

from loveimage import prompts

pytestmark = pytest.mark.anyio


def order(client, shoot_id, **body):
    return client.post(f"/api/shoots/{shoot_id}/jobs", json=body)


async def usage(pool):
    return await pool.fetchval("SELECT generations FROM daily_usage WHERE day = CURRENT_DATE")


async def fail_job(pool, attempt=1):
    await pool.execute(
        "UPDATE jobs SET status = 'failed', error = 'provider_failed', run_at = NULL, provider_job_id = 'old', attempt = $1",
        attempt,
    )


async def test_order_after_a_reached_limit_creates_a_queued_job(client, pool, ready):
    shoot_id = await ready()
    r = await order(client, shoot_id)
    assert r.status_code == 202
    body = r.json()
    assert [(j["variant"], j["status"]) for j in body["jobs"]] == [(1, "queued")]
    job = await pool.fetchrow("SELECT model, prompt, attempt, deadline_at > now() AS alive FROM jobs")
    assert job["model"] == "flare"
    assert job["prompt"] == prompts.TEMPLATE.format(scene="a quiet evening walk in an old town")
    assert (job["attempt"], job["alive"]) == (1, True)
    assert await usage(pool) == 1


async def test_a_new_variant_needs_the_last_one_finished(client, pool, queued):
    shoot_id = await queued()
    await pool.execute("UPDATE jobs SET status = 'succeeded', run_at = NULL")
    r = await order(client, shoot_id)
    assert [j["variant"] for j in r.json()["jobs"]] == [1, 2]
    assert await usage(pool) == 2


async def test_order_while_one_runs_changes_nothing(client, pool, queued):
    shoot_id = await queued()
    r = await order(client, shoot_id)
    assert r.status_code == 202
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    assert await usage(pool) == 1


async def test_twenty_parallel_orders_make_one_job(client, pool, ready):
    shoot_id = await ready()
    responses = await asyncio.gather(*(order(client, shoot_id) for _ in range(20)))
    assert {r.status_code for r in responses} == {202}
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1
    assert await usage(pool) == 1


async def test_daily_limit_lets_exactly_the_limit_through(client, pool, ready):
    shoots = [await ready() for _ in range(9)]
    responses = await asyncio.gather(*(order(client, s) for s in shoots))
    assert sorted(r.status_code for r in responses) == [202] * 5 + [429] * 4
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 5
    assert await usage(pool) == 5
    assert next(r for r in responses if r.status_code == 429).json()["detail"] == "daily_limit"


async def test_retry_of_a_failed_job_counts_and_raises_the_attempt(client, pool, queued):
    shoot_id = await queued()
    await fail_job(pool)

    r = await order(client, shoot_id, variant=1)

    assert r.status_code == 202
    job = await pool.fetchrow("SELECT status, attempt, error, provider_job_id, run_at <= now() AS due FROM jobs")
    assert dict(job) == {"status": "queued", "attempt": 2, "error": None, "provider_job_id": None, "due": True}
    assert await usage(pool) == 2


async def test_three_attempts_is_the_end(client, pool, queued):
    shoot_id = await queued()
    await fail_job(pool, attempt=3)
    r = await order(client, shoot_id, variant=1)
    assert r.status_code == 409
    assert r.json()["detail"] == "not_retryable"
    assert await usage(pool) == 1


@pytest.mark.parametrize("variant, status", [(1, "succeeded"), (2, "failed")])
async def test_only_a_failed_variant_can_be_repeated(client, pool, queued, variant, status):
    shoot_id = await queued()
    await pool.execute("UPDATE jobs SET status = $1, run_at = NULL", status)
    r = await order(client, shoot_id, variant=variant)
    assert r.status_code == 409
    assert r.json()["detail"] == "not_retryable"


async def test_retry_refused_by_the_limit_leaves_the_job_failed(client, pool, queued, settings):
    settings.daily_generation_limit = 1
    shoot_id = await queued()
    await fail_job(pool)
    r = await order(client, shoot_id, variant=1)
    assert r.status_code == 429
    assert tuple(await pool.fetchrow("SELECT status, attempt FROM jobs")) == ("failed", 1)


async def test_can_retry_flag_follows_the_attempts(client, pool, queued):
    shoot_id = await queued()
    await fail_job(pool, attempt=2)
    assert (await client.get(f"/api/shoots/{shoot_id}")).json()["jobs"][0]["can_retry"] is True
    await fail_job(pool, attempt=3)
    assert (await client.get(f"/api/shoots/{shoot_id}")).json()["jobs"][0]["can_retry"] is False


async def test_a_task_of_unknown_outcome_is_not_repeated(client, pool, queued):
    shoot_id = await queued()
    await pool.execute("UPDATE jobs SET status = 'failed', error = 'submit_unknown', run_at = NULL, attempt = 1")
    assert (await client.get(f"/api/shoots/{shoot_id}")).json()["jobs"][0]["can_retry"] is False
    r = await order(client, shoot_id, variant=1)
    assert (r.status_code, r.json()["detail"]) == (409, "not_retryable")


async def test_order_before_the_shoot_is_ready_is_refused(client, uploaded):
    r = await order(client, await uploaded())
    assert r.status_code == 409
    assert r.json()["detail"] == "not_ready"


async def test_order_on_unknown_shoot_is_404(client):
    assert (await order(client, uuid.uuid4())).status_code == 404


@pytest.mark.parametrize("variant", [0, 1001, "1; DROP"])
async def test_bad_variant_is_refused_without_an_echo(client, ready, variant):
    r = await order(client, await ready(), variant=variant)
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}


async def test_result_is_served_only_when_it_exists(client, pool, ready, settings):
    shoot_id = await ready()
    url = f"/api/shoots/{shoot_id}/results/1"
    assert (await client.get(url)).status_code == 404
    await order(client, shoot_id)
    assert (await client.get(url)).status_code == 404

    relative = f"shoots/{shoot_id}/v1-1.jpg"
    (settings.data_dir / relative).write_bytes(b"\xff\xd8result")
    await pool.execute("UPDATE jobs SET status = 'succeeded', run_at = NULL, result_path = $1", relative)

    r = await client.get(url)
    assert r.status_code == 200
    assert r.content == b"\xff\xd8result"
    assert (await client.get(f"/api/shoots/{shoot_id}")).json()["jobs"][0]["result_url"] == url
