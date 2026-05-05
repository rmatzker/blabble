import asyncio
import logging
import signal as posix_signal
import sys
import uuid
from pathlib import Path

from dbus_next import BusType
from dbus_next.aio import MessageBus

from . import config as config_mod
from . import paste, recorder, sounds, transcribe, transform
from .hotkey import GlobalShortcuts
from .tray import StatusNotifierItem, register_with_watcher

log = logging.getLogger(__name__)


class DictateService:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg
        self.recording = False
        self.recorder = recorder.Recorder(
            sample_rate=cfg.recording.sample_rate,
            device=cfg.recording.device,
        )
        self.start_sound = self._resolve_sound(cfg.sounds.start, "start.wav")
        self.stop_sound = self._resolve_sound(cfg.sounds.stop, "stop.wav")
        self.tray: StatusNotifierItem | None = None
        self._lock = asyncio.Lock()

    def _resolve_sound(self, value: str, default_name: str) -> Path:
        if value == "auto":
            return config_mod.SOUNDS_DIR / default_name
        return Path(value).expanduser()

    async def run(self) -> None:
        sounds.ensure_blips(
            config_mod.SOUNDS_DIR / "start.wav",
            config_mod.SOUNDS_DIR / "stop.wav",
        )

        bus = await MessageBus(bus_type=BusType.SESSION).connect()

        if self.cfg.tray.enabled:
            self.tray = StatusNotifierItem(
                idle_icon=self.cfg.tray.icon_idle,
                recording_icon=self.cfg.tray.icon_recording,
            )
            self.tray.on_activate = lambda: asyncio.create_task(self.toggle())
            await register_with_watcher(bus, self.tray)

        gs = GlobalShortcuts(bus)
        await gs.setup([
            {
                "id": "toggle",
                "description": "Start/stop dictation",
                "preferred_trigger": self.cfg.hotkey.toggle,
            }
        ])
        gs.on_shortcut("toggle", self.toggle)

        log.info("dictate ready (hotkey=%s)", self.cfg.hotkey.toggle)

        loop = asyncio.get_event_loop()
        stop = loop.create_future()
        for sig in (posix_signal.SIGINT, posix_signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
        await stop
        log.info("shutdown signal received")

    async def toggle(self) -> None:
        async with self._lock:
            if self.recording:
                await self._stop()
            else:
                await self._start()

    async def _start(self) -> None:
        try:
            if self.cfg.sounds.enabled:
                sounds.play(self.start_sound)
            self.recorder.start()
            self.recording = True
            if self.tray:
                self.tray.set_state("recording")
            log.info("recording started")
        except Exception:
            log.exception("start failed")
            self.recording = False
            if self.tray:
                self.tray.set_state("idle")

    async def _stop(self) -> None:
        wav_path = self._wav_path()
        try:
            self.recording = False
            if self.tray:
                self.tray.set_state("idle")
            if self.cfg.sounds.enabled:
                sounds.play(self.stop_sound)

            self.recorder.stop(wav_path)
            log.info("recording saved: %s", wav_path)

            text = await transcribe.transcribe(self.cfg.server.url, wav_path)
            log.info("transcribed (raw): %r", text)

            steps = [
                transform.Transformation(t.name, t.find, t.replace, t.regex)
                for t in self.cfg.transformations
            ]
            text = transform.apply_pipeline(text, steps)
            log.info("transcribed (final): %r", text)

            if text:
                await paste.paste_at_cursor(
                    text,
                    method=self.cfg.paste.method,
                    press_enter_after=self.cfg.paste.press_enter_after,
                )
        except Exception:
            log.exception("stop failed")
        finally:
            if not self.cfg.recording.keep_local_copies:
                wav_path.unlink(missing_ok=True)
            else:
                self._enforce_max_copies()

    def _wav_path(self) -> Path:
        if self.cfg.recording.keep_local_copies:
            d = config_mod.RECORDINGS_DIR
        else:
            d = Path("/tmp")
        d.mkdir(parents=True, exist_ok=True)
        return d / f"dictate-{uuid.uuid4().hex[:8]}.wav"

    def _enforce_max_copies(self) -> None:
        d = config_mod.RECORDINGS_DIR
        if not d.exists():
            return
        files = sorted(d.glob("dictate-*.wav"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - self.cfg.recording.max_local_copies
        for f in files[: max(0, excess)]:
            f.unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    cfg = config_mod.load()
    asyncio.run(DictateService(cfg).run())
