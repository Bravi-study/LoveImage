import io
import uuid

import httpx
import pytest
from PIL import Image

from loveimage import accounts, api, files, store

pytestmark = pytest.mark.anyio

SHOOTS = "/api/shoots"


async def nothing_created(pool, settings):
    assert await pool.fetchval("SELECT count(*) FROM shoots") == 0
    assert not (settings.data_dir / "shoots").exists()


async def test_upload_creates_a_shoot_waiting_for_the_check(client, send, pool, owner):
    r = await send(client, wish="  на   крыше  ")
    assert r.status_code == 201
    body = r.json()
    shoot_id = body["id"]
    assert body["status"] == "uploaded"
    assert body["wish"] == "на крыше"
    assert body["jobs"] == []
    assert body["photo_urls"] == [f"{SHOOTS}/{shoot_id}/photos/1", f"{SHOOTS}/{shoot_id}/photos/2"]
    row = await pool.fetchrow(
        """SELECT status, user_id, consent_at IS NOT NULL AS consented, run_at <= now() AS due, deadline_at > now() AS alive
           FROM shoots WHERE id = $1""",
        uuid.UUID(shoot_id),
    )
    assert dict(row) == {"status": "uploaded", "user_id": owner.id, "consented": True, "due": True, "alive": True}


@pytest.mark.parametrize("consent", [None, "", "false", "True", "TRUE", "1", "on", " true", "true ", "yes"])
async def test_consent_must_be_exactly_true(stranger, send, pool, settings, consent):
    r = await send(stranger, consent=consent)
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}
    assert "set-cookie" not in r.headers
    assert await pool.fetchval("SELECT count(*) FROM users") == 0
    await nothing_created(pool, settings)


async def test_photos_do_not_stay_behind_a_failed_insert(client, send, settings, monkeypatch):
    async def fail(*args):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(store, "create_shoot", fail)
    with pytest.raises(RuntimeError):
        await send(client)
    assert not list((settings.data_dir / "shoots").iterdir())


async def test_consent_sent_as_a_file_is_refused(client, photos, pool, settings):
    r = await client.post(
        SHOOTS,
        files={"photo1": ("a.jpg", photos[0]), "photo2": ("b.jpg", photos[1]), "consent": ("c.txt", b"true")},
    )
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


@pytest.mark.parametrize("missing", ["photo1", "photo2"])
async def test_a_missing_photo_is_refused(client, send, pool, settings, missing):
    r = await send(client, **{missing: None})
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


async def test_a_photo_sent_as_text_is_refused(client, photos, pool, settings):
    r = await client.post(
        SHOOTS, data={"photo1": "not a file", "consent": "true"}, files={"photo2": ("b.jpg", photos[1])}
    )
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


async def test_wish_of_300_characters_is_accepted(client, send):
    assert (await send(client, wish="я" * 300)).status_code == 201


async def test_wish_over_300_characters_is_refused(client, send, pool, settings):
    r = await send(client, wish="я" * 301)
    assert r.status_code == 422
    assert r.text == '{"detail":"bad_wish"}'
    await nothing_created(pool, settings)


@pytest.mark.parametrize("wish", ["a\x00b", "a\x07b", "a\x7fb", "a‮b", "\x1b[31mred"])
async def test_wish_with_control_characters_is_refused_without_an_echo(client, send, pool, settings, wish):
    r = await send(client, wish=wish)
    assert r.status_code == 422
    assert r.text == '{"detail":"bad_wish"}'
    await nothing_created(pool, settings)


async def test_line_breaks_in_a_wish_become_spaces(client, send):
    assert (await send(client, wish="на\nкрыше\t\r\nзимой")).json()["wish"] == "на крыше зимой"


async def test_a_huge_wish_answers_with_a_code(client, send, pool, settings):
    r = await send(client, wish="я" * (1024 * 1024))
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


async def test_a_third_file_answers_with_a_code(client, photos, pool, settings):
    parts = {"photo1": ("a", photos[0]), "photo2": ("b", photos[1]), "photo3": ("c", photos[0])}
    r = await client.post(SHOOTS, files=parts, data={"consent": "true"})
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


async def test_too_many_fields_answer_with_a_code(client, photos, pool, settings):
    fields = {"consent": "true", **{f"field{n}": "x" for n in range(9)}}
    r = await client.post(SHOOTS, files={"photo1": ("a", photos[0]), "photo2": ("b", photos[1])}, data=fields)
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


@pytest.mark.parametrize(
    "content_type, body",
    [
        ("multipart/form-data", b"no boundary here"),
        ("multipart/form-data; boundary=b", b"--b\r\nno headers at all\xff\xfe\r\n--b--\r\n"),
        ("application/json", b"{}"),
        ("text/plain", b"hello"),
    ],
)
async def test_a_body_that_is_no_form_answers_with_a_code(client, pool, settings, content_type, body):
    r = await client.post(SHOOTS, content=body, headers={"content-type": content_type})
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid"}
    await nothing_created(pool, settings)


