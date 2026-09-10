"""FastAPI app: POST /shot -> screenshot a URL, upload to R2, return public URL."""

import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, HttpUrl

from config import Settings, load_settings
from runner import ShotError, ShotParams, take_shot
from storage import upload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shot-api")

settings = None  # type: Optional[Settings]
semaphore = None  # type: Optional[asyncio.Semaphore]
bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, semaphore
    settings = load_settings()
    semaphore = asyncio.Semaphore(settings.max_concurrency)
    log.info("started, max_concurrency=%d", settings.max_concurrency)
    yield


app = FastAPI(title="shot-api", version="1.0.0", lifespan=lifespan)


def get_settings() -> Settings:
    assert settings is not None
    return settings


async def require_auth(creds: Optional[HTTPAuthorizationCredentials] = Security(bearer)):
    if settings.api_key and (creds is None or creds.credentials != settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")


class ShotRequest(BaseModel):
    url: HttpUrl
    format: Literal["png", "jpeg", "pdf"] = "png"
    width: Optional[int] = Field(None, ge=100, le=4000)
    height: Optional[int] = Field(None, ge=100, le=20000, description="omit for full-page")
    selectors: Optional[List[str]] = None
    selector_all: Optional[str] = None
    js: Optional[str] = Field(None, description="JavaScript to run before the shot")
    wait: Optional[int] = Field(None, ge=0, le=30000, description="ms to wait after load")
    wait_for: Optional[str] = Field(None, description="JS expression to wait for")
    quality: Optional[int] = Field(None, ge=1, le=100, description="jpeg quality")
    retina: bool = False
    scale_factor: Optional[float] = Field(None, gt=0, le=4)
    user_agent: Optional[str] = None
    timeout: int = Field(30, ge=5, le=120)
    key_prefix: str = Field("shots", pattern=r"^[a-zA-Z0-9_\-/]+$", max_length=100)


class ShotResponse(BaseModel):
    url: str
    key: str
    content_type: str
    size_bytes: int
    duration_ms: int


@app.post("/shot", response_model=ShotResponse, dependencies=[Depends(require_auth)])
async def shot(req: ShotRequest):
    started = time.monotonic()
    params = ShotParams(
        url=str(req.url),
        format=req.format,
        width=req.width,
        height=req.height,
        selectors=req.selectors,
        selector_all=req.selector_all,
        js=req.js,
        wait=req.wait,
        wait_for=req.wait_for,
        quality=req.quality,
        retina=req.retina,
        scale_factor=req.scale_factor,
        user_agent=req.user_agent,
        timeout=req.timeout,
    )
    try:
        result = await take_shot(params, settings, semaphore)
    except ShotError as e:
        status_code = status.HTTP_504_GATEWAY_TIMEOUT if "Timed out" in str(e) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code, detail={"error": str(e), "stderr": e.stderr})

    try:
        url, key, size = await upload(result.file_path, settings, prefix=req.key_prefix)
    except Exception as e:
        log.exception("R2 upload failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail={"error": f"R2 upload failed: {e}"})
    finally:
        shutil.rmtree(result.file_path.parent, ignore_errors=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    log.info("shot %s -> %s (%d bytes, %d ms)", req.url, key, size, duration_ms)
    return ShotResponse(
        url=url,
        key=key,
        content_type=result.content_type,
        size_bytes=size,
        duration_ms=duration_ms,
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}
