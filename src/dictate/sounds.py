import logging
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100


def _gen_sweep(start_hz: float, end_hz: float, duration_s: float = 0.06) -> bytes:
    n = int(duration_s * SAMPLE_RATE)
    freq = np.linspace(start_hz, end_hz, n)
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    tone = np.sin(phase)

    attack = int(0.005 * SAMPLE_RATE)
    decay = int(0.015 * SAMPLE_RATE)
    env = np.ones(n)
    env[:attack] = np.linspace(0, 1, attack)
    env[-decay:] = np.linspace(1, 0, decay)

    samples = (tone * env * 0.15 * 32767).astype(np.int16)
    return samples.tobytes()


def _write_wav(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(data)


def ensure_blips(start_path: Path, stop_path: Path) -> None:
    if not start_path.exists():
        _write_wav(start_path, _gen_sweep(600, 1000))
        log.info("generated %s", start_path)
    if not stop_path.exists():
        _write_wav(stop_path, _gen_sweep(1000, 600))
        log.info("generated %s", stop_path)


def play(path: Path, volume: float = 1.0) -> None:
    if not shutil.which("paplay"):
        log.warning("paplay not found, skipping sound %s", path)
        return
    if not path.exists():
        log.warning("sound file missing: %s", path)
        return
    pa_volume = max(0, min(65536, int(volume * 65536)))
    subprocess.Popen(
        ["paplay", f"--volume={pa_volume}", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
