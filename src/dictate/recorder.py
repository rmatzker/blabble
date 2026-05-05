import logging
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, sample_rate: int = 16000, channels: int = 1, device: str | None = None):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("sounddevice status: %s", status)
        self._frames.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("recorder already started")
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self, output_path: Path) -> Path:
        if self._stream is None:
            raise RuntimeError("recorder not started")
        self._stream.stop()
        self._stream.close()
        self._stream = None

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._frames:
            data = np.concatenate(self._frames, axis=0)
        else:
            data = np.zeros((0, self.channels), dtype=np.int16)

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(data.tobytes())
        return output_path

    @property
    def is_recording(self) -> bool:
        return self._stream is not None


def is_silent(wav_path: Path, *, rms_threshold: float = 200.0,
              min_duration_ms: int = 250) -> tuple[bool, str]:
    """Return (is_silent, reason). Used to skip transcription of accidental
    or empty recordings — whisper.cpp hallucinates phrases like
    '[BLANK_AUDIO]' or 'Thanks for watching!' on silent input.
    """
    with wave.open(str(wav_path), "rb") as wf:
        n_frames = wf.getnframes()
        sample_rate = wf.getframerate()
        frames = wf.readframes(n_frames)

    duration_ms = int(1000 * n_frames / sample_rate) if sample_rate else 0
    if duration_ms < min_duration_ms:
        return True, f"too short ({duration_ms}ms < {min_duration_ms}ms)"

    if not frames:
        return True, "empty wav"

    samples = np.frombuffer(frames, dtype=np.int16)
    if samples.size == 0:
        return True, "no samples"
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms < rms_threshold:
        return True, f"low energy (rms={rms:.0f} < {rms_threshold:.0f})"

    return False, ""
