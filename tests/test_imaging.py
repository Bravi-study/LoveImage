import asyncio
import io

import pillow_heif
import pytest
from PIL import Image

from loveimage import imaging

pytestmark = pytest.mark.anyio

GPS_IFD = 0x8825


def _encode(img, fmt, **kwargs):
    buf = io.BytesIO()
    img.save(buf, fmt, **kwargs)
    return buf.getvalue()


def _open(data):
    return Image.open(io.BytesIO(data))


async def test_extra_images_wait_instead_of_starting_a_process(monkeypatch):
    running = peak = 0

    class Child:
        returncode = 0

        async def communicate(self, data):
            nonlocal running
            await asyncio.sleep(0.01)
            running -= 1
            return b"jpeg", b""

    async def start(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        return Child()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(imaging, "_SLOTS", asyncio.Semaphore(2))
    assert await asyncio.gather(*(imaging.normalize(b"x") for _ in range(6))) == [b"jpeg"] * 6
    assert peak == 2


async def test_exif_orientation_is_applied():
    exif = Image.Exif()
    exif[0x0112] = 6
    out = _open(await imaging.normalize(_encode(Image.new("RGB", (100, 50), "red"), "JPEG", exif=exif)))
    assert out.format == "JPEG"
    assert out.size == (50, 100)


async def test_location_and_hidden_text_are_removed():
    exif = Image.Exif()
    exif[0x010E] = "secret-description"
    exif.get_ifd(GPS_IFD)[1] = "N"
    src = _encode(Image.new("RGB", (50, 50), "red"), "JPEG", exif=exif, comment=b"secret-comment")
    assert _open(src).getexif().get_ifd(GPS_IFD)

    out = await imaging.normalize(src)

    assert b"secret" not in out
    assert not _open(out).getexif()


async def test_heic_is_accepted():
    pillow_heif.register_heif_opener()
    out = _open(await imaging.normalize(_encode(Image.new("RGB", (64, 64), "blue"), "HEIF")))
    assert out.format == "JPEG"
    assert out.size == (64, 64)


async def test_long_side_is_limited():
    out = _open(await imaging.normalize(_encode(Image.new("RGB", (3000, 1000), "green"), "PNG")))
    assert out.size == (2048, 683)


async def test_a_large_photo_keeps_its_rotation():
    exif = Image.Exif()
    exif[0x0112] = 6
    out = _open(await imaging.normalize(_encode(Image.new("RGB", (3000, 1000), "red"), "JPEG", exif=exif)))
    assert out.size == (683, 2048)


@pytest.mark.parametrize("mode", ["P", "1", "L", "LA", "RGBA"])
async def test_a_large_photo_of_any_mode_is_limited(mode):
    out = _open(await imaging.normalize(_encode(Image.new(mode, (3000, 1000)), "PNG")))
    assert (out.mode, out.size) == ("RGB", (2048, 683))


async def test_a_killed_child_says_nothing_about_the_image(tmp_path, monkeypatch):
    child = tmp_path / "child.py"
    child.write_text("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    monkeypatch.setattr(imaging, "CHILD", child)
    with pytest.raises(imaging.NotDecoded):
        await imaging.normalize(b"anything")


async def test_transparent_background_becomes_white():
    out = _open(await imaging.normalize(_encode(Image.new("RGBA", (10, 10), (0, 0, 0, 0)), "PNG")))
    assert min(out.getpixel((5, 5))) > 250


async def test_image_above_pixel_limit_is_rejected():
    with pytest.raises(imaging.BadImage):
        await imaging.normalize(_encode(Image.new("1", (9000, 9000)), "PNG"))


async def test_garbage_is_rejected():
    with pytest.raises(imaging.BadImage):
        await imaging.normalize(b"this is not an image")


async def test_format_outside_the_list_is_rejected():
    with pytest.raises(imaging.BadImage):
        await imaging.normalize(_encode(Image.new("RGB", (10, 10), "red"), "GIF"))
