"""Upload files to R2 (S3-compatible) and return public URLs."""

import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig

from config import Settings

_client = None


def get_client(settings: Settings):
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )
    return _client


def make_key(file_path: Path, prefix: str = "shots") -> str:
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    return f"{prefix}/{today}/{uuid.uuid4().hex}{file_path.suffix}"


async def upload(file_path: Path, settings: Settings, prefix: str = "shots") -> Tuple[str, str, int]:
    """Returns (public_url, key, size_bytes)."""
    key = make_key(file_path, prefix)
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    size = file_path.stat().st_size
    body = file_path.read_bytes()
    await _put_object(settings, key, body, content_type)
    return f"{settings.public_base_url}/{key}", key, size


async def _put_object(settings: Settings, key: str, body: bytes, content_type: str) -> None:
    def _put():
        get_client(settings).put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )

    await asyncio.to_thread(_put)


async def upload_bytes(body: bytes, key: str, content_type: str, settings: Settings) -> str:
    """Upload raw bytes to an exact key; returns the public URL."""
    await _put_object(settings, key, body, content_type)
    return f"{settings.public_base_url}/{key}"
