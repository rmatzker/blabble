"""Linux sound playback via paplay (PulseAudio / PipeWire)."""
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class PaplaySounds:
    def play(self, path: Path, volume: float) -> None:
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
