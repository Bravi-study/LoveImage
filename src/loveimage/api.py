import asyncio
import errno
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Path as PathParam, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request as PlainRequest

from loveimage import accounts, db, files, handlers, imaging, limits, prompts, store
from loveimage.settings import Settings

MAX_PHOTO = 25 * 1024 * 1024
# Two photos and the short fields around them. Counted while it arrives: a body is never read to its end first.
MAX_BODY = 2 * MAX_PHOTO + 64 * 1024
MAX_WISH = 300
SHOOTS_PAGE = 30


class JobIn(BaseModel):
    variant: int | None = Field(None, ge=1, le=1000)


class JobOut(BaseModel):
    variant: int
    status: str
    error: str | None
    can_retry: bool
    result_url: str | None


class ShootOut(BaseModel):
    id: UUID
    status: str
    reject_reason: str | None
    wish: str
    created_at: datetime
    photo_urls: list[str]
    jobs: list[JobOut]


class ShootCard(BaseModel):
    id: UUID
    status: str
    wish: str
    created_at: datetime
    cover_url: str | None
    images: int
    active: bool


async def _read_form(request: Request) -> FormData:
    total = 0

    async def receive():
        nonlocal total
        message = await request.receive()
        total += len(message.get("body", b""))
        if total > MAX_BODY:
            raise HTTPException(413, "too_large")
        return message

    try:
        return await PlainRequest(request.scope, receive).form(max_files=2, max_fields=4)
    except StarletteHTTPException as e:
        # A broken multipart answers 400 with a sentence, and the site only knows codes.
        if e.status_code == 400:
            raise HTTPException(422, "invalid") from e
        raise


