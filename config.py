import os
from dataclasses import dataclass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    # R2 / S3
    s3_endpoint: str          # https://<account_id>.r2.cloudflarestorage.com
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str            # R2 uses "auto"
    public_base_url: str      # e.g. https://cdn.example.com  (bucket custom domain, no trailing slash)

    # API
    api_key: str              # Bearer token (required)

    # Limits
    max_concurrency: int
    default_timeout: int      # seconds
    max_timeout: int

    # SSRF protection
    allow_private_ips: bool


def load_settings() -> Settings:
    return Settings(
        s3_endpoint=_require("S3_ENDPOINT"),
        s3_bucket=_require("S3_BUCKET"),
        s3_access_key=_require("S3_ACCESS_KEY"),
        s3_secret_key=_require("S3_SECRET_KEY"),
        s3_region=os.environ.get("S3_REGION", "auto"),
        public_base_url=_require("PUBLIC_BASE_URL").rstrip("/"),
        api_key=_require("API_KEY"),
        max_concurrency=int(os.environ.get("MAX_CONCURRENCY", "4")),
        default_timeout=int(os.environ.get("DEFAULT_TIMEOUT", "30")),
        max_timeout=int(os.environ.get("MAX_TIMEOUT", "120")),
        allow_private_ips=os.environ.get("ALLOW_PRIVATE_IPS", "false").lower() == "true",
    )
