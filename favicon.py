"""Best-effort favicon fetching: parse <link rel="icon"> from the page, download it."""

import hashlib
import re
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

LINK_RE = re.compile(r"<link[^>]+>", re.I)
REL_RE = re.compile(r'rel=["\']?([^"\'>\s]+)', re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)', re.I)

EXT_BY_TYPE = {
    "image/png": ".png",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/svg+xml": ".svg",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def pick_favicon_href(html: str, page_url: str) -> str:
    """Find the best <link rel="...icon..."> href; fall back to /favicon.ico."""
    best = None
    for tag in LINK_RE.findall(html[:200_000]):
        rel = REL_RE.search(tag)
        href = HREF_RE.search(tag)
        if not rel or not href:
            continue
        rels = rel.group(1).lower()
        if "icon" in rels:
            # prefer svg/png over the generic shortcut icon
            if best is None or "svg" in tag.lower() or "png" in tag.lower():
                best = href.group(1)
    if best:
        return urljoin(page_url, best)
    parsed = urlparse(page_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


async def fetch_favicon(page_url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Returns (content, extension) or (None, None) on any failure."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=15, headers={"User-Agent": UA}
        ) as client:
            try:
                resp = await client.get(page_url)
                html = resp.text if "html" in resp.headers.get("content-type", "") else ""
            except httpx.HTTPError:
                html = ""
            favicon_url = pick_favicon_href(html, str(resp.url) if html else page_url) if html else (
                f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/favicon.ico"
            )
            r = await client.get(favicon_url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = EXT_BY_TYPE.get(ctype)
            if ext is None:
                # guess from URL path, default .ico
                path = urlparse(favicon_url).path
                ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ".ico"
                if ext not in EXT_BY_TYPE.values():
                    ext = ".ico"
            if len(r.content) > 2 * 1024 * 1024 or not r.content:
                return None, None
            return r.content, ext
    except Exception:
        return None, None


def favicon_key(content: bytes, ext: str) -> str:
    """Deterministic key by content hash so identical favicons are stored once."""
    return f"shot_api/favicons/{hashlib.sha1(content).hexdigest()}{ext}"
