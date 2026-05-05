import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


async def transcribe(server_url: str, wav_path: Path, *, timeout: float = 60.0) -> str:
    url = server_url.rstrip("/") + "/v1/audio/transcriptions"
    async with httpx.AsyncClient(timeout=timeout) as client:
        with wav_path.open("rb") as f:
            files = {"file": (wav_path.name, f, "audio/wav")}
            data = {"response_format": "json", "model": "whisper-1"}
            r = await client.post(url, files=files, data=data)
            r.raise_for_status()
            return r.json().get("text", "").strip()
