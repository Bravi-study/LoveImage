import pytest

pytestmark = pytest.mark.anyio


async def add_job(pool, shoot_id, variant, status="succeeded"):
    await pool.execute(
        """INSERT INTO jobs (shoot_id, variant, status, model, prompt, run_at)
           VALUES ($1, $2, $3::text, 'm', 'p', CASE WHEN $3::text IN ('queued', 'submitting', 'submitted') THEN now() END)""",
        shoot_id,
        variant,
        status,
    )


async def first_card(client):
    return (await client.get("/api/shoots")).json()[0]


async def test_only_own_shoots_are_listed(client, ready, new_guest, pool):
    mine = await ready()
    other = await new_guest()
    await ready(user_id=other.id)
    assert [c["id"] for c in (await client.get("/api/shoots")).json()] == [str(mine)]


async def test_pages_go_newest_first_without_overlap(client, ready, pool):
    ids = []
    for minutes in range(35):
        shoot_id = await ready()
        await pool.execute(
            "UPDATE shoots SET created_at = now() - make_interval(mins => $2) WHERE id = $1", shoot_id, minutes
        )
        ids.append(str(shoot_id))

    first = (await client.get("/api/shoots")).json()
    second = (await client.get("/api/shoots", params={"before": first[-1]["created_at"]})).json()

    assert [c["id"] for c in first] == ids[:30]
    assert [c["id"] for c in second] == ids[30:]


async def test_rejected_are_left_out_and_the_others_stay(client, ready, uploaded, pool):
    rejected, kept = await ready(), await uploaded()
    await pool.execute("UPDATE shoots SET status = 'rejected', reject_reason = 'minor' WHERE id = $1", rejected)
    assert [c["id"] for c in (await client.get("/api/shoots")).json()] == [str(kept)]


async def test_cover_is_the_latest_finished_variant(client, ready, pool):
    shoot_id = await ready()
    card = await first_card(client)
    assert (card["cover_url"], card["images"], card["active"]) == (None, 0, False)

    await add_job(pool, shoot_id, 1)
    await add_job(pool, shoot_id, 2)

    card = await first_card(client)
    assert card["cover_url"] == f"/api/shoots/{shoot_id}/results/2"
    assert card["images"] == 2


async def test_failed_image_does_not_count(client, ready, pool):
    await add_job(pool, await ready(), 1, "failed")
    card = await first_card(client)
    assert (card["cover_url"], card["images"], card["active"]) == (None, 0, False)


@pytest.mark.parametrize("status", ["queued", "submitting", "submitted"])
async def test_active_while_a_job_runs(client, ready, pool, status):
    await add_job(pool, await ready(), 1, status)
    assert (await first_card(client))["active"] is True


async def test_no_cookie_means_an_empty_list(stranger, pool):
    r = await stranger.get("/api/shoots")
    assert r.status_code == 200
    assert r.json() == []
    assert await pool.fetchval("SELECT count(*) FROM users") == 0


async def test_owner_deletes_a_shoot(client, ready, pool, settings):
    shoot_id = await ready()
    assert (await client.delete(f"/api/shoots/{shoot_id}")).status_code == 204
    assert await pool.fetchval("SELECT count(*) FROM shoots WHERE id = $1", shoot_id) == 0
    assert not settings.shoot_dir(shoot_id).exists()


@pytest.mark.parametrize("status", ["queued", "submitting", "submitted"])
async def test_delete_is_refused_while_a_job_runs(client, ready, pool, settings, status):
    shoot_id = await ready()
    await add_job(pool, shoot_id, 1, status)
    r = await client.delete(f"/api/shoots/{shoot_id}")
    assert r.status_code == 409
    assert r.json()["detail"] == "busy"
    assert await pool.fetchval("SELECT count(*) FROM shoots") == 1
    assert settings.shoot_dir(shoot_id).exists()


async def test_deleting_does_not_give_the_limit_back(client, ready, settings, pool):
    settings.personal_daily_limit = 1
    shoot_id = await ready()
    assert (await client.post(f"/api/shoots/{shoot_id}/jobs", json={})).status_code == 202
    await pool.execute("UPDATE jobs SET status = 'succeeded', run_at = NULL")

    assert (await client.delete(f"/api/shoots/{shoot_id}")).status_code == 204

    assert await pool.fetchval("SELECT generations FROM user_usage") == 1
    r = await client.post(f"/api/shoots/{await ready()}/jobs", json={})
    assert r.status_code == 429