class SpaFiles(StaticFiles):
    """Serves index.html for unknown site addresses, so client-side routes survive a reload.

    Missing files and API paths stay 404: a stale script must never receive HTML.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.split("/")[0] == "api" or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        # The page keeps its name and points at the current build: a kept copy shows the old site after a deploy.
        if Path(full_path).name == "index.html":
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pool = await db.create_pool(settings.database_url)
        await db.migrate(app.state.pool)
        async with httpx.AsyncClient() as app.state.http:
            yield
        await app.state.pool.close()

    app = FastAPI(title="LoveImage", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # The default answer echoes the input: a password back at the sender, and a lone surrogate in it crashes the encoder.
        return JSONResponse({"detail": "invalid"}, status_code=422)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> JSONResponse:
        # The page turns this into a number, so a screenshot is enough to start with. Only a name: the text can hold paths and addresses, and it still reaches the log.
        return JSONResponse({"detail": errno.errorcode.get(getattr(exc, "errno", None), type(exc).__name__)}, status_code=500)

    app.state.uploads = limits.IpCounter()
    app.state.images = limits.IpCounter()

    async def shoot_out(shoot_id: UUID) -> ShootOut:
        shoot = await store.get_shoot(app.state.pool, shoot_id)
        if shoot is None:
            raise HTTPException(404, "not_found")
        jobs = await store.list_jobs(app.state.pool, shoot_id)
        return ShootOut(
            id=shoot["id"],
            status=shoot["status"],
            reject_reason=shoot["reject_reason"],
            wish=shoot["wish"],
            created_at=shoot["created_at"],
            # A rejected shoot's photos are already deleted.
            photo_urls=[]
            if shoot["status"] == "rejected"
            else [f"/api/shoots/{shoot_id}/photos/{n}" for n in (1, 2)],
            jobs=[
                JobOut(
                    variant=j["variant"],
                    status=j["status"],
                    error=j["error"],
                    # After submit_unknown the first task may be paid for and its record must stay: a new variant is ordered instead.
                    can_retry=j["status"] == "failed" and j["attempt"] < store.MAX_ATTEMPTS and j["error"] != "submit_unknown",
                    result_url=f"/api/shoots/{shoot_id}/results/{j['variant']}" if j["result_path"] else None,
                )
                for j in jobs
            ],
        )

    async def own_shoot(request: Request, shoot_id: UUID):
        """A stranger's shoot and a missing one answer the same, so ids can't be probed."""
        user_id = await accounts.current_user(request)
        shoot = await store.get_shoot(app.state.pool, shoot_id)
        # user_id goes first: a shoot without an owner must not match a visitor without a session.
        if user_id is None or shoot is None or shoot["user_id"] != user_id:
            raise HTTPException(404, "not_found")
        return shoot

    async def read_upload(request: Request, user_id) -> tuple[list[bytes], str]:
        form = await _read_form(request)
        try:
            uploads = [form.get("photo1"), form.get("photo2")]
            wish = form.get("wish", "")
            # The box must be ticked on the page, so what the visitor agreed to is only recorded on a real "true".
            if not all(isinstance(u, UploadFile) for u in uploads) or not isinstance(wish, str) or form.get("consent") != "true":
                raise HTTPException(422, "invalid")
            wish = " ".join(wish.split())
            if len(wish) > MAX_WISH or not wish.isprintable():
                raise HTTPException(422, "bad_wish")
            if any(u.size > MAX_PHOTO for u in uploads):
                raise HTTPException(413, "too_large")
            # Before the decoding and the paid check: there is no point in either when no photo can be made today.
            refused = await store.limit_reached(
                app.state.pool, user_id, settings.daily_generation_limit, settings.personal_daily_limit
            )
            if refused:
                raise HTTPException(429, refused)
            jpegs = []
            for n, upload in enumerate(uploads, 1):
                try:
                    jpegs.append(await imaging.normalize(await upload.read()))
                except imaging.BadImage as e:
                    raise HTTPException(422, f"bad_image_{n}") from e
            return jpegs, wish
        finally:
            await form.close()

    @app.post("/api/shoots", status_code=201)
    async def create_shoot(request: Request, response: Response) -> ShootOut:
        ip = limits.client_ip(request)
        # Before the body: junk and oversized files spend the upload count too.
        if app.state.uploads.reached(ip, settings.uploads_per_ip) or app.state.images.reached(ip, settings.images_per_ip):
            raise HTTPException(429, "ip_limit")
        app.state.uploads.add(ip)
        # The first variant is ordered by the worker, which knows no addresses, so it is counted here.
        # shortcut: a shoot rejected later keeps its count, the worker would need a way to reach this process.
        app.state.images.add(ip)
        created = False
        try:
            user_id = await accounts.current_user(request)
            jpegs, wish = await read_upload(request, user_id)
            user_id = user_id or await accounts.start_guest(request, response)
            shoot_id = uuid.uuid4()
            # The files go first: the worker may pick the row up right after the insert.
            folder = settings.shoot_dir(shoot_id)
            try:
                for name, jpeg in zip(handlers.PHOTOS, jpegs, strict=True):
                    await asyncio.to_thread(imaging.write_atomic, folder / name, jpeg)
                await store.create_shoot(app.state.pool, shoot_id, user_id, wish)
            except Exception:
                # Without a row nobody could ever delete these photos.
                await asyncio.to_thread(files.remove_folder, folder)
                raise
            created = True
        finally:
            if not created:
                app.state.images.remove(ip)
        return await shoot_out(shoot_id)

    @app.get("/api/shoots")
    async def list_shoots(request: Request, before: datetime | None = None) -> list[ShootCard]:
        user_id = await accounts.current_user(request)
        if user_id is None:
            return []
        rows = await store.user_shoots(app.state.pool, user_id, before, SHOOTS_PAGE)
        return [
            ShootCard(
                id=r["id"],
                status=r["status"],
                wish=r["wish"],
                created_at=r["created_at"],
                images=r["images"],
                active=r["active"],
                cover_url=f"/api/shoots/{r['id']}/results/{r['latest_variant']}" if r["latest_variant"] else None,
            )
            for r in rows
        ]

    @app.get("/api/shoots/{shoot_id}")
    async def get_shoot(request: Request, shoot_id: UUID) -> ShootOut:
        await own_shoot(request, shoot_id)
        return await shoot_out(shoot_id)

    @app.get("/api/shoots/{shoot_id}/photos/{n}", response_class=FileResponse)
    async def get_photo(request: Request, shoot_id: UUID, n: Annotated[int, PathParam(ge=1, le=2)]):
        await own_shoot(request, shoot_id)
        return await files.serve(settings.shoot_dir(shoot_id) / handlers.PHOTOS[n - 1])

    @app.post("/api/shoots/{shoot_id}/analysis")
    async def repeat_analysis(request: Request, shoot_id: UUID) -> ShootOut:
        await own_shoot(request, shoot_id)
        if not await store.reopen_shoot(app.state.pool, shoot_id):
            raise HTTPException(409, "not_failed")
        return await shoot_out(shoot_id)

    @app.post("/api/shoots/{shoot_id}/jobs", status_code=202)
    async def order_job(request: Request, shoot_id: UUID, job: JobIn) -> ShootOut:
        shoot = await own_shoot(request, shoot_id)
        if shoot["status"] != "ready":
            raise HTTPException(409, "not_ready")
        ip = limits.client_ip(request)
        if app.state.images.reached(ip, settings.images_per_ip):
            raise HTTPException(429, "ip_limit")
        # Before the await: a parallel burst has to see its own orders.
        app.state.images.add(ip)
        outcome = None
        try:
            outcome = await store.order_job(
                app.state.pool,
                shoot_id,
                shoot["user_id"],
                job.variant,
                settings.kie_model,
                prompts.build(shoot["scene_en"]),
                settings.daily_generation_limit,
                settings.personal_daily_limit,
            )
        finally:
            if outcome != "created":
                app.state.images.remove(ip)
        if outcome in ("daily_limit", "personal_limit"):
            raise HTTPException(429, outcome)
        if outcome == "not_retryable":
            raise HTTPException(409, "not_retryable")
        # "busy" answers like "created": a double click has nothing to report, the first order is running.
        return await shoot_out(shoot_id)

    @app.get("/api/shoots/{shoot_id}/results/{variant}", response_class=FileResponse)
    async def get_result(request: Request, shoot_id: UUID, variant: Annotated[int, PathParam(ge=1, le=1000)]):
        await own_shoot(request, shoot_id)
        path = await store.result_path(app.state.pool, shoot_id, variant)
        if path is None:
            raise HTTPException(404, "not_found")
        return await files.serve(settings.data_dir / path)

    @app.delete("/api/shoots/{shoot_id}", status_code=204)
    async def delete_shoot(request: Request, shoot_id: UUID) -> None:
        await own_shoot(request, shoot_id)
        await files.delete_shoot(app.state.pool, settings, shoot_id)

    app.include_router(accounts.make_router())

    if settings.web_dist.is_dir():
        app.mount("/", SpaFiles(directory=settings.web_dist, html=True))
    return app
