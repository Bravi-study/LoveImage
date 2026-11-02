import asyncio
import threading
import time
import uuid

import pytest

from conftest import SCENE
from loveimage import accounts, store

pytestmark = pytest.mark.anyio

CREDS = {"nickname": "Ромео", "password": "secret-pass"}


async def add_work(pool, user_id):
    return await pool.fetchval(
        "INSERT INTO shoots (status, user_id, consent_at) VALUES ('ready', $1, now()) RETURNING id", user_id
    )


async def owner_of(pool, shoot_id):
    return await pool.fetchval("SELECT user_id FROM shoots WHERE id = $1", shoot_id)


async def add_usage(pool, user_id, generations, days_ago=0):
    await pool.execute(
        "INSERT INTO user_usage (user_id, day, generations) VALUES ($1, CURRENT_DATE - $2::int, $3)",
        user_id,
        days_ago,
        generations,
    )


@pytest.fixture
async def account(visit, pool):
    async with visit() as client:
        assert (await client.post("/api/register", json=CREDS)).status_code == 201
    return await pool.fetchval("SELECT id FROM users WHERE nickname_key = 'ромео'")


def test_password_hash_round_trip():
    stored = accounts.hash_password("secret-pass")
    assert accounts.verify_password("secret-pass", stored)
    assert not accounts.verify_password("other-pass", stored)


def test_password_hash_is_salted_and_keeps_its_parameters():
    one, two = accounts.hash_password("secret-pass"), accounts.hash_password("secret-pass")
    assert one != two
    assert one.startswith("scrypt$32768$8$1$")
    assert "secret-pass" not in one


async def test_guest_becomes_the_account_and_keeps_the_shoots(client, owner, pool):
    work = await add_work(pool, owner.id)

    r = await client.post("/api/register", json=CREDS)

    assert r.status_code == 201
    assert r.json() == {"nickname": "Ромео"}
    assert await pool.fetchval("SELECT count(*) FROM users") == 1
    row = await pool.fetchrow("SELECT id, nickname, nickname_key FROM users")
    assert tuple(row) == (owner.id, "Ромео", "ромео")
    assert await owner_of(pool, work) == owner.id
    assert await store.session_user(pool, accounts.token_hash(owner.token)) is None
    assert (await client.get("/api/me")).json() == {"nickname": "Ромео"}


async def test_register_without_a_cookie_creates_an_account(stranger, pool):
    assert (await stranger.post("/api/register", json=CREDS)).status_code == 201
    assert await pool.fetchval("SELECT count(*) FROM users WHERE nickname_key = 'ромео'") == 1
    assert (await stranger.get("/api/me")).json() == {"nickname": "Ромео"}


async def test_register_with_an_accounts_cookie_creates_another(stranger, pool):
    await stranger.post("/api/register", json=CREDS)
    r = await stranger.post("/api/register", json={**CREDS, "nickname": "Джульетта"})
    assert r.status_code == 201
    assert await pool.fetchval("SELECT count(*) FROM users WHERE nickname IS NOT NULL") == 2
    assert (await stranger.get("/api/me")).json() == {"nickname": "Джульетта"}


async def test_taken_nickname_is_refused_in_any_case(stranger, visit):
    await stranger.post("/api/register", json=CREDS)
    async with visit() as other:
        r = await other.post("/api/register", json={**CREDS, "nickname": "РОМЕО"})
    assert r.status_code == 409
    assert r.json()["detail"] == "nickname_taken"


async def test_parallel_registrations_leave_one_winner(visit):
    async with visit() as one, visit() as two:
        answers = await asyncio.gather(one.post("/api/register", json=CREDS), two.post("/api/register", json=CREDS))
    assert sorted(r.status_code for r in answers) == [201, 409]


async def test_refused_registration_leaves_the_guest_as_it_was(client, owner, stranger, pool):
    await stranger.post("/api/register", json=CREDS)
    assert (await client.post("/api/register", json=CREDS)).status_code == 409
    assert await pool.fetchval("SELECT nickname FROM users WHERE id = $1", owner.id) is None
    assert await store.session_user(pool, accounts.token_hash(owner.token)) == owner.id


@pytest.mark.parametrize("nickname", ["ab", "a" * 21, "имя с пробелом", "bad!", "Ромео\n"])
async def test_bad_nickname_is_refused(stranger, nickname):
    assert (await stranger.post("/api/register", json={**CREDS, "nickname": nickname})).status_code == 422


@pytest.mark.parametrize("password", ["12345", "x" * 129])
async def test_bad_password_is_refused(stranger, password):
    assert (await stranger.post("/api/register", json={**CREDS, "password": password})).status_code == 422


