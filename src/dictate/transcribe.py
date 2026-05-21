import asyncio
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Transient failures we'll retry before giving up.
_TRANSIENT_EXC = (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)


async def transcribe(
    server_url: str,
    wav_path: Path,
    *,
    connect_timeout: float = 3.0,
    read_timeout: float = 60.0,
    retries: int = 1,
) -> str:
    """POST the WAV to whisper.cpp and return the transcript text.

    Splits connect vs. read timeouts so an unreachable server fails fast
    (~connect_timeout) instead of waiting the full read budget. Retries
    up to `retries` extra times on transient network errors and HTTP 5xx.
    """
    url = server_url.rstrip("/") + "/v1/audio/transcriptions"
    timeout = httpx.Timeout(
        connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
    )
    max_attempts = max(1, retries + 1)

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                with wav_path.open("rb") as f:
                    files = {"file": (wav_path.name, f, "audio/wav")}
                    data = {"response_format": "json", "model": "whisper-1"}
                    r = await client.post(url, files=files, data=data)
                if 500 <= r.status_code < 600:
                    raise httpx.HTTPStatusError(
                        f"server error {r.status_code}", request=r.request, response=r
                    )
                r.raise_for_status()
                return r.json().get("text", "").strip()
        except _TRANSIENT_EXC as e:
            last_exc = e
            if attempt < max_attempts:
                log.warning(
                    "transcribe attempt %d/%d failed (%s: %s); retrying",
                    attempt, max_attempts, type(e).__name__, e,
                )
                await asyncio.sleep(0.5)
                continue
            raise
        except httpx.HTTPStatusError as e:
            last_exc = e
            if (
                attempt < max_attempts
                and e.response is not None
                and 500 <= e.response.status_code < 600
            ):
                log.warning(
                    "transcribe attempt %d/%d got %s; retrying",
                    attempt, max_attempts, e.response.status_code,
                )
                await asyncio.sleep(0.5)
                continue
            raise

    assert last_exc is not None
    raise last_exc
