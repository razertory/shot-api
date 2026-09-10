"""Run shot-scraper as a subprocess and return the output file path."""

import asyncio
import ipaddress
import shlex
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from config import Settings


class ShotError(Exception):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


@dataclass
class ShotParams:
    url: str
    format: str = "png"            # png | jpeg | pdf
    width: Optional[int] = None
    height: Optional[int] = None      # None => full page (not for pdf)
    selectors: Optional[List[str]] = None
    selector_all: Optional[str] = None
    js: Optional[str] = None
    wait: Optional[int] = None        # ms
    wait_for: Optional[str] = None    # js expression
    quality: Optional[int] = None     # jpeg only
    retina: bool = False
    scale_factor: Optional[float] = None
    user_agent: Optional[str] = None
    timeout: int = 30              # seconds


async def validate_url(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ShotError(f"Unsupported scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ShotError("URL has no hostname")
    if settings.allow_private_ips:
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as e:
        raise ShotError(f"DNS resolution failed for {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ShotError(f"Blocked: {host} resolves to private/reserved IP {ip}")


def build_command(params: ShotParams, output: Path) -> List[str]:
    if params.format == "pdf":
        cmd = ["shot-scraper", "pdf", params.url, "-o", str(output), "--wait", str(params.wait or 0)]
        if params.js:
            cmd += ["--javascript", params.js]
        if params.user_agent:
            cmd += ["--user-agent", params.user_agent]
        return cmd

    cmd = ["shot-scraper", params.url, "-o", str(output)]
    if params.width:
        cmd += ["--width", str(params.width)]
    if params.height:
        cmd += ["--height", str(params.height)]
    for sel in params.selectors or []:
        cmd += ["--selector", sel]
    if params.selector_all:
        cmd += ["--selector-all", params.selector_all]
    if params.js:
        cmd += ["--javascript", params.js]
    if params.wait:
        cmd += ["--wait", str(params.wait)]
    if params.wait_for:
        cmd += ["--wait-for", params.wait_for]
    if params.format == "jpeg":
        cmd += ["--quality", str(params.quality or 80)]
    if params.retina:
        cmd += ["--retina"]
    if params.scale_factor:
        cmd += ["--scale-factor", str(params.scale_factor)]
    if params.user_agent:
        cmd += ["--user-agent", params.user_agent]
    return cmd


@dataclass
class ShotResult:
    file_path: Path
    content_type: str


CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf"}


async def take_shot(params: ShotParams, settings: Settings, semaphore: asyncio.Semaphore) -> ShotResult:
    await validate_url(params.url, settings)

    timeout = min(params.timeout, settings.max_timeout)
    suffix = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}[params.format]
    tmp = Path(tempfile.mkdtemp(prefix="shot-")) / f"shot{suffix}"
    cmd = build_command(params, tmp)

    async with semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ShotError(f"Timed out after {timeout}s: {params.url}")

    if proc.returncode != 0 or not tmp.exists():
        raise ShotError(
            f"shot-scraper failed (exit {proc.returncode})",
            stderr=stderr.decode(errors="replace")[:2000],
        )

    return ShotResult(file_path=tmp, content_type=CONTENT_TYPES[params.format])