@pytest.mark.parametrize("nickname", ["Ёжик", "abc", "a1Б"])
async def test_good_nickname_is_accepted(stranger, nickname):
    assert (await stranger.post("/api/register", json={**CREDS, "nickname": nickname})).status_code == 201


async def test_lone_surrogate_in_the_password_is_refused_quietly(stranger):
    r = await stranger.post(
        "/api/register",
        content=b'{"nickname":"Romeo","password":"\\ud800secret"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}


async def test_wrong_password_and_unknown_nickname_answer_the_same(stranger, account):
    wrong = await stranger.post("/api/login", json={**CREDS, "password": "wrong-pass"})
    unknown = await stranger.post("/api/login", json={**CREDS, "nickname": "Никого"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "bad_credentials"}


async def test_login_ignores_case_and_answers_with_the_stored_spelling(stranger, account):
    r = await stranger.post("/api/login", json={**CREDS, "nickname": "ромео"})
    assert r.status_code == 200
    assert r.json() == {"nickname": "Ромео"}
    assert (await stranger.get("/api/me")).json() == {"nickname": "Ромео"}


async def test_ten_failures_lock_even_the_right_password(stranger, account, monkeypatch):
    for _ in range(10):
        assert (await stranger.post("/api/login", json={**CREDS, "password": "wrong-pass"})).status_code == 401

    def refuse(*args):
        raise AssertionError("a locked login must not spend a hash")

    monkeypatch.setattr(accounts, "verify_password", refuse)
    r = await stranger.post("/api/login", json=CREDS)
    assert r.status_code == 429
    assert r.json()["detail"] == "locked"


async def test_a_burst_of_guesses_cannot_get_past_the_lock(stranger, account, pool):
    # One client: the hashing queue lives in the app, so a burst has to meet itself there.
    wrong = {**CREDS, "password": "wrong-pass"}
    answers = await asyncio.gather(
        *(stranger.post("/api/login", json=wrong) for _ in range(25)), stranger.post("/api/login", json=CREDS)
    )
    # Two hash slots allow one guess past the tenth, so 11 is the honest bound.
    assert [r.status_code for r in answers].count(401) <= 11
    assert answers[-1].status_code == 429
    assert await pool.fetchval("SELECT locked_until > now() FROM users WHERE id = $1", account)


async def test_lock_ends_after_its_time(stranger, account, pool):
    await pool.execute("UPDATE users SET locked_until = now() - interval '1 second' WHERE id = $1", account)
    assert (await stranger.post("/api/login", json=CREDS)).status_code == 200


async def test_success_clears_the_failures(stranger, account, pool):
    await pool.execute("UPDATE users SET failed_logins = 9 WHERE id = $1", account)
    assert (await stranger.post("/api/login", json=CREDS)).status_code == 200
    assert await pool.fetchval("SELECT failed_logins FROM users WHERE id = $1", account) == 0


async def test_login_with_a_guest_cookie_moves_the_shoots(client, owner, account, pool):
    work = await add_work(pool, owner.id)
    await add_usage(pool, owner.id, 2)
    await add_usage(pool, owner.id, 4, days_ago=1)
    await add_usage(pool, account, 3)

    assert (await client.post("/api/login", json=CREDS)).status_code == 200

    assert await owner_of(pool, work) == account
    assert await pool.fetchval("SELECT generations FROM user_usage WHERE user_id = $1 AND day = CURRENT_DATE", account) == 5
    assert await pool.fetchval("SELECT count(*) FROM user_usage WHERE user_id = $1", account) == 1
    assert await pool.fetchval("SELECT count(*) FROM users WHERE id = $1", owner.id) == 0
    assert await store.session_user(pool, accounts.token_hash(owner.token)) is None


async def test_login_with_another_accounts_cookie_moves_nothing(visit, account, pool):
    async with visit() as other:
        await other.post("/api/register", json={**CREDS, "nickname": "Ёжик"})
        other_id = await pool.fetchval("SELECT id FROM users WHERE nickname = 'Ёжик'")
        work = await add_work(pool, other_id)
        assert (await other.post("/api/login", json=CREDS)).status_code == 200
    assert await owner_of(pool, work) == other_id
    assert await pool.fetchval("SELECT count(*) FROM users") == 2


async def test_wrong_password_with_a_guest_cookie_moves_nothing(client, owner, account, pool):
    work = await add_work(pool, owner.id)
    assert (await client.post("/api/login", json={**CREDS, "password": "wrong-pass"})).status_code == 401
    assert await owner_of(pool, work) == owner.id
    assert await store.session_user(pool, accounts.token_hash(owner.token)) == owner.id


async def test_logout_ends_this_browsers_session_only(visit, account):
    async with visit() as one, visit() as two:
        await one.post("/api/login", json=CREDS)
        await two.post("/api/login", json=CREDS)
        r = await one.post("/api/logout")
        assert r.status_code == 204
        assert accounts.COOKIE not in one.cookies
        assert (await one.get("/api/me")).json() == {"nickname": None}
        assert (await two.get("/api/me")).json() == {"nickname": "Ромео"}


async def test_logged_out_token_stops_working(visit, account):
    async with visit() as one:
        await one.post("/api/login", json=CREDS)
        token = one.cookies[accounts.COOKIE]
        await one.post("/api/logout")
    async with visit(token) as old:
        assert (await old.get("/api/me")).json() == {"nickname": None}


async def test_logout_without_a_cookie_is_fine(stranger):
    r = await stranger.post("/api/logout")
    assert r.status_code == 204
    # A cross-site form post carries no cookie, and must not be able to delete one.
    assert "set-cookie" not in r.headers


async def test_cookie_flags(stranger):
    header = (await stranger.post("/api/register", json=CREDS)).headers["set-cookie"]
    for part in ("HttpOnly", "Max-Age=31536000", "Path=/", "SameSite=lax"):
        assert part in header
    assert "Secure" not in header


async def test_cookie_is_secure_behind_https(stranger):
    r = await stranger.post("/api/register", json=CREDS, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["set-cookie"]


async def test_me_creates_nobody(stranger, visit, pool):
    assert (await stranger.get("/api/me")).json() == {"nickname": None}
    async with visit("garbage") as junk:
        assert (await junk.get("/api/me")).json() == {"nickname": None}
    assert await pool.fetchval("SELECT count(*) FROM users") == 0


async def test_me_of_a_guest_has_no_nickname(client):
    r = await client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"nickname": None}


async def test_only_the_tokens_hash_is_stored(stranger, pool):
    await stranger.post("/api/register", json=CREDS)
    token = stranger.cookies[accounts.COOKIE]
    assert await pool.fetchval("SELECT token_hash FROM sessions") == accounts.token_hash(token)


async def test_expired_session_counts_as_none(stranger, pool):
    await stranger.post("/api/register", json=CREDS)
    await pool.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert (await stranger.get("/api/me")).json() == {"nickname": None}


@pytest.mark.parametrize(("hours", "moved"), [(23, False), (25, True)])
async def test_last_seen_is_written_at_most_once_a_day(client, owner, pool, hours, moved):
    before = await pool.fetchval(
        "UPDATE users SET last_seen_at = now() - make_interval(hours => $2) WHERE id = $1 RETURNING last_seen_at",
        owner.id,
        hours,
    )
    await client.get("/api/me")
    after = await pool.fetchval("SELECT last_seen_at FROM users WHERE id = $1", owner.id)
    assert (after != before) is moved


async def test_at_most_two_hashes_run_at_once(stranger, monkeypatch):
    lock = threading.Lock()
    running = peak = 0

    def slow(password):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return "scrypt$1$1$1$00$00"

    monkeypatch.setattr(accounts, "hash_password", slow)
    await asyncio.gather(
        *(stranger.post("/api/register", json={**CREDS, "nickname": f"user{i}"}) for i in range(6))
    )
    assert peak == 2


async def test_a_guests_shoot_follows_the_account_to_another_browser(stranger, visit, send, pool, settings):
    shoot_id = (await send(stranger)).json()["id"]
    user_id = await pool.fetchval("SELECT user_id FROM shoots")
    await store.approve_shoot(pool, uuid.UUID(shoot_id), SCENE, "m", "p", 100, 100)
    # The queue worker is not running here: finish the job by hand.
    relative = f"shoots/{shoot_id}/v1-1.jpg"
    (settings.data_dir / relative).write_bytes(b"\xff\xd8result")
    await pool.execute("UPDATE jobs SET status = 'succeeded', run_at = NULL, result_path = $1", relative)

    assert (await stranger.post("/api/register", json=CREDS)).status_code == 201
    assert (await stranger.post("/api/logout")).status_code == 204
    assert (await stranger.get(f"/api/shoots/{shoot_id}")).status_code == 404

    async with visit() as other:
        assert (await other.post("/api/login", json=CREDS)).status_code == 200
        cards = (await other.get("/api/shoots")).json()
        assert [(c["id"], c["images"]) for c in cards] == [(shoot_id, 1)]
        assert (await other.get(f"/api/shoots/{shoot_id}")).status_code == 200
        assert (await other.get(cards[0]["cover_url"])).status_code == 200
