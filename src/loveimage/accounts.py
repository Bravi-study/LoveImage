import asyncio
import hashlib
import hmac
import secrets
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from loveimage import store

COOKIE = "loveimage_session"
N, R, P = 2**15, 8, 1
MAXMEM = 64 * 1024 * 1024


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, maxmem=MAXMEM, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, n, r, p, salt, digest = stored.split("$")
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=MAXMEM, dklen=32
    )
    return hmac.compare_digest(actual, bytes.fromhex(digest))


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class Credentials(BaseModel):
    nickname: str = Field(pattern=r"^[0-9A-Za-zА-Яа-яЁё]{3,20}$")
    password: str = Field(min_length=6, max_length=128)


class Me(BaseModel):
    nickname: str | None


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=store.SESSION_DAYS * 86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request.headers.get("x-forwarded-proto") == "https",
    )


def _old_hash(request: Request) -> bytes | None:
    token = request.cookies.get(COOKIE)
    return token_hash(token) if token else None


async def current_user(request: Request) -> UUID | None:
    old = _old_hash(request)
    return await store.session_user(request.app.state.pool, old) if old else None


async def start_guest(request: Request, response: Response) -> UUID:
    token = secrets.token_urlsafe(32)
    user_id = await store.create_guest(request.app.state.pool, token_hash(token))
    _set_cookie(request, response, token)
    return user_id


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    # Each hash takes 32 MB and a core. Created here: every app, and so every test, has its own event loop.
    # shortcut: registers and login attempts have no limit per address, only this queue holds the load, add an address counter if it gets abused.
    hashing = asyncio.Semaphore(2)

    async def scrypt(func, *args):
        async with hashing:
            return await asyncio.to_thread(func, *args)

    @router.post("/register", status_code=201)
    async def register(request: Request, response: Response, body: Credentials) -> Me:
        password_hash = await scrypt(hash_password, body.password)
        token = secrets.token_urlsafe(32)
        try:
            await store.register(
                request.app.state.pool,
                _old_hash(request),
                body.nickname,
                body.nickname.casefold(),
                password_hash,
                token_hash(token),
            )
        except asyncpg.UniqueViolationError as e:
            raise HTTPException(409, "nickname_taken") from e
        _set_cookie(request, response, token)
        return Me(nickname=body.nickname)

    @router.post("/login")
    async def login(request: Request, response: Response, body: Credentials) -> Me:
        pool = request.app.state.pool
        async with hashing:
            # Read inside the slot: a burst queued here has to see the lock its own failures set.
            user = await store.find_account(pool, body.nickname.casefold())
            if user is None:
                raise HTTPException(401, "bad_credentials")
            if user["locked"]:
                raise HTTPException(429, "locked")
            if not await asyncio.to_thread(verify_password, body.password, user["password_hash"]):
                await store.login_failed(pool, user["id"])
                raise HTTPException(401, "bad_credentials")
        token = secrets.token_urlsafe(32)
        await store.login_succeeded(pool, user["id"], _old_hash(request), token_hash(token))
        _set_cookie(request, response, token)
        return Me(nickname=user["nickname"])

    @router.post("/logout", status_code=204)
    async def logout(request: Request, response: Response) -> None:
        old = _old_hash(request)
        if old:
            await store.delete_session(request.app.state.pool, old)
            response.delete_cookie(COOKIE)

    @router.get("/me")
    async def me(request: Request) -> Me:
        user_id = await current_user(request)
        return Me(nickname=await store.nickname(request.app.state.pool, user_id) if user_id else None)

    return router
