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


app = FastAPI(
    title="shot-api",
    version="1.0.0",
    summary="网页截图 API：截图 → 上传 R2 → 返回公开 URL",
    description=(
        "基于 [shot-scraper](https://shot-scraper.datasette.io/)（Playwright/Chromium）的截图服务。\n\n"
        "流程：`POST /shot` 截图 → 上传 Cloudflare R2 → 返回公开 URL。\n\n"
        "认证：所有业务接口需要 `Authorization: Bearer <API_KEY>`。"
    ),
    lifespan=lifespan,
)


def get_settings() -> Settings:
    assert settings is not None
    return settings


async def require_auth(creds: Optional[HTTPAuthorizationCredentials] = Security(bearer)):
    if settings.api_key and (creds is None or creds.credentials != settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")


class ShotRequest(BaseModel):
    """截图请求。唯一必填字段是 `url`。"""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "summary": "最简请求（全页 PNG）",
                    "value": {"url": "https://github.com/razertory"},
                },
                {
                    "summary": "JPEG 缩略图（省流量）",
                    "value": {"url": "https://example.com", "format": "jpeg", "quality": 70, "width": 800},
                },
                {
                    "summary": "截取指定元素 + 高清",
                    "value": {"url": "https://example.com", "selectors": ["#main"], "retina": True},
                },
                {
                    "summary": "等待异步内容后截图",
                    "value": {
                        "url": "https://example.com/dashboard",
                        "wait_for": "document.querySelector('#chart svg')",
                        "timeout": 60,
                    },
                },
                {
                    "summary": "存为 PDF",
                    "value": {"url": "https://example.com", "format": "pdf"},
                },
            ]
        }
    }

    url: HttpUrl = Field(description="目标页面 URL（http/https；默认拦截内网地址）")
    format: Literal["png", "jpeg", "pdf"] = Field("png", description="输出格式")
    width: Optional[int] = Field(None, ge=100, le=4000, description="视口宽度，默认 1280")
    height: Optional[int] = Field(None, ge=100, le=20000, description="视口高度；省略 = 全页截图（pdf 除外）")
    selectors: Optional[List[str]] = Field(
        None, description="CSS 选择器列表，只截取覆盖这些元素的最小区域", examples=[["#main", ".header"]]
    )
    selector_all: Optional[str] = Field(None, description="截取所有匹配该选择器的元素的总区域")
    js: Optional[str] = Field(None, description="截图前注入执行的 JavaScript（可用于移除弹窗等）")
    wait: Optional[int] = Field(None, ge=0, le=30000, description="页面 load 后额外等待的毫秒数")
    wait_for: Optional[str] = Field(
        None, description="等待该 JS 表达式返回 true 再截图（最长 30s）", examples=["document.querySelector('#content')"]
    )
    quality: Optional[int] = Field(None, ge=1, le=100, description="JPEG 质量，默认 80")
    retina: bool = Field(False, description="2x 高清截图（等效 scale_factor=2）")
    scale_factor: Optional[float] = Field(None, gt=0, le=4, description="缩放倍数")
    user_agent: Optional[str] = Field(None, description="自定义 User-Agent")
    timeout: int = Field(30, ge=5, le=120, description="整体超时秒数")
    key_prefix: str = Field("shots", pattern=r"^[a-zA-Z0-9_\-/]+$", max_length=100, description="R2 对象 key 前缀")


class ShotResponse(BaseModel):
    """截图成功，图片已上传 R2。`url` 为公开可访问地址。"""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://cdn.example.com/shots/2026/09/10/ab12cd.png",
                    "key": "shots/2026/09/10/ab12cd.png",
                    "content_type": "image/png",
                    "size_bytes": 263816,
                    "duration_ms": 2100,
                }
            ]
        }
    }

    url: str = Field(description="图片公开 URL")
    key: str = Field(description="R2 对象 key")
    content_type: str = Field(description="MIME 类型")
    size_bytes: int = Field(description="文件大小（字节）")
    duration_ms: int = Field(description="端到端耗时（毫秒）")


class ErrorDetail(BaseModel):
    error: str = Field(description="错误描述")
    stderr: Optional[str] = Field(None, description="shot-scraper 标准错误输出（仅截图失败时）")


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class UnauthorizedResponse(BaseModel):
    detail: str = Field(examples=["invalid or missing API key"])


@app.post(
    "/shot",
    response_model=ShotResponse,
    dependencies=[Depends(require_auth)],
    summary="截取网页截图并上传 R2",
    response_description="截图成功，返回图片公开 URL",
    responses={
        401: {"model": UnauthorizedResponse, "description": "API key 缺失或错误"},
        422: {
            "model": ErrorResponse,
            "description": "参数非法 / URL 无法访问 / 内网地址被拦截 / 截图执行失败",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "Blocked: 10.0.0.1 resolves to private/reserved IP 10.0.0.1",
                            "stderr": "",
                        }
                    }
                }
            },
        },
        502: {"model": ErrorResponse, "description": "R2 上传失败"},
        504: {"model": ErrorResponse, "description": "截图超时"},
    },
)
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


@app.get("/healthz", summary="健康检查", tags=["meta"])
async def healthz():
    return {"ok": True}
