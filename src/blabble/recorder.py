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
        # sounddevice only treats None as "system default"; "" is a substring
        # query that matches every device and errors out when several exist.
        self.device = device or None
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
        try:
            self._stream = self._open_stream(self.device)
        except ValueError as e:
            if self.device is None:
                raise
            # Named device not found — common with Bluetooth mics whose
            # names shift between connects (e.g. AirPods on Windows).
            log.warning("device %r unavailable (%s); falling back to system default",
                        self.device, e)
            self._stream = self._open_stream(None)
        self._stream.start()

    def _open_stream(self, device: str | None) -> sd.InputStream:
        return sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=device,
            callback=self._callback,
        )

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


# Speech-level measurement: RMS per 30 ms frame, then a high percentile.
# A whole-clip RMS is diluted by pauses before/after the utterance, so a
# short phrase in a long clip reads as "silent"; the loud frames don't.
_FRAME_MS = 30
_SPEECH_PERCENTILE = 95
# Skip the stream-open transient (a click at the very start of the
# recording), which would otherwise dominate the percentile of short clips.
_SKIP_LEAD_MS = 90


def speech_level(samples: np.ndarray, sample_rate: int) -> float:
    """RMS of the loudest frames (95th percentile of 30 ms frame RMS)."""
    n = max(1, sample_rate * _FRAME_MS // 1000)
    skip = sample_rate * _SKIP_LEAD_MS // 1000
    x = samples[skip:] if samples.size - skip >= n else samples
    x = x[: x.size // n * n].astype(np.float64)
    if x.size == 0:
        return 0.0
    frame_rms = np.sqrt(np.mean(x.reshape(-1, n) ** 2, axis=1))
    return float(np.percentile(frame_rms, _SPEECH_PERCENTILE))


def is_silent(wav_path: Path, *, rms_threshold: float = 200.0,
              min_duration_ms: int = 250) -> tuple[bool, str]:
    """Return (is_silent, reason). Used to skip transcription of accidental
    or empty recordings — whisper.cpp hallucinates phrases like
    '[BLANK_AUDIO]' or 'Thanks for watching!' on silent input.

    rms_threshold is compared against speech_level(), not whole-clip RMS.
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
    level = speech_level(samples, sample_rate)
    log.info("speech level %.0f (threshold %.0f)", level, rms_threshold)
    if level < rms_threshold:
        return True, f"low energy (speech level={level:.0f} < {rms_threshold:.0f})"

    return False, ""