@pytest.mark.parametrize("n", [1, 2])
async def test_something_that_is_no_image_is_named_by_its_slot(client, send, pool, settings, n):
    r = await send(client, **{f"photo{n}": b"not an image"})
    assert r.status_code == 422
    assert r.json() == {"detail": f"bad_image_{n}"}
    await nothing_created(pool, settings)


@pytest.mark.parametrize("n", [1, 2])
async def test_photo_above_the_limit_is_refused(client, send, pool, settings, n):
    r = await send(client, **{f"photo{n}": b"x" * (api.MAX_PHOTO + 1)})
    assert r.status_code == 413
    assert r.json() == {"detail": "too_large"}
    await nothing_created(pool, settings)


async def test_an_endless_body_is_cut_off_without_reading_it_all(stranger, pool, settings):
    sent = 0

    async def body():
        nonlocal sent
        yield (
            b'--b\r\nContent-Disposition: form-data; name="photo1"; filename="a.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n"
        )
        for _ in range(200):
            sent += 1
            yield b"x" * (1024 * 1024)

    r = await stranger.post(SHOOTS, content=body(), headers={"content-type": "multipart/form-data; boundary=b"})

    assert r.status_code == 413
    assert r.json() == {"detail": "too_large"}
    assert sent <= api.MAX_BODY // (1024 * 1024) + 2
    assert "set-cookie" not in r.headers
    await nothing_created(pool, settings)


@pytest.mark.parametrize(
    "method, url, kwargs",
    [
        ("get", "/api/shoots/SECRET", {}),
        ("get", "/api/shoots", {"params": {"before": "SECRET"}}),
        ("get", f"/api/shoots/{uuid.uuid4()}/photos/SECRET", {}),
        ("post", f"/api/shoots/{uuid.uuid4()}/jobs", {"json": {"variant": "SECRET"}}),
        ("post", "/api/register", {"json": {"nickname": "SECRET!", "password": "SECRET"}}),
        ("post", "/api/login", {"content": b'{"nickname":"SECRET'}),
    ],
)
async def test_validation_errors_never_echo_the_input(client, method, url, kwargs):
    r = await client.request(method, url, **kwargs)
    assert r.status_code == 422
    assert r.text == '{"detail":"invalid"}'


async def test_photos_are_served_clean_in_their_order(client, send):
    links = (await send(client)).json()["photo_urls"]
    first, second = [await client.get(link) for link in links]
    for r in (first, second):
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        for name, value in files.FILE_HEADERS.items():
            assert r.headers[name] == value
    red = [Image.open(io.BytesIO(r.content)).convert("RGB").getpixel((50, 40))[0] for r in (first, second)]
    assert red[0] > 200 > 50 > red[1]


async def test_first_upload_creates_a_guest_with_a_cookie(stranger, send, pool):
    r = await send(stranger)
    assert r.status_code == 201
    assert accounts.COOKIE in r.headers["set-cookie"]
    assert await pool.fetchval("SELECT count(*) FROM users WHERE nickname IS NULL") == 1
    assert (await stranger.get(f"{SHOOTS}/{r.json()['id']}")).status_code == 200


async def test_garbage_upload_creates_no_guest(stranger, send, pool):
    assert (await send(stranger, photo2=b"not an image")).status_code == 422
    assert await pool.fetchval("SELECT count(*) FROM users") == 0


async def test_upload_with_a_session_keeps_its_owner(client, send, owner, pool):
    r = await send(client)
    assert "set-cookie" not in r.headers
    assert await pool.fetchval("SELECT user_id FROM shoots") == owner.id


async def test_upload_with_a_dead_cookie_creates_a_new_guest(visit, send):
    async with visit("dead-token") as client:
        r = await send(client)
        assert accounts.COOKIE in r.headers["set-cookie"]
        assert (await client.get(f"{SHOOTS}/{r.json()['id']}")).status_code == 200


@pytest.fixture
def site(pool, settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>site</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    app = api.create_app(settings.model_copy(update={"web_dist": dist}))
    app.state.pool = pool
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_site_addresses_fall_back_to_the_page(site):
    for path in ["/", "/shoot/x", "/account"]:
        r = await site.get(path)
        assert r.status_code == 200 and "site" in r.text
    assert (await site.get("/assets/app.js")).text == "console.log(1)"
    for path in ["/assets/missing.js", "/api", "/api/", "/api/nope"]:
        assert (await site.get(path)).status_code == 404


async def test_the_page_is_checked_with_the_server_on_every_visit(site):
    for path in ["/", "/shoot/x", "/index.html"]:
        r = await site.get(path)
        assert r.headers["cache-control"] == "no-cache"
        again = await site.get(path, headers={"if-none-match": r.headers["etag"]})
        assert again.status_code == 304 and again.headers["cache-control"] == "no-cache"
    assert "cache-control" not in (await site.get("/assets/app.js")).headers


async def test_api_docs_are_closed(client, settings):
    for path in ("/docs", "/redoc", "/openapi.json"):
        body = (await client.get(path)).text.lower()
        assert "swagger" not in body and "redoc" not in body and '"openapi"' not in body
    # The site's API types are generated from this.
    assert api.create_app(settings).openapi()["paths"]
