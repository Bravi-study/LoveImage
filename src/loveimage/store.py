MAX_ATTEMPTS = 3
SESSION_DAYS = 365
LOGIN_ATTEMPTS = 10
# One real 1K task took 157 s: a task cut off late is already paid for, so the wait is generous.
GENERATION_DEADLINE = 420
ACTIVE = "('queued', 'submitting', 'submitted')"


async def create_shoot(pool, shoot_id, user_id, wish):
    await pool.execute(
        """INSERT INTO shoots (id, user_id, status, wish, consent_at, run_at, deadline_at)
           VALUES ($1, $2, 'uploaded', $3, now(), now(), now() + interval '90 seconds')""",
        shoot_id,
        user_id,
        wish,
    )


async def claim_shoot(pool):
    return await pool.fetchrow(
        """UPDATE shoots SET run_at = now() + interval '120 seconds'
           WHERE id = (SELECT id FROM shoots WHERE run_at <= now()
                       ORDER BY run_at LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING id, wish, now() > deadline_at AS expired"""
    )


async def finish_shoot(pool, shoot_id, status, reject_reason=None):
    await pool.execute(
        """UPDATE shoots SET status = $2, reject_reason = $3, run_at = NULL, deadline_at = NULL
           WHERE id = $1 AND status = 'uploaded'""",
        shoot_id,
        status,
        reject_reason,
    )


async def retry_shoot_later(pool, shoot_id, seconds):
    await pool.execute(
        "UPDATE shoots SET run_at = now() + make_interval(secs => $2) WHERE id = $1 AND status = 'uploaded'",
        shoot_id,
        seconds,
    )


async def reopen_shoot(pool, shoot_id):
    """Returns False when the shoot is not in the failed state."""
    return bool(
        await pool.fetchval(
            """UPDATE shoots SET status = 'uploaded', run_at = now(), deadline_at = now() + interval '90 seconds'
               WHERE id = $1 AND status = 'failed' RETURNING true""",
            shoot_id,
        )
    )


async def get_shoot(pool, shoot_id):
    return await pool.fetchrow(
        "SELECT id, user_id, status, reject_reason, wish, scene_en, created_at FROM shoots WHERE id = $1",
        shoot_id,
    )


async def list_jobs(pool, shoot_id):
    return await pool.fetch(
        "SELECT variant, status, attempt, error, result_path FROM jobs WHERE shoot_id = $1 ORDER BY variant",
        shoot_id,
    )


async def result_path(pool, shoot_id, variant):
    return await pool.fetchval(
        "SELECT result_path FROM jobs WHERE shoot_id = $1 AND variant = $2 AND status = 'succeeded'",
        shoot_id,
        variant,
    )


async def limit_reached(pool, user_id, daily_limit, personal_limit):
    """A read-only look before the check is paid for. The real count is taken in _charge."""
    daily = await pool.fetchval("SELECT generations FROM daily_usage WHERE day = CURRENT_DATE") or 0
    if daily >= daily_limit:
        return "daily_limit"
    if user_id:
        personal = (
            await pool.fetchval(
                "SELECT generations FROM user_usage WHERE user_id = $1 AND day = CURRENT_DATE", user_id
            )
            or 0
        )
        if personal >= personal_limit:
            return "personal_limit"
    return None


class _LimitReached(Exception):
    pass


async def _charge(conn, user_id, daily_limit, personal_limit):
    personal = await conn.fetchval(
        """INSERT INTO user_usage (user_id, day, generations) SELECT $1::uuid, CURRENT_DATE, 1 WHERE $2 > 0
           ON CONFLICT (user_id, day) DO UPDATE SET generations = user_usage.generations + 1
           WHERE user_usage.generations < $2 RETURNING true""",
        user_id,
        personal_limit,
    )
    if not personal:
        raise _LimitReached("personal_limit")
    charged = await conn.fetchval(
        # The WHERE on the SELECT covers the first order of the day, which never reaches ON CONFLICT.
        """INSERT INTO daily_usage (day, generations) SELECT CURRENT_DATE, 1 WHERE $1 > 0
           ON CONFLICT (day) DO UPDATE SET generations = daily_usage.generations + 1
           WHERE daily_usage.generations < $1 RETURNING true""",
        daily_limit,
    )
    if not charged:
        raise _LimitReached("daily_limit")


