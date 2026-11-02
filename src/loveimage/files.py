import asyncio
import shutil
from contextlib import suppress
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from loveimage import store

FILE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # A day, not no-cache: FileResponse never answers 304, so a page of 30 cards would download them all on each visit.
    "Cache-Control": "private, max-age=86400",
}


async def serve(path: Path) -> FileResponse:
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(404, "not_found")
    return FileResponse(path, media_type="image/jpeg", headers=FILE_HEADERS)


def remove_folder(path: Path) -> None:
    # A folder that is already gone counts as removed. Any other failure must reach the log: the rows are
    # already deleted, so the error reports a folder left behind and a retry answers 404.
    with suppress(FileNotFoundError):
        shutil.rmtree(path)


async def delete_shoot(pool, settings, shoot_id) -> None:
    outcome = await store.delete_shoot(pool, shoot_id)
    if outcome == "missing":
        raise HTTPException(404, "not_found")
    if outcome == "busy":
        raise HTTPException(409, "busy")
    # After the database: a crash between the two leaves a spare folder, not a broken shoot.
    await asyncio.to_thread(remove_folder, settings.shoot_dir(shoot_id))
