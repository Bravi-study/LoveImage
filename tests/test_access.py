import uuid

import pytest

from conftest import make_jpeg
from loveimage import files

pytestmark = pytest.mark.anyio

NOT_FOUND = {"detail": "not_found"}
READS = ["", "/photos/1", "/photos/2", "/results/1"]


@pytest.fixture
async def shoot(pool, queued, settings):
    """The owner's shoot with both photos and one finished picture."""
    shoot_id = await queued()
    relative = f"shoots/{shoot_id}/v1-1.jpg"
    (settings.data_dir / relative).write_bytes(make_jpeg())
    await pool.execute("UPDATE jobs SET status = 'succeeded', run_at = NULL, result_path = $1", relative)
    return shoot_id


@pytest.fixture(params=["no session", "dead cookie", "another guest", "an account"])
async def someone_else(request, visit, new_guest):
    token = {"no session": None, "dead cookie": "dead-token"}.get(request.param)
    if request.param == "another guest":
        token = (await new_guest()).token
    async with visit(token) as client:
        if request.param == "an account":
            r = await client.post("/api/register", json={"nickname": "Tybalt", "password": "secret-pass"})
            assert r.status_code == 201
        yield client


@pytest.mark.parametrize("tail", READS)
async def test_the_owner_reads_everything(client, shoot, tail):
    assert (await client.get(f"/api/shoots/{shoot}{tail}")).status_code == 200


@pytest.mark.parametrize("tail", READS)
async def test_nobody_else_reads_anything(someone_else, shoot, tail):
    r = await someone_else.get(f"/api/shoots/{shoot}{tail}")
    assert r.status_code == 404
    assert r.json() == NOT_FOUND


@pytest.mark.parametrize("tail", READS)
async def test_a_strangers_shoot_answers_like_a_missing_one(someone_else, shoot, tail):
    theirs = await someone_else.get(f"/api/shoots/{shoot}{tail}")
    missing = await someone_else.get(f"/api/shoots/{uuid.uuid4()}{tail}")
    assert (theirs.status_code, theirs.content) == (missing.status_code, missing.content)


async def test_nobody_else_orders_a_job(someone_else, shoot, pool):
    r = await someone_else.post(f"/api/shoots/{shoot}/jobs", json={})
    assert r.status_code == 404
    assert r.json() == NOT_FOUND
    assert await pool.fetchval("SELECT count(*) FROM jobs") == 1


async def test_nobody_else_repeats_the_analysis(someone_else, shoot, pool):
    await pool.execute("UPDATE shoots SET status = 'failed' WHERE id = $1", shoot)
    r = await someone_else.post(f"/api/shoots/{shoot}/analysis")
    assert r.status_code == 404
    assert r.json() == NOT_FOUND
    assert await pool.fetchval("SELECT status FROM shoots") == "failed"


async def test_nobody_else_deletes(someone_else, shoot, pool, settings):
    r = await someone_else.delete(f"/api/shoots/{shoot}")
    assert r.status_code == 404
    assert r.json() == NOT_FOUND
    assert await pool.fetchval("SELECT count(*) FROM shoots") == 1
    assert settings.shoot_dir(shoot).exists()


@pytest.mark.parametrize("tail", ["/photos/1", "/photos/2", "/results/1"])
async def test_files_carry_the_private_headers(client, shoot, tail):
    r = await client.get(f"/api/shoots/{shoot}{tail}")
    for name, value in files.FILE_HEADERS.items():
        assert r.headers[name] == value
