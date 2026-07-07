"""Windows sound playback via sounddevice.

Using sounddevice (already a core dep for recording) instead of winsound
because winsound has no per-call volume control, which is the whole
point of the SoundsConfig.volume knob.
"""
import logging
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class SounddeviceSounds:
    def play(self, path: Path, volume: float) -> None:
        if not path.exists():
            log.warning("sound file missing: %s", path)
            return
        # Decode + scale on a worker thread so playback never blocks
        # the asyncio loop. sd.play() is itself non-blocking but file
        # I/O isn't.
        threading.Thread(
            target=self._play_blocking,
            args=(path, max(0.0, min(1.0, volume))),
            daemon=True,
        ).start()

    @staticmethod
    def _play_blocking(path: Path, volume: float) -> None:
        try:
            with wave.open(str(path), "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                frames = wf.readframes(wf.getnframes())
            data = np.frombuffer(frames, dtype=np.int16)
            if channels > 1:
                data = data.reshape(-1, channels)
            scaled = (data.astype(np.float32) * volume / 32767.0)
            sd.play(scaled, samplerate=sample_rate)
        except Exception:
            log.exception("playback failed for %s", path)
