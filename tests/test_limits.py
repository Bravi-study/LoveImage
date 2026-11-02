import asyncio
from datetime import date

import pytest

from loveimage import limits, store
from loveimage.settings import Settings

pytestmark = pytest.mark.anyio

JUNK = b"not an image"


def order(client, shoot_id, **body):
    return client.post(f"/api/shoots/{shoot_id}/jobs", json=body)


async def counts(pool):
    return (
        await pool.fetchval("SELECT count(*) FROM jobs"),
        await pool.fetchval("SELECT coalesce(sum(generations), 0) FROM user_usage"),
        await pool.fetchval("SELECT coalesce(sum(generations), 0) FROM daily_usage"),
    )


async def add_usage(pool, user_id=None, daily=None, personal=None):
    if daily is not None:
        await pool.execute("INSERT INTO daily_usage (day, generations) VALUES (CURRENT_DATE, $1)", daily)
    if personal is not None:
        await pool.execute("INSERT INTO user_usage (user_id, day, generations) VALUES ($1, CURRENT_DATE, $2)", user_id, personal)


def test_defaults():
    fields = Settings.model_fields
    assert fields["daily_generation_limit"].default == 30
    assert fields["personal_daily_limit"].default == 6
    assert fields["uploads_per_ip"].default == 30
    assert fields["images_per_ip"].default == 12


async def test_seventh_image_is_refused_and_leaves_no_trace(client, ready, settings, pool):
    settings.personal_daily_limit, settings.daily_generation_limit = 6, 100
    shoots = [await ready() for _ in range(7)]
    for shoot_id in shoots[:6]:
        assert (await order(client, shoot_id)).status_code == 202

    r = await order(client, shoots[6])

    assert r.status_code == 429
    assert r.json()["detail"] == "personal_limit"
    assert await counts(pool) == (6, 6, 6)


async def test_parallel_orders_stop_exactly_at_the_limit(client, ready, settings, pool):
    settings.personal_daily_limit, settings.daily_generation_limit = 5, 100
    shoots = [await ready() for _ in range(12)]
    answers = await asyncio.gather(*(order(client, s) for s in shoots))
    assert sorted(r.status_code for r in answers) == [202] * 5 + [429] * 7
    assert await counts(pool) == (5, 5, 5)


async def test_site_limit_rolls_the_job_and_the_personal_count_back(client, ready, settings, pool):
    settings.personal_daily_limit, settings.daily_generation_limit = 10, 1
    first, second = await ready(), await ready()
    assert (await order(client, first)).status_code == 202

    r = await order(client, second)

    assert r.status_code == 429
    assert r.json()["detail"] == "daily_limit"
    assert await counts(pool) == (1, 1, 1)


async def test_limit_of_zero_refuses_the_first_order_of_the_day(client, ready, settings, pool):
    settings.daily_generation_limit = 0
    r = await order(client, await ready())
    assert r.status_code == 429
    assert r.json()["detail"] == "daily_limit"
    assert await counts(pool) == (0, 0, 0)
    assert await pool.fetchval("SELECT count(*) FROM daily_usage") == 0


async def test_personal_limit_of_zero_stops_everyone(client, ready, settings, pool):
    settings.personal_daily_limit = 0
    r = await order(client, await ready())
    assert r.status_code == 429
    assert r.json()["detail"] == "personal_limit"
    assert await counts(pool) == (0, 0, 0)


async def test_repeat_after_a_failure_counts(client, ready, settings, pool):
    settings.personal_daily_limit = 1
    shoot_id = await ready()
    assert (await order(client, shoot_id)).status_code == 202
    await pool.execute("UPDATE jobs SET status = 'failed', run_at = NULL, error = 'x'")

    r = await order(client, shoot_id, variant=1)

    assert r.status_code == 429
    assert tuple(await pool.fetchrow("SELECT attempt, status FROM jobs")) == (1, "failed")


