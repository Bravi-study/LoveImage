"""Stand-ins for kie.ai and the language model, so the whole flow runs without spending anything.

uv run uvicorn --app-dir scripts fake_providers:app --port 9110
POST /_control {"mode": ...}: ok, no_face, minor, unsafe_wish, llm_down, no_credits, task_fail, slow_upload, slow_create.
"""

import asyncio
import io
import json
import time
import uuid
from email.parser import BytesParser

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from PIL import Image, ImageEnhance, ImageOps

ANALYSIS_SECONDS = 1.5
GENERATION_SECONDS = 4
SLOW_SECONDS = 30

app = FastAPI()
state = {"mode": "ok", "creates": 0, "last_input": None}
uploads: dict[str, bytes] = {}
tasks: dict[str, dict] = {}


def _ok(data):
    return {"code": 200, "msg": "success", "data": data}


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _couple(first: bytes, second: bytes) -> bytes:
    """Both photos side by side in warm light: enough to see that both reached the generator."""
    picture = Image.new("RGB", (900, 1200), "white")
    for x, jpeg in ((0, first), (450, second)):
        half = ImageOps.fit(Image.open(io.BytesIO(jpeg)).convert("RGB"), (450, 1200))
        picture.paste(half, (x, 0))
    picture = Image.blend(picture, Image.new("RGB", picture.size, (255, 170, 120)), 0.18)
    out = io.BytesIO()
    ImageEnhance.Color(picture).enhance(1.15).save(out, "JPEG", quality=90)
    return out.getvalue()


@app.get("/_control")
async def read_control():
    return state


@app.post("/_control")
async def control(request: Request):
    state.update(mode=(await request.json())["mode"], creates=0)
    return state


@app.post("/api/file-stream-upload")
async def upload(request: Request):
    if state["mode"] == "slow_upload":
        await asyncio.sleep(SLOW_SECONDS)
    head = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser().parsebytes(head + await request.body())
    part = next(p for p in message.get_payload() if p.get_filename())
    name = f"{uuid.uuid4().hex}.jpg"
    uploads[name] = part.get_payload(decode=True)
    return _ok({"downloadUrl": f"{_base(request)}/files/{name}"})


@app.get("/files/{name}")
async def uploaded_file(name: str):
    return Response(uploads[name], media_type="image/jpeg")


@app.post("/api/v1/jobs/createTask")
async def create_task(request: Request):
    if state["mode"] == "no_credits":
        return {"code": 402, "msg": "insufficient credits"}
    body = await request.json()
    task_id = uuid.uuid4().hex
    state["creates"] += 1
    state["last_input"] = body["input"]
    tasks[task_id] = {
        "at": time.monotonic(),
        "uploads": [url.rsplit("/", 1)[1] for url in body["input"]["input_urls"]],
        "fail": state["mode"] == "task_fail",
    }
    # The task already exists and is paid for when the answer gets lost.
    if state["mode"] == "slow_create":
        await asyncio.sleep(SLOW_SECONDS)
    return _ok({"taskId": task_id})


@app.get("/api/v1/jobs/recordInfo")
async def record_info(taskId: str, request: Request):
    task = tasks[taskId]
    if time.monotonic() - task["at"] < GENERATION_SECONDS:
        return _ok({"taskId": taskId, "state": "generating"})
    if task["fail"]:
        return _ok({"taskId": taskId, "state": "fail", "failCode": "fake", "failMsg": "fake failure"})
    urls = {"resultUrls": [f"{_base(request)}/results/{taskId}.jpg"]}
    return _ok({"taskId": taskId, "state": "success", "resultJson": json.dumps(urls)})


@app.get("/results/{task_id}.jpg")
async def result(task_id: str):
    first, second = tasks[task_id]["uploads"]
    return Response(_couple(uploads[first], uploads[second]), media_type="image/jpeg")


@app.post("/v1/chat/completions")
async def chat():
    mode = state["mode"]
    if mode == "llm_down":
        return JSONResponse({"error": "down"}, status_code=503)
    await asyncio.sleep(ANALYSIS_SECONDS)
    photo = {"one_real_face": True, "apparent_age": 15 if mode == "minor" else 30, "unsafe": False, "famous_person": False, "written_request": False}
    verdict = {
        "photo_1": photo,
        "photo_2": {**photo, "one_real_face": mode != "no_face"},
        "wish_unsafe": mode == "unsafe_wish",
        "scene_en": "a quiet evening walk in an old town, warm light",
    }
    return {"choices": [{"message": {"content": json.dumps(verdict)}}]}
