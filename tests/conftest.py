import io
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from loveimage import accounts, api, db, handlers, imaging, prompts, store
from loveimage.settings import Settings

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/loveimage_test")
# The tables are truncated and dropped here, and the live database's name is one word away.
assert TEST_DSN.rsplit("/", 1)[-1].split("?")[0].endswith("_test"), "tests only run on a *_test database"

# Real keys live in .env and in the shell: neither may reach a test, so a stray Settings() fails instead.
Settings.model_config["env_file"] = None
for field in Settings.model_fields:
    os.environ.pop(field.upper(), None)

SCENE = "a quiet evening walk in an old town"


def make_jpeg(color="orange", size=(100, 80)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Every test client has an in-process transport, so a real socket means a double was forgotten."""

    async def refuse(self, request):
        raise AssertionError(f"a test tried to reach {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        kie_ai_api_key="test-kie",
        commandcode_api_key="test-llm",
        kie_model="flare",
        kie_resolution="1K",
        llm_model="test/vision",
        daily_generation_limit=5,
        personal_daily_limit=1000,
        uploads_per_ip=1000,
        images_per_ip=1000,
        database_url=TEST_DSN,
        data_dir=tmp_path,
        web_dist=tmp_path / "no-site",
        kie_base_url="https://kie.test",
        kie_upload_url="https://upload.test",
        llm_base_url="https://llm.test/v1",
    )


@pytest.fixture
async def pool():
    pool = await db.create_pool(TEST_DSN)
    await db.migrate(pool)
    await pool.execute("TRUNCATE users, daily_usage CASCADE")
    yield pool
    await pool.close()


@pytest.fixture
def new_guest(pool):
    async def make():
        token = secrets.token_urlsafe(32)
        return SimpleNamespace(id=await store.create_guest(pool, accounts.token_hash(token)), token=token)

    return make


@pytest.fixture
async def owner(new_guest):
    return await new_guest()


@pytest.fixture
def visit(pool, settings):
    """Opens a client of its own, with the given session or without one."""

    @asynccontextmanager
    async def open_client(token=None):
        app = api.create_app(settings)
        app.state.pool = pool
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if token:
                # The domain httpx files a host without dots under, so a Set-Cookie from the app replaces this one.
                client.cookies.set(accounts.COOKIE, token, domain="test.local")
            yield client

    return open_client


@pytest.fixture
async def client(visit, owner):
    async with visit(owner.token) as client:
        yield client


@pytest.fixture
async def stranger(visit):
    async with visit() as client:
        yield client


@pytest.fixture
def photos():
    return make_jpeg("orange"), make_jpeg("navy")


@pytest.fixture
def send(photos):
    """POST /api/shoots. None leaves a field out."""

    def post(client, photo1=photos[0], photo2=photos[1], wish=None, consent="true", headers=None):
        parts = {"photo1": photo1, "photo2": photo2}
        files = {name: ("photo.jpg", data, "image/jpeg") for name, data in parts.items() if data is not None}
        fields = {name: value for name, value in {"wish": wish, "consent": consent}.items() if value is not None}
        return client.post("/api/shoots", files=files, data=fields, headers=headers)

    return post


@pytest.fixture
def uploaded(pool, settings, owner, photos):
    """A shoot row with its photos, as the upload endpoint leaves it."""

    async def make(wish="", user_id=None):
        shoot_id = uuid.uuid4()
        for name, jpeg in zip(handlers.PHOTOS, photos, strict=True):
            imaging.write_atomic(settings.shoot_dir(shoot_id) / name, jpeg)
        await store.create_shoot(pool, shoot_id, user_id or owner.id, wish)
        return shoot_id

    return make


@pytest.fixture
def ready(pool, settings, owner, uploaded):
    """A checked shoot with no jobs: the state a reached limit leaves behind."""

    async def make(user_id=None):
        shoot_id = await uploaded(user_id=user_id)
        await store.approve_shoot(pool, shoot_id, SCENE, settings.kie_model, "unused", 0, 0)
        return shoot_id

    return make


@pytest.fixture
def queued(pool, settings, owner, uploaded):
    """A checked shoot whose first job waits for the worker."""

    async def make():
        shoot_id = await uploaded()
        await store.approve_shoot(pool, shoot_id, SCENE, settings.kie_model, prompts.build(SCENE), 100, 100)
        return shoot_id

    return make