async def test_people_are_counted_apart(client, ready, visit, new_guest, settings):
    settings.personal_daily_limit = 1
    assert (await order(client, await ready())).status_code == 202
    other = await new_guest()
    async with visit(other.token) as guest:
        assert (await order(guest, await ready(user_id=other.id))).status_code == 202


@pytest.mark.parametrize(
    "daily_limit, personal_limit, used",
    [(0, 10, 0), (10, 0, 0), (1, 10, 1), (10, 1, 1)],
    ids=["daily-zero", "personal-zero", "daily-reached", "personal-reached"],
)
async def test_approval_at_a_limit_leaves_the_shoot_ready_with_no_jobs(pool, owner, uploaded, client, daily_limit, personal_limit, used):
    if used:
        await add_usage(pool, owner.id, daily=used, personal=used)
    before = await counts(pool)
    shoot_id = await uploaded()

    await store.approve_shoot(pool, shoot_id, "a walk", "m", "p", daily_limit, personal_limit)

    assert await counts(pool) == before
    body = (await client.get(f"/api/shoots/{shoot_id}")).json()
    assert (body["status"], body["jobs"]) == ("ready", [])


async def test_ready_with_no_jobs_can_be_ordered_once_the_limit_allows(pool, owner, uploaded, client, settings):
    shoot_id = await uploaded()
    await store.approve_shoot(pool, shoot_id, "a walk", "m", "p", 0, 10)
    settings.daily_generation_limit = 1
    r = await order(client, shoot_id)
    assert r.status_code == 202
    assert await counts(pool) == (1, 1, 1)


async def test_upload_precheck_stops_a_full_day_and_writes_nothing(stranger, send, pool, settings):
    settings.daily_generation_limit = 3
    await add_usage(pool, daily=3)

    r = await send(stranger)

    assert r.status_code == 429
    assert r.json()["detail"] == "daily_limit"
    assert "set-cookie" not in r.headers
    assert await pool.fetchval("SELECT count(*) FROM shoots") == 0
    assert await pool.fetchval("SELECT count(*) FROM users") == 0
    assert await counts(pool) == (0, 0, 3)


async def test_upload_precheck_stops_a_full_person(client, send, owner, pool, settings):
    settings.personal_daily_limit = 2
    await add_usage(pool, owner.id, personal=2)

    r = await send(client)

    assert r.status_code == 429
    assert r.json()["detail"] == "personal_limit"
    assert await pool.fetchval("SELECT count(*) FROM shoots") == 0
    assert await counts(pool) == (0, 2, 0)


async def test_upload_precheck_respects_a_limit_of_zero(client, send, settings):
    settings.daily_generation_limit = 0
    assert (await send(client)).json()["detail"] == "daily_limit"


async def test_precheck_changes_no_row(pool, owner):
    assert await store.limit_reached(pool, owner.id, 1, 1) is None
    assert await store.limit_reached(pool, None, 1, 0) is None
    assert await store.limit_reached(pool, owner.id, 1, 0) == "personal_limit"
    assert await store.limit_reached(pool, owner.id, 0, 1) == "daily_limit"
    assert await counts(pool) == (0, 0, 0)
    assert await pool.fetchval("SELECT count(*) FROM daily_usage") == 0


async def test_guest_without_a_session_passes_the_personal_precheck(stranger, send, settings):
    settings.personal_daily_limit = 0
    assert (await send(stranger)).status_code == 201


async def test_a_guests_usage_moves_on_login(client, owner, visit, pool, ready, settings):
    settings.personal_daily_limit = 3
    credentials = {"nickname": "Romeo", "password": "secret-pass"}
    async with visit() as other:
        assert (await other.post("/api/register", json=credentials)).status_code == 201
    account = await pool.fetchval("SELECT id FROM users WHERE nickname = 'Romeo'")
    await add_usage(pool, owner.id, personal=2)
    await add_usage(pool, account, personal=1)
    shoot_id = await ready()

    assert (await client.post("/api/login", json=credentials)).status_code == 200

    assert await pool.fetchval("SELECT generations FROM user_usage WHERE user_id = $1", account) == 3
    r = await order(client, shoot_id)
    assert r.status_code == 429
    assert r.json()["detail"] == "personal_limit"


