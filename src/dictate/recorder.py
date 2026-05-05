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
