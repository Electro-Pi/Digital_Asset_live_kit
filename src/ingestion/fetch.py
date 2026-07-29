"""Polite, cached HTTP fetching for the guideline corpus.

Three properties matter here. The fetcher identifies itself with a contact
address, it respects each site's crawl delay and robots.txt, and it caches raw
bytes on disk so re-running any later stage of the pipeline never touches the
publishers' servers again. A corpus rebuild after a chunking tweak should cost
zero requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger

USER_AGENT = (
    "MedicalCodaBot/0.1 (KFSHRC Family Medicine PoC; "
    "contact: ahmed.selim@electropi.ai)"
)


class FetchError(RuntimeError):
    pass


def cache_path(root: Path, url: str, suffix: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    host = urlparse(url).netloc.replace(":", "_")
    return root / host / f"{digest}{suffix}"


class PoliteFetcher:
    def __init__(self, cache_root: Path, *, timeout_s: float = 45.0) -> None:
        self._cache_root = cache_root
        self._timeout = timeout_s
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            response = await self._client.get(f"{origin}/robots.txt")
            if response.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
        except Exception as exc:  # noqa: BLE001
            # A missing or unreachable robots.txt is not permission to hammer
            # the host, but it is also not a reason to abort — the per-source
            # crawl delay still applies.
            logger.warning("Could not read robots.txt for {}: {}", origin, exc)
        self._robots[origin] = parser
        return parser

    async def _throttle(self, url: str, delay_s: float) -> None:
        host = urlparse(url).netloc
        previous = self._last_request.get(host)
        if previous is not None:
            wait = delay_s - (time.monotonic() - previous)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_request[host] = time.monotonic()

    async def get(
        self,
        url: str,
        *,
        delay_s: float = 2.0,
        binary: bool = False,
        refresh: bool = False,
    ) -> bytes:
        """Fetch a URL, serving from the on-disk cache when possible."""
        suffix = ".pdf" if binary else ".html"
        path = cache_path(self._cache_root, url, suffix)
        if path.exists() and not refresh:
            return path.read_bytes()

        robots = await self._robots_for(url)
        if robots is not None and not robots.can_fetch(USER_AGENT, url):
            raise FetchError(f"robots.txt disallows fetching {url}")

        # Prefer the site's own declared crawl-delay when it is stricter than
        # ours (USPSTF asks for 5s, for instance).
        effective_delay = delay_s
        if robots is not None:
            declared = robots.crawl_delay(USER_AGENT)
            if declared:
                effective_delay = max(effective_delay, float(declared))

        await self._throttle(url, effective_delay)

        response = await self._client.get(url)
        if response.status_code != 200:
            raise FetchError(f"HTTP {response.status_code} for {url}")

        payload = response.content
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        logger.info("Fetched {} ({} bytes)", url, len(payload))
        return payload
