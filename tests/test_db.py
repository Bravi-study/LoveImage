import asyncio

import asyncpg
import pytest

from loveimage import db, store

pytestmark = pytest.mark.anyio

ALL_TABLES = "schema_migrations, jobs, shoots, daily_usage, user_usage, sessions, users"


@pytest.fixture
async def user(pool):
    return await pool.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")


async def _shoot(pool, user, status="ready"):
    return await pool.fetchval(
        "INSERT INTO shoots (status, user_id, consent_at) VALUES ($1, $2, now()) RETURNING id", status, user
    )


async def test_migrate_twice_changes_nothing(pool):
    before = await pool.fetchval("SELECT count(*) FROM schema_migrations")
    await db.migrate(pool)
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == before
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations WHERE name = '001_init.sql'") == 1


async def test_parallel_migrations_on_an_empty_database_all_succeed(pool):
    await pool.execute(f"DROP TABLE {ALL_TABLES}")
    await asyncio.gather(*(db.migrate(pool) for _ in range(4)))
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations WHERE name = '001_init.sql'") == 1


async def test_migration_waits_for_the_advisory_lock(pool):
    async with pool.acquire() as holder:
        await holder.execute("SELECT pg_advisory_lock($1::bigint)", db.MIGRATION_LOCK)
        migrating = asyncio.create_task(db.migrate(pool))
        try:
            async with asyncio.timeout(3):
                while not await pool.fetchval(
                    """SELECT count(*) FROM pg_locks l JOIN pg_database d ON d.oid = l.database
                       WHERE l.locktype = 'advisory' AND NOT l.granted AND d.datname = current_database()"""
                ):
                    await asyncio.sleep(0.01)
            assert not migrating.done()
        finally:
            await holder.execute("SELECT pg_advisory_unlock($1::bigint)", db.MIGRATION_LOCK)
    async with asyncio.timeout(3):
        await migrating


async def test_uploaded_shoot_must_have_run_at(pool, user):
    with pytest.raises(asyncpg.CheckViolationError):
        await _shoot(pool, user, status="uploaded")


async def test_finished_shoot_must_not_have_run_at(pool, user):
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO shoots (status, user_id, consent_at, run_at) VALUES ('ready', $1, now(), now())", user
        )


async def test_shoot_must_have_consent_and_an_owner(pool, user):
    with pytest.raises(asyncpg.NotNullViolationError):
        await pool.execute("INSERT INTO shoots (status, user_id) VALUES ('ready', $1)", user)
    with pytest.raises(asyncpg.NotNullViolationError):
        await pool.execute("INSERT INTO shoots (status, consent_at) VALUES ('ready', now())")


@pytest.mark.parametrize("status", ["queued", "submitting", "submitted"])
async def test_active_job_must_have_run_at(pool, user, status):
    shoot = await _shoot(pool, user)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO jobs (shoot_id, variant, status, model, prompt) VALUES ($1, 1, $2, 'm', 'p')", shoot, status
        )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_finished_job_must_not_have_run_at(pool, user, status):
    shoot = await _shoot(pool, user)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO jobs (shoot_id, variant, status, model, prompt, run_at) VALUES ($1, 1, $2, 'm', 'p', now())",
            shoot,
            status,
        )


async def test_one_job_per_variant(pool, user):
    shoot = await _shoot(pool, user)
    insert = "INSERT INTO jobs (shoot_id, variant, status, model, prompt, run_at) VALUES ($1, 1, 'queued', 'm', 'p', now())"
    await pool.execute(insert, shoot)
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(insert, shoot)


async def test_unknown_statuses_are_rejected(pool, user):
    shoot = await _shoot(pool, user)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO jobs (shoot_id, variant, status, model, prompt) VALUES ($1, 1, 'lost', 'm', 'p')", shoot
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await _shoot(pool, user, status="analyzing")


async def test_user_is_a_guest_or_a_full_account(pool):
    await pool.execute("INSERT INTO users DEFAULT VALUES")
    await pool.execute("INSERT INTO users (nickname, nickname_key, password_hash) VALUES ('Ромео', 'ромео', 'x')")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("INSERT INTO users (nickname) VALUES ('Половина')")


async def test_nickname_key_is_unique_and_guests_do_not_collide(pool):
    await pool.execute("INSERT INTO users DEFAULT VALUES")
    await pool.execute("INSERT INTO users DEFAULT VALUES")
    await pool.execute("INSERT INTO users (nickname, nickname_key, password_hash) VALUES ('Ромео', 'ромео', 'x')")
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute("INSERT INTO users (nickname, nickname_key, password_hash) VALUES ('РОМЕО', 'ромео', 'y')")


async def test_deleting_a_user_takes_everything_of_theirs(pool, user):
    await pool.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, now() + interval '1 day')", b"hash", user
    )
    await pool.execute("INSERT INTO user_usage (user_id, day, generations) VALUES ($1, CURRENT_DATE, 1)", user)
    shoot = await _shoot(pool, user)
    await pool.execute("INSERT INTO jobs (shoot_id, variant, status, model, prompt) VALUES ($1, 1, 'failed', 'm', 'p')", shoot)

    await pool.execute("DELETE FROM users WHERE id = $1", user)

    for table in ("sessions", "user_usage", "shoots", "jobs"):
        assert await pool.fetchval(f"SELECT count(*) FROM {table}") == 0


async def test_parallel_shoot_claims_get_different_rows(pool, uploaded):
    made = {await uploaded() for _ in range(5)}
    rows = await asyncio.gather(*(store.claim_shoot(pool) for _ in range(5)))
    assert {r["id"] for r in rows} == made


async def test_parallel_job_claims_get_different_rows(pool, queued):
    for _ in range(5):
        await queued()
    rows = await asyncio.gather(*(store.claim_job(pool) for _ in range(5)))
    assert len({r["id"] for r in rows}) == 5


async def test_a_locked_shoot_is_skipped_not_waited_for(pool, uploaded):
    first, second = await uploaded(), await uploaded()
    await pool.execute("UPDATE shoots SET run_at = now() - interval '1 hour' WHERE id = $1", first)
    async with pool.acquire() as holder, holder.transaction():
        await holder.execute("SELECT 1 FROM shoots WHERE id = $1 FOR UPDATE", first)
        async with asyncio.timeout(2):
            assert (await store.claim_shoot(pool))["id"] == second


async def test_a_locked_job_is_skipped_not_waited_for(pool, queued):
    await queued()
    await queued()
    locked, other = [r["id"] for r in await pool.fetch("SELECT id FROM jobs ORDER BY id")]
    await pool.execute("UPDATE jobs SET run_at = now() - interval '1 hour' WHERE id = $1", locked)
    async with pool.acquire() as holder, holder.transaction():
        await holder.execute("SELECT 1 FROM jobs WHERE id = $1 FOR UPDATE", locked)
        async with asyncio.timeout(2):
            assert (await store.claim_job(pool))["id"] == other
