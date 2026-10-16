"""Polite JSON fetcher with on-disk TTL cache, throttling, and retries."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from fbm.config import DATA_DIR

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
RETRYABLE = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, detail: str):
        super().__init__(f"GET {url} failed ({status}): {detail}")
        self.url = url
        self.status = status


class Fetcher:
    def __init__(
        self,
        cache_dir: Path = DATA_DIR / "cache" / "http",
        min_interval: float = 0.25,
        timeout: float = 45.0,
        max_attempts: int = 4,
        client: httpx.Client | None = None,
    ):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self._client = client or httpx.Client(
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"}, timeout=timeout, follow_redirects=True
        )
        self._lock = threading.Lock()
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    def _cache_path(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None = None) -> Path:
        key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params or {}))
        if headers:
            key += "|" + "&".join(f"{k}={headers[k]}" for k in sorted(headers))
        return self.cache_dir / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        ttl: float = 3600.0,
        cache: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Fetch JSON. `ttl` seconds of cache freshness (0 = always refetch); `cache=False` skips disk."""
        path = self._cache_path(url, params, headers)
        if cache and ttl > 0 and path.exists() and (time.time() - path.stat().st_mtime) < ttl:
            return json.loads(path.read_text(encoding="utf-8"))

        last_error = "no attempt"
        status: int | None = None
        for attempt in range(self.max_attempts):
            self._throttle()
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as err:
                last_error, status = str(err), None
            else:
                status = resp.status_code
                if status == 200:
                    data = resp.json()
                    if cache:
                        self._write_cache(path, resp.text)
                    return data
                last_error = resp.text[:200]
                if status not in RETRYABLE:
                    break
            time.sleep(min(2**attempt, 10))
        raise FetchError(url, status, last_error)

    def _write_cache(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
