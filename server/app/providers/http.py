"""HTTP compartilhado: retentativas com backoff e download em streaming."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

RETRY_STATUS = {408, 425, 500, 502, 503, 504}

# Cliente único com keep-alive, compartilhado por todas as cenas paralelas
_client = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True,
                       headers={"User-Agent": "AiEditor/0.1"},
                       limits=httpx.Limits(max_connections=20, max_keepalive_connections=20))


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


def request(method: str, url: str, *, retries: int = 3, **kwargs: Any) -> httpx.Response:
    delay = 2.0
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _client.request(method, url, **kwargs)
        except httpx.TransportError as e:
            last_exc = e
        else:
            if resp.status_code == 429:
                wait = rate_limit_wait(resp)
                # sem header ou espera longa (cota diária/mensal): devolve o 429 para o chamador decidir
                if wait is None or wait > MAX_RATE_WAIT or attempt == retries:
                    return resp
                time.sleep(wait + 1)
                continue
            if resp.status_code not in RETRY_STATUS:
                return resp
            last_exc = ProviderError(f"HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    raise ProviderError(f"{method} {url} falhou: {last_exc}") from last_exc


MAX_RATE_WAIT = 65.0


def rate_limit_wait(resp: httpx.Response) -> float | None:
    """Segundos até liberar. X-RateLimit-Reset vem em segundos (Pixabay) ou em timestamp Unix (Pexels)."""
    for header in ("Retry-After", "X-RateLimit-Reset", "X-Ratelimit-Reset"):
        raw = resp.headers.get(header)
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        return max(0.0, value - time.time()) if value > 1e9 else value
    return None


def json_or_raise(resp: httpx.Response, what: str) -> Any:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:500]
    if resp.status_code >= 400:
        raise ProviderError(f"{what}: HTTP {resp.status_code}", resp.status_code, body)
    return body


def download(url: str, dest: Path, headers: dict | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(3):
        try:
            with _client.stream("GET", url, headers=headers, timeout=httpx.Timeout(300.0, connect=15.0)) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            tmp.replace(dest)
            return dest
        except (httpx.HTTPError, OSError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return dest
