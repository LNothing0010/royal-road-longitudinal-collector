from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings


@dataclass
class FetchResult:
    url: str
    text: str
    status_code: int
    elapsed_seconds: float


class PublicHtmlClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=settings.timeout_seconds,
            follow_redirects=True,
            http2=False,
        )
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _rate_limit(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_for = self.settings.min_delay_seconds - (now - self._last_request)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, TimeoutError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    async def get(self, url: str) -> FetchResult:
        await self._rate_limit()
        started = time.monotonic()
        response = await self.client.get(url)
        response.raise_for_status()
        return FetchResult(url=str(response.url), text=response.text, status_code=response.status_code, elapsed_seconds=time.monotonic() - started)

    async def close(self) -> None:
        await self.client.aclose()