async def _new_variant(conn, shoot_id, model, prompt):
    await conn.execute(
        """INSERT INTO jobs (shoot_id, variant, status, model, prompt, run_at, deadline_at)
           SELECT $1, COALESCE(max(variant), 0) + 1, 'queued', $2, $3, now(), now() + interval '60 seconds'
           FROM jobs WHERE shoot_id = $1""",
        shoot_id,
        model,
        prompt,
    )


async def approve_shoot(pool, shoot_id, scene, model, prompt, daily_limit, personal_limit):
    """Marks the shoot ready and orders its first variant in one step. A reached limit leaves it ready with no jobs."""
    async with pool.acquire() as conn, conn.transaction():
        # The owner is read here: a guest who logged in during the check has already moved to another row.
        owner = await conn.fetchval(
            """UPDATE shoots SET status = 'ready', scene_en = $2, run_at = NULL, deadline_at = NULL
               WHERE id = $1 AND status = 'uploaded' RETURNING user_id""",
            shoot_id,
            scene,
        )
        if owner is None:
            return
        try:
            async with conn.transaction():
                await _new_variant(conn, shoot_id, model, prompt)
                await _charge(conn, owner, daily_limit, personal_limit)
        except _LimitReached:
            pass


async def order_job(pool, shoot_id, user_id, variant, model, prompt, daily_limit, personal_limit):
    """No variant orders a new one, a number repeats that failed one.

    Returns created, busy, not_retryable, personal_limit or daily_limit. A refused charge rolls the job back.
    """
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                # One running job per shoot: the lock makes a double click see the first order.
                await conn.execute("SELECT 1 FROM shoots WHERE id = $1 FOR UPDATE", shoot_id)
                if await conn.fetchval(f"SELECT true FROM jobs WHERE shoot_id = $1 AND status IN {ACTIVE} LIMIT 1", shoot_id):
                    return "busy"
                if variant is None:
                    await _new_variant(conn, shoot_id, model, prompt)
                elif not await conn.fetchval(
                    """UPDATE jobs SET status = 'queued', attempt = attempt + 1,
                                       provider_job_id = NULL, error = NULL, result_path = NULL,
                                       run_at = now(), deadline_at = now() + interval '60 seconds'
                       WHERE shoot_id = $1 AND variant = $2 AND status = 'failed' AND attempt < $3
                         AND error IS DISTINCT FROM 'submit_unknown' RETURNING true""",
                    shoot_id,
                    variant,
                    MAX_ATTEMPTS,
                ):
                    return "not_retryable"
                await _charge(conn, user_id, daily_limit, personal_limit)
        except _LimitReached as e:
            return e.args[0]
    return "created"


async def claim_job(pool):
    return await pool.fetchrow(
        """UPDATE jobs SET run_at = now() + interval '120 seconds'
           WHERE id = (SELECT id FROM jobs WHERE run_at <= now()
                       ORDER BY run_at LIMIT 1 FOR UPDATE SKIP LOCKED)
           RETURNING id, shoot_id, variant, status, attempt, provider_job_id, prompt, model,
                     now() > deadline_at AS expired,
                     EXTRACT(EPOCH FROM deadline_at - now())::float8 AS seconds_left"""
    )


async def _job_update(pool, row, old_status, assignments, *args):
    """The status and attempt fence: False means another attempt took over and the caller must stop."""
    return bool(
        await pool.fetchval(
            f"UPDATE jobs SET {assignments} WHERE id = $1 AND attempt = $2 AND status = $3 RETURNING true",
            row["id"],
            row["attempt"],
            old_status,
            *args,
        )
    )


async def job_to_submitting(pool, row):
    return await _job_update(pool, row, "queued", "status = 'submitting'")


async def job_back_to_queue(pool, row, old_status, seconds):
    return await _job_update(pool, row, old_status, "status = 'queued', run_at = now() + make_interval(secs => $4)", seconds)


async def job_submitted(pool, row, task_id, credits):
    async with pool.acquire() as conn, conn.transaction():
        updated = await _job_update(
            conn,
            row,
            "submitting",
            """status = 'submitted', provider_job_id = $4, credits = credits + $5,
               run_at = now() + interval '3 seconds', deadline_at = now() + make_interval(secs => $6)""",
            task_id,
            credits,
            GENERATION_DEADLINE,
        )
        if updated:
            # generations stays 0 here: a job ordered before midnight must not use up the new day's limit.
            await conn.execute(
                """INSERT INTO daily_usage (day, generations, credits) VALUES (CURRENT_DATE, 0, $1)
                   ON CONFLICT (day) DO UPDATE SET credits = daily_usage.credits + EXCLUDED.credits""",
                credits,
            )
    return updated


