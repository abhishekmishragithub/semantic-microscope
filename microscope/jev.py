"""Async Jev client: one keep-alive connection pool, token bucket, semaphore, retries.

One call carries every question. Never raises for a per-request failure; the caller gets a
JevResult with `error` set so the record is still written. Only auth failure is fatal.
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000  # docs.typesafe.ai/models, 2026-09-20
RETRYABLE = {429, 500, 502, 503, 504, 529}


class AuthError(RuntimeError):
    pass


@dataclass
class JevResult:
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    latency_ms: int = 0
    server_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    request_id: str | None = None
    attempts: int = 0
    n_429: int = 0
    error: str | None = None


@dataclass
class Chaos:
    """Force failures on specific call numbers (1-based) so --chaos is deterministic."""

    force_429_on: int = 2
    force_timeout_on: int = 5


class TokenBucket:
    def __init__(self, rate: float, burst: int | None = None) -> None:
        self.rate, self.capacity = rate, float(burst or max(1, int(rate)))
        self.tokens, self.updated = self.capacity, time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    kind = raw.get("type")
    if kind == "noul":
        return {"kind": kind, "value": raw["noul"]}
    if kind == "score":
        return {
            "kind": kind,
            "value": raw["score"],
            "confidence": raw["confidence"],
            "distribution": raw["probabilities"],
            "legend": raw["legend"],
        }
    if kind == "choice":
        return {
            "kind": kind,
            "value": raw["choice"],
            "confidence": raw["confidence"],
            "distribution": raw["probabilities"],
        }
    raise ValueError(f"unknown answer type {kind!r}")


class JevClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = MODEL,
        rps: float = 15.0,
        concurrency: int = 12,
        max_attempts: int = 4,
        chaos: Chaos | None = None,
    ) -> None:
        self.model, self.max_attempts, self.chaos = model, max_attempts, chaos
        self.concurrency = concurrency
        self.bucket, self.sem = TokenBucket(rps), asyncio.Semaphore(concurrency)
        self.calls = 0
        self.drifted_models: set[str] = set()
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=concurrency + 4),
            http2=False,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def warm(self, n: int | None = None) -> None:
        """Open the TLS connections before the run so the first sentences don't pay for them."""

        async def one() -> None:
            try:
                await self._http.get("https://api.typesafe.ai/", timeout=5.0)
            except httpx.HTTPError:
                pass

        await asyncio.gather(*(one() for _ in range(n or self.concurrency)))

    async def _send(self, body: dict[str, Any], call_no: int, attempt: int) -> httpx.Response:
        if self.chaos and call_no == self.chaos.force_timeout_on:
            raise httpx.ReadTimeout("chaos: forced timeout")
        if self.chaos and call_no == self.chaos.force_429_on:
            return httpx.Response(
                429, headers={"retry-after": "0"}, json={"detail": "chaos: forced 429"}
            )
        return await self._http.post(URL, json=body)

    async def ask(self, state: Any, questions: dict[str, Any]) -> JevResult:
        body = {"state": state, "model": self.model, "questions": questions}
        res = JevResult()
        self.calls += 1
        call_no = self.calls
        async with self.sem:
            for attempt in range(1, self.max_attempts + 1):
                await self.bucket.take()
                res.attempts = attempt
                t0 = time.perf_counter()
                try:
                    r = await self._send(body, call_no, attempt)
                except httpx.HTTPError as e:
                    res.latency_ms = int((time.perf_counter() - t0) * 1000)
                    res.error = f"{type(e).__name__}: {e}"
                    await self._backoff(attempt, None)
                    continue
                res.latency_ms = int((time.perf_counter() - t0) * 1000)
                res.request_id = r.headers.get("x-typesafe-request-id")
                svc = r.headers.get("x-envoy-upstream-service-time")
                res.server_ms = int(svc) if svc and svc.isdigit() else None
                if r.status_code == 200:
                    return self._parse(r, res)
                res.error = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code == 401:
                    raise AuthError(res.error)
                if r.status_code not in RETRYABLE:
                    return res
                if r.status_code == 429:
                    res.n_429 += 1
                await self._backoff(attempt, r.headers.get("retry-after"))
        return res

    def _parse(self, r: httpx.Response, res: JevResult) -> JevResult:
        try:
            data = r.json()
            res.model = data.get("model")
            usage = data.get("usage") or {}
            res.input_tokens = int(usage.get("input_tokens", 0))
            res.output_tokens = int(usage.get("output_tokens", 0))
            res.answers = {k: normalize(v) for k, v in data["answers"].items()}
            res.error = None
        except (ValueError, KeyError, TypeError) as e:
            res.answers, res.error = {}, f"bad response body: {type(e).__name__}: {e}"
        if res.model and res.model != self.model and res.model not in self.drifted_models:
            self.drifted_models.add(res.model)
            print(f"\nWARNING: asked for {self.model}, response says {res.model}", file=sys.stderr)
        return res

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self.max_attempts:
            return
        if retry_after and retry_after.replace(".", "", 1).isdigit():
            delay = float(retry_after)
        else:
            delay = 0.25 * (2 ** (attempt - 1)) + random.uniform(0, 0.1)
        await asyncio.sleep(delay)
