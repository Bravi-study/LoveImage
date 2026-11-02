import io
import uuid

import httpx
import pytest
from PIL import Image

import fake_providers
from conftest import make_jpeg
from loveimage import handlers, kie, llm, store, worker

pytestmark = pytest.mark.anyio

WISH = "вечер в старом городе"


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(fake_providers, "ANALYSIS_SECONDS", 0)
    fake_providers.state.update(mode="ok", creates=0, last_input=None)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_providers.app))


async def create(http, settings):
    urls = [await kie.upload(http, settings, make_jpeg(color)) for color in ("orange", "navy")]
    return await kie.create_task(http, settings, "model", "a picture", urls)


async def test_real_client_gets_a_picture_with_both_photos(http, settings, monkeypatch):
    monkeypatch.setattr(fake_providers, "GENERATION_SECONDS", 0)
    result = await kie.poll_task(http, settings, await create(http, settings))
    picture = Image.open(io.BytesIO(await kie.download(http, settings, result))).convert("RGB")
    assert picture.size == (900, 1200)
    assert picture.getpixel((200, 600))[0] > picture.getpixel((700, 600))[0]
    assert fake_providers.state["creates"] == 1


async def test_both_addresses_and_the_format_reach_the_fake(http, settings):
    await create(http, settings)
    sent = fake_providers.state["last_input"]
    assert len(sent["input_urls"]) == 2
    assert sent["aspect_ratio"] == "3:4"


async def test_task_is_still_generating_at_first(http, settings):
    assert await kie.poll_task(http, settings, await create(http, settings)) is None


async def test_failed_task_mode(http, settings, monkeypatch):
    monkeypatch.setattr(fake_providers, "GENERATION_SECONDS", 0)
    fake_providers.state["mode"] = "task_fail"
    with pytest.raises(kie.Rejected) as e:
        await kie.poll_task(http, settings, await create(http, settings))
    assert e.value.error == "provider_failed"


async def test_no_credits_mode(http, settings):
    fake_providers.state["mode"] = "no_credits"
    with pytest.raises(kie.Rejected) as e:
        await create(http, settings)
    assert e.value.error == "no_credits"


@pytest.mark.parametrize(
    "mode, reason",
    [("ok", None), ("no_face", "no_face_2"), ("minor", "minor"), ("unsafe_wish", "unsafe_wish")],
)
async def test_analysis_modes(http, settings, mode, reason):
    fake_providers.state["mode"] = mode
    verdict = await llm.analyze(http, settings, make_jpeg(), make_jpeg(), WISH)
    assert verdict.reject_reason(WISH) == reason


async def test_llm_down_mode(http, settings):
    fake_providers.state["mode"] = "llm_down"
    with pytest.raises(llm.Transient):
        await llm.analyze(http, settings, make_jpeg(), make_jpeg(), WISH)


async def test_control_switches_the_mode_and_resets_the_counter(http):
    fake_providers.state["creates"] = 3
    r = await http.post("http://fake/_control", json={"mode": "minor"})
    assert (r.json()["mode"], r.json()["creates"]) == ("minor", 0)


async def test_whole_flow_from_upload_to_the_result(http, client, send, pool, settings, monkeypatch):
    monkeypatch.setattr(fake_providers, "GENERATION_SECONDS", 0)
    ctx = worker.Ctx(pool, http, settings)

    shoot_id = (await send(client, wish=WISH)).json()["id"]
    await handlers.analyze(ctx, await store.claim_shoot(pool))
    await handlers.handle_job(ctx, await store.claim_job(pool))
    await pool.execute("UPDATE jobs SET run_at = now()")
    await handlers.handle_job(ctx, await store.claim_job(pool))

    shoot = (await client.get(f"/api/shoots/{shoot_id}")).json()
    assert shoot["status"] == "ready"
    assert [(j["variant"], j["status"]) for j in shoot["jobs"]] == [(1, "succeeded")]
    assert await pool.fetchval("SELECT credits FROM daily_usage") == 6
    assert WISH not in await pool.fetchval("SELECT prompt FROM jobs")
    assert await pool.fetchval("SELECT scene_en FROM shoots WHERE id = $1", uuid.UUID(shoot_id))

    picture = Image.open(io.BytesIO((await client.get(shoot["jobs"][0]["result_url"])).content)).convert("RGB")
    left, right = picture.getpixel((200, 600)), picture.getpixel((700, 600))
    assert left[0] > right[0]
    assert left[2] < right[2]
