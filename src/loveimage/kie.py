import json
import uuid

import httpx

from loveimage.settings import Settings

MAX_RESULT = 30 * 1024 * 1024
# A fixed format keeps the history grid even. "auto" would follow the first photo.
ASPECT_RATIO = "3:4"
REFUSALS = {401: "provider_auth", 402: "no_credits", 422: "provider_rejected", 500: "provider_rejected"}


class Retry(Exception):
    """Nothing was paid for, or the state simply is not known yet: safe to repeat."""


class Unknown(Exception):
    """The request may have created a paid task, so it must not be repeated."""


class Rejected(Exception):
    def __init__(self, error: str, detail: object = ""):
        super().__init__(f"{error}: {detail}")
        self.error = error


def _auth(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.kie_ai_api_key}"}


def _body(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def upload(http: httpx.AsyncClient, settings: Settings, jpeg: bytes) -> str:
    name = f"{uuid.uuid4().hex}.jpg"
    try:
        response = await http.post(
            f"{settings.kie_upload_url}/api/file-stream-upload",
            headers=_auth(settings),
            data={"uploadPath": "loveimage", "fileName": name},
            files={"file": (name, jpeg, "image/jpeg")},
            timeout=30,
        )
    except httpx.TransportError as e:
        raise Retry(repr(e)) from e
    body = _body(response) or {}
    code = body.get("code", response.status_code)
    if code in (401, 402):
        raise Rejected(REFUSALS[code], body.get("msg"))
    url = (body.get("data") or {}).get("downloadUrl")
    if not isinstance(url, str):
        raise Retry(f"upload answered {response.status_code}: {response.text[:300]}")
    return url


async def create_task(http: httpx.AsyncClient, settings: Settings, model: str, prompt: str, image_urls: list[str]) -> str:
    try:
        response = await http.post(
            f"{settings.kie_base_url}/api/v1/jobs/createTask",
            headers=_auth(settings),
            json={
                "model": model,
                "input": {
                    "prompt": prompt,
                    "input_urls": image_urls,
                    "aspect_ratio": ASPECT_RATIO,
                    "resolution": settings.kie_resolution,
                },
            },
            timeout=30,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        raise Retry(repr(e)) from e
    except httpx.TransportError as e:
        raise Unknown(repr(e)) from e
    body = _body(response)
    if response.status_code >= 500 or body is None:
        raise Unknown(f"HTTP {response.status_code}: {response.text[:500]}")
    code = body.get("code", response.status_code)
    if code == 429:
        raise Retry(body.get("msg"))
    if code in REFUSALS:
        raise Rejected(REFUSALS[code], body.get("msg"))
    task_id = (body.get("data") or {}).get("taskId") if code == 200 else None
    if not isinstance(task_id, str):
        raise Unknown(response.text[:500])
    return task_id


def _result_url(data: dict) -> str:
    try:
        url = json.loads(data["resultJson"])["resultUrls"][0]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise Rejected("provider_failed", f"bad resultJson: {data.get('resultJson')!r:.300}") from e
    if not isinstance(url, str):
        raise Rejected("provider_failed", f"bad result url: {url!r:.300}")
    return url


async def poll_task(http: httpx.AsyncClient, settings: Settings, task_id: str) -> str | None:
    """Returns the result URL, or None while the task is still running."""
    try:
        response = await http.get(
            f"{settings.kie_base_url}/api/v1/jobs/recordInfo",
            params={"taskId": task_id},
            headers=_auth(settings),
            timeout=15,
        )
    except httpx.TransportError as e:
        raise Retry(repr(e)) from e
    body = _body(response)
    if body is None:
        raise Retry(f"HTTP {response.status_code}: {response.text[:300]}")
    code = body.get("code", response.status_code)
    if code == 401:
        raise Rejected("provider_auth", body.get("msg"))
    if code != 200:
        raise Retry(f"code {code}: {body.get('msg')}")
    data = body.get("data") or {}
    state = data.get("state")
    if state == "fail":
        raise Rejected("provider_failed", f"failCode {data.get('failCode')}: {data.get('failMsg')}")
    return _result_url(data) if state == "success" else None


async def download(http: httpx.AsyncClient, settings: Settings, url: str) -> bytes:
    # The base URL is here for the fake provider, which runs on plain http.
    if not url.startswith(("https://", settings.kie_base_url)):
        raise Rejected("provider_failed", f"refused url {url[:300]}")
    data = bytearray()
    try:
        async with http.stream("GET", url, timeout=60) as response:
            if response.status_code != 200:
                raise Retry(f"download answered {response.status_code}")
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > MAX_RESULT:
                    raise Rejected("provider_failed", "result is too large")
    except httpx.TransportError as e:
        raise Retry(repr(e)) from e
    return bytes(data)