async def job_poll_later(pool, row, seconds):
    return await _job_update(pool, row, "submitted", "run_at = now() + make_interval(secs => $4)", seconds)


async def job_succeeded(pool, row, path):
    return await _job_update(pool, row, "submitted", "status = 'succeeded', result_path = $4, run_at = NULL", path)


async def job_failed(pool, row, old_status, error):
    async with pool.acquire() as conn, conn.transaction():
        failed = await _job_update(conn, row, old_status, "status = 'failed', error = $4, run_at = NULL", error)
        # The site's own account refused, nothing was made: an empty balance must not use up the day's limit.
        if failed and error in ("no_credits", "provider_auth"):
            await conn.execute(
                "UPDATE daily_usage SET generations = generations - 1 WHERE day = CURRENT_DATE AND generations > 0"
            )
            await conn.execute(
                """UPDATE user_usage SET generations = generations - 1
                   WHERE day = CURRENT_DATE AND generations > 0
                     AND user_id = (SELECT user_id FROM shoots WHERE id = $1)""",
                row["shoot_id"],
            )
    return failed


async def job_crashed(pool, row):
    """A submitted task is paid for and its id is kept, so until its deadline the lease just brings it back for another poll."""
    await pool.execute(
        f"""UPDATE jobs SET status = 'failed', run_at = NULL,
                            error = CASE WHEN status = 'submitting' THEN 'submit_unknown' ELSE 'internal' END
            WHERE id = $1 AND attempt = $2 AND status IN {ACTIVE}
              AND (status <> 'submitted' OR deadline_at <= now())""",
        row["id"],
        row["attempt"],
    )


async def delete_shoot(pool, shoot_id):
    """Returns deleted, busy or missing. The row lock stops an order slipping in between the check and the delete."""
    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT true FROM shoots WHERE id = $1 FOR UPDATE", shoot_id):
            return "missing"
        if await conn.fetchval(f"SELECT true FROM jobs WHERE shoot_id = $1 AND status IN {ACTIVE} LIMIT 1", shoot_id):
            return "busy"
        await conn.execute("DELETE FROM shoots WHERE id = $1", shoot_id)
    return "deleted"


async def create_guest(pool, token_hash):
    return await pool.fetchval(
        """WITH guest AS (INSERT INTO users DEFAULT VALUES RETURNING id),
                session AS (INSERT INTO sessions (token_hash, user_id, expires_at)
                            SELECT $1::bytea, id, now() + make_interval(days => $2) FROM guest)
           SELECT id FROM guest""",
        token_hash,
        SESSION_DAYS,
    )


async def session_user(pool, token_hash):
    """The user behind a live session. Writes last_seen_at at most once a day, so polling stays cheap."""
    return await pool.fetchval(
        """WITH found AS (SELECT user_id FROM sessions WHERE token_hash = $1 AND expires_at > now()),
                seen AS (UPDATE users SET last_seen_at = now()
                         WHERE id = (SELECT user_id FROM found) AND last_seen_at < now() - interval '1 day')
           SELECT user_id FROM found""",
        token_hash,
    )


async def nickname(pool, user_id):
    return await pool.fetchval("SELECT nickname FROM users WHERE id = $1", user_id)


async def delete_session(pool, token_hash):
    await pool.execute("DELETE FROM sessions WHERE token_hash = $1", token_hash)


async def _replace_session(conn, old_token_hash, token_hash, user_id):
    await conn.execute("DELETE FROM sessions WHERE token_hash = $1", old_token_hash)
    await conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, now() + make_interval(days => $3))",
        token_hash,
        user_id,
        SESSION_DAYS,
    )


