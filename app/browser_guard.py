from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

_MAX_CONCURRENT_BROWSERS = 2
_BASE_BACKOFF_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 600.0

_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BROWSERS)
_state_lock = asyncio.Lock()
_failures = 0
_blocked_until = 0.0


class BrowserCircuitOpen(RuntimeError):
    pass


def is_browser_resource_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "resource temporarily unavailable",
        "connection closed while reading from the driver",
        "can't start new thread",
        "cannot start new thread",
        "pthread_create",
        "eagain",
        "too many open files",
        "browser has been closed",
    )
    return any(marker in text for marker in markers)


async def _record_resource_failure(exc: BaseException, label: str) -> None:
    global _failures, _blocked_until
    if not is_browser_resource_error(exc):
        return

    async with _state_lock:
        _failures = min(_failures + 1, 8)
        delay = min(
            _MAX_BACKOFF_SECONDS,
            _BASE_BACKOFF_SECONDS * (2 ** (_failures - 1)),
        )
        _blocked_until = max(_blocked_until, time.monotonic() + delay)

    print(
        f"IRIS_BROWSER_CIRCUIT_OPEN label={label} "
        f"failures={_failures} cooldown={int(delay)}s "
        f"error={type(exc).__name__}:{str(exc)[:220]}",
        flush=True,
    )


async def _record_success() -> None:
    global _failures, _blocked_until
    async with _state_lock:
        _failures = 0
        _blocked_until = 0.0


@asynccontextmanager
async def browser_slot(label: str):
    now = time.monotonic()
    async with _state_lock:
        remaining = _blocked_until - now
    if remaining > 0:
        raise BrowserCircuitOpen(
            f"Chromium em cooldown por mais {int(remaining) + 1}s"
        )

    acquired = False
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=45.0)
        acquired = True

        now = time.monotonic()
        async with _state_lock:
            remaining = _blocked_until - now
        if remaining > 0:
            raise BrowserCircuitOpen(
                f"Chromium em cooldown por mais {int(remaining) + 1}s"
            )

        try:
            yield
        except Exception as exc:
            await _record_resource_failure(exc, label)
            raise
        else:
            await _record_success()
    finally:
        if acquired:
            _semaphore.release()
