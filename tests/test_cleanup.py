import asyncio
import uuid

import pytest

from loveimage import files, store, worker

pytestmark = pytest.mark.anyio


@pytest.fixture
def ctx(pool, settings):
    return worker.Ctx(pool, None, settings)


async def add_user(pool, account=False, seen_days_ago=0):
    nickname = f"user{uuid.uuid4().hex[:8]}" if account else None
    return await pool.fetchval(
        """INSERT INTO users (nickname, nickname_key, password_hash, last_seen_at)
           VALUES ($1, $1, $2, now() - make_interval(days => $3)) RETURNING id""",
        nickname,
        "hash" if account else None,
        seen_days_ago,
    )


async def add_shoot(pool, settings, user_id, status="ready", days_ago=0):
    shoot_id = await pool.fetchval(
        """INSERT INTO shoots (status, user_id, consent_at, created_at)
           VALUES ($1, $2, now(), now() - make_interval(days => $3)) RETURNING id""",
        status,
        user_id,
        days_ago,
    )
    settings.shoot_dir(shoot_id).mkdir(parents=True)
    return shoot_id


async def has_row(pool, table, row_id):
    return await pool.fetchval(f"SELECT count(*) FROM {table} WHERE id = $1", row_id) == 1


async def test_old_guest_goes_with_the_shoots(ctx, pool, settings):
    guest = await add_user(pool, seen_days_ago=31)
    shoot = await add_shoot(pool, settings, guest)

    await worker.clean_up(ctx)

    assert not await has_row(pool, "users", guest)
    assert not await has_row(pool, "shoots", shoot)
    assert not settings.shoot_dir(shoot).exists()


async def test_recent_guest_stays(ctx, pool, settings):
    guest = await add_user(pool, seen_days_ago=29)
    shoot = await add_shoot(pool, settings, guest)

    await worker.clean_up(ctx)

    assert await has_row(pool, "users", guest)
    assert await has_row(pool, "shoots", shoot)
    assert settings.shoot_dir(shoot).exists()


async def test_old_account_stays_with_a_ready_shoot(ctx, pool, settings):
    account = await add_user(pool, account=True, seen_days_ago=400)
    ready = await add_shoot(pool, settings, account, days_ago=400)

    await worker.clean_up(ctx)

    assert await has_row(pool, "users", account)
    assert await has_row(pool, "shoots", ready)
    assert settings.shoot_dir(ready).exists()


async def test_expired_sessions_go_and_live_ones_stay(ctx, pool):
    user = await add_user(pool)
    for token, ends in ((b"old", "-1 second"), (b"live", "1 day")):
        await pool.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, now() + $3::text::interval)",
            token,
            user,
            ends,
        )

    await worker.clean_up(ctx)

    assert [r["token_hash"] for r in await pool.fetch("SELECT token_hash FROM sessions")] == [b"live"]


async def test_usage_older_than_yesterday_goes(ctx, pool):
    user = await add_user(pool, account=True)
    for days_ago in (0, 1, 2):
        await pool.execute(
            "INSERT INTO user_usage (user_id, day, generations) VALUES ($1, CURRENT_DATE - $2::int, 1)", user, days_ago
        )

    await worker.clean_up(ctx)

    assert await pool.fetchval("SELECT count(*) FROM user_usage") == 2
    assert await pool.fetchval("SELECT count(*) FROM user_usage WHERE day < CURRENT_DATE - 1") == 0


@pytest.mark.parametrize("status", ["failed", "rejected"])
async def test_dead_shoots_go_after_two_days(ctx, pool, settings, status):
    account = await add_user(pool, account=True)
    old = await add_shoot(pool, settings, account, status=status, days_ago=3)
    recent = await add_shoot(pool, settings, account, status=status, days_ago=1)

    await worker.clean_up(ctx)

    assert not await has_row(pool, "shoots", old)
    assert not settings.shoot_dir(old).exists()
    assert await has_row(pool, "shoots", recent)
    assert settings.shoot_dir(recent).exists()


async def test_spending_history_stays(ctx, pool, settings):
    guest = await add_user(pool, seen_days_ago=31)
    await add_shoot(pool, settings, guest)
    await pool.execute("INSERT INTO daily_usage (day, generations, credits) VALUES (CURRENT_DATE - 40, 1, 6)")

    await worker.clean_up(ctx)

    assert await pool.fetchval("SELECT credits FROM daily_usage") == 6


async def test_one_bad_folder_does_not_stop_the_others(ctx, pool, settings, monkeypatch):
    guest = await add_user(pool, seen_days_ago=31)
    await add_shoot(pool, settings, guest)
    await add_shoot(pool, settings, guest)
    real = files.remove_folder
    calls = []

    def flaky(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(path)
        real(path)

    monkeypatch.setattr(files, "remove_folder", flaky)

    await worker.clean_up(ctx)

    assert len(calls) == 2
    assert calls[0].exists()
    assert not calls[1].exists()


async def test_cleaning_runs_at_once_and_ends_on_the_signal(ctx, pool):
    guest = await add_user(pool, seen_days_ago=31)
    stop = asyncio.Event()
    cleaning = asyncio.create_task(worker.clean_up_forever(ctx, stop))

    async with asyncio.timeout(2):
        while await has_row(pool, "users", guest):
            await asyncio.sleep(0.01)
        stop.set()
        await cleaning


async def test_a_failure_does_not_end_the_loop_with_an_error(ctx, monkeypatch):
    stop = asyncio.Event()
    calls = []

    async def broken_once(pool):
        calls.append(pool)
        if len(calls) == 1:
            raise RuntimeError("no database")
        stop.set()
        return []

    monkeypatch.setattr(store, "clean_up", broken_once)
    monkeypatch.setattr(worker, "CLEANUP_EVERY", 0)

    await asyncio.wait_for(worker.clean_up_forever(ctx, stop), 2)

    assert len(calls) == 2