async def register(pool, old_token_hash, nickname, nickname_key, password_hash, token_hash):
    """A guest's own row becomes the account, so the shoots stay put. Otherwise a new account is created."""
    async with pool.acquire() as conn, conn.transaction():
        user_id = await conn.fetchval(
            """UPDATE users SET nickname = $2, nickname_key = $3, password_hash = $4
               WHERE nickname IS NULL
                 AND id = (SELECT user_id FROM sessions WHERE token_hash = $1 AND expires_at > now())
               RETURNING id""",
            old_token_hash,
            nickname,
            nickname_key,
            password_hash,
        )
        if user_id is None:
            user_id = await conn.fetchval(
                "INSERT INTO users (nickname, nickname_key, password_hash) VALUES ($1, $2, $3) RETURNING id",
                nickname,
                nickname_key,
                password_hash,
            )
        await _replace_session(conn, old_token_hash, token_hash, user_id)


async def find_account(pool, nickname_key):
    return await pool.fetchrow(
        "SELECT id, nickname, password_hash, locked_until > now() AS locked FROM users WHERE nickname_key = $1",
        nickname_key,
    )


async def login_failed(pool, user_id):
    # shortcut: anyone who knows a nickname can lock it for 15 minutes, add a per-address count if someone does it.
    await pool.execute(
        """UPDATE users
           SET failed_logins = CASE WHEN failed_logins + 1 >= $2 THEN 0 ELSE failed_logins + 1 END,
               locked_until = CASE WHEN failed_logins + 1 >= $2 THEN now() + interval '15 minutes' ELSE locked_until END
           WHERE id = $1""",
        user_id,
        LOGIN_ATTEMPTS,
    )


async def login_succeeded(pool, account_id, old_token_hash, token_hash):
    """Clears the failures. A guest session in this browser hands its shoots and today's count to the account."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = $1", account_id)
        guest = await conn.fetchval(
            """SELECT u.id FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = $1 AND s.expires_at > now() AND u.nickname IS NULL
               FOR UPDATE OF u""",
            old_token_hash,
        )
        if guest:
            await conn.execute("UPDATE shoots SET user_id = $2 WHERE user_id = $1", guest, account_id)
            await conn.execute(
                """INSERT INTO user_usage (user_id, day, generations)
                   SELECT $2::uuid, day, generations FROM user_usage WHERE user_id = $1 AND day = CURRENT_DATE
                   ON CONFLICT (user_id, day) DO UPDATE SET generations = user_usage.generations + EXCLUDED.generations""",
                guest,
                account_id,
            )
            await conn.execute("DELETE FROM users WHERE id = $1", guest)
        await _replace_session(conn, old_token_hash, token_hash, account_id)


async def user_shoots(pool, user_id, before, limit):
    # Without rejected: their photos are already deleted, the page that said why was enough.
    # shortcut: the cursor is created_at alone, so shoots with the same microsecond can drop out at a page edge,
    # upgrade to a (created_at, id) cursor if one person ever uploads in bulk
    return await pool.fetch(
        f"""SELECT s.id, s.created_at, s.status, s.wish,
                   count(j.id) FILTER (WHERE j.status = 'succeeded')::int AS images,
                   max(j.variant) FILTER (WHERE j.status = 'succeeded') AS latest_variant,
                   COALESCE(bool_or(j.status IN {ACTIVE}), false) AS active
            FROM shoots s
            LEFT JOIN jobs j ON j.shoot_id = s.id
            WHERE s.user_id = $1 AND s.status <> 'rejected' AND ($2::timestamptz IS NULL OR s.created_at < $2)
            GROUP BY s.id
            ORDER BY s.created_at DESC
            LIMIT $3""",
        user_id,
        before,
        limit,
    )


async def clean_up(pool):
    """Deletes what has expired and returns the shoots whose folders should go too."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        await conn.execute("DELETE FROM user_usage WHERE day < CURRENT_DATE - 1")
        guests = await conn.fetch(
            # shortcut: last_seen_at is written at most once a day, so a guest can go up to a day before 30 days from
            # their real last visit, widen the interval or write it on each visit when someone loses shoots this way.
            # The SELECT still sees the shoots: the cascade fires after the statement.
            """WITH gone AS (DELETE FROM users
                             WHERE nickname IS NULL AND last_seen_at < now() - interval '30 days' RETURNING id)
               SELECT s.id FROM shoots s JOIN gone ON s.user_id = gone.id"""
        )
        dead = await conn.fetch(
            """DELETE FROM shoots WHERE status IN ('failed', 'rejected') AND created_at < now() - interval '2 days'
               RETURNING id"""
        )
    return [r["id"] for r in (*guests, *dead)]