async def test_uploads_stop_at_the_limit_before_the_body_is_read(client, send, settings, photos):
    settings.uploads_per_ip = 2
    assert (await send(client)).status_code == 201
    assert (await send(client, photo1=JUNK)).status_code == 422

    r = await send(client, photo1=b"x" * (25 * 1024 * 1024 + 1))

    # 429, not 413: the size is only known after reading the body.
    assert r.status_code == 429
    assert r.json()["detail"] == "ip_limit"


async def test_other_address_has_its_own_uploads(client, send, settings):
    settings.uploads_per_ip = 1
    assert (await send(client, photo1=JUNK, headers={"X-Forwarded-For": "1.1.1.1"})).status_code == 422
    assert (await send(client, photo1=JUNK, headers={"X-Forwarded-For": "1.1.1.1"})).status_code == 429
    assert (await send(client, photo1=JUNK, headers={"X-Forwarded-For": "2.2.2.2"})).status_code == 422


async def test_the_last_forwarded_address_counts(client, send, settings):
    settings.uploads_per_ip = 1
    assert (await send(client, photo1=JUNK, headers={"X-Forwarded-For": "1.1.1.1"})).status_code == 422
    assert (await send(client, photo1=JUNK, headers={"X-Forwarded-For": "9.9.9.9, 1.1.1.1"})).status_code == 429


async def test_new_day_clears_the_count(client, send, settings, monkeypatch):
    settings.uploads_per_ip = 1
    assert (await send(client, photo1=JUNK)).status_code == 422
    assert (await send(client, photo1=JUNK)).status_code == 429
    monkeypatch.setattr(limits, "_today", lambda: date(2100, 1, 1))
    assert (await send(client, photo1=JUNK)).status_code == 422


async def test_an_upload_spends_an_image_and_a_refused_one_gives_it_back(client, send, settings):
    settings.images_per_ip = 1
    assert (await send(client, photo1=JUNK)).status_code == 422
    assert (await send(client)).status_code == 201

    r = await send(client)

    assert r.status_code == 429
    assert r.json()["detail"] == "ip_limit"


async def test_images_stop_at_the_limit_and_a_repeat_spends_nothing(client, ready, settings, pool):
    settings.images_per_ip = 2
    first, second, third = await ready(), await ready(), await ready()
    assert (await order(client, first)).status_code == 202
    assert (await order(client, first)).status_code == 202
    assert (await order(client, second)).status_code == 202

    r = await order(client, third)

    assert r.status_code == 429
    assert r.json()["detail"] == "ip_limit"
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 2


async def test_refused_personal_limit_spends_no_address_count(client, ready, settings):
    settings.personal_daily_limit, settings.images_per_ip = 1, 2
    first, second = await ready(), await ready()
    assert (await order(client, first)).status_code == 202
    for _ in range(3):
        assert (await order(client, second)).json()["detail"] == "personal_limit"
    settings.personal_daily_limit = 2
    assert (await order(client, second)).status_code == 202


async def test_a_crash_in_the_order_gives_the_address_count_back(client, ready, settings, monkeypatch):
    settings.images_per_ip = 1
    shoot_id = await ready()
    real, calls = store.order_job, []

    async def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        return await real(*args)

    monkeypatch.setattr(store, "order_job", flaky)
    with pytest.raises(RuntimeError):
        await order(client, shoot_id)

    assert (await order(client, shoot_id)).status_code == 202


async def test_retry_after_a_failure_spends_the_address_count(client, ready, settings, pool):
    settings.images_per_ip = 2
    first, second = await ready(), await ready()
    assert (await order(client, first)).status_code == 202
    await pool.execute("UPDATE jobs SET status = 'failed', run_at = NULL, error = 'x'")
    assert (await order(client, first, variant=1)).status_code == 202

    assert (await order(client, second)).json()["detail"] == "ip_limit"
