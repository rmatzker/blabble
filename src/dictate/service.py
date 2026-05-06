import asyncio
import logging
import os
import re
import signal as posix_signal
import sys
import uuid
from pathlib import Path

from dbus_next import BusType
from dbus_next.aio import MessageBus

from . import config as config_mod
from . import paste, recorder, sounds, transcribe, transform
from .hotkey import GlobalShortcuts
from .menu import MENU_PATH, DBusMenu, MenuItem
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
        self.menu: DBusMenu | None = None
        self._lock = asyncio.Lock()
        self._watchdog: asyncio.Task | None = None

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
            self.menu = DBusMenu([
                MenuItem(
                    id=1,
                    label="Auto-Enter after paste",
                    toggle_type="checkmark",
                    get_toggle_state=lambda: self.cfg.paste.press_enter_after,
                    on_click=self._toggle_press_enter,
                ),
                MenuItem(id=2, item_type="separator"),
                MenuItem(
                    id=3,
                    label="Quit",
                    on_click=self._quit_action,
                ),
            ])
            bus.export(MENU_PATH, self.menu)

            self.tray = StatusNotifierItem(
                idle_icon=self.cfg.tray.icon_idle,
                recording_icon=self.cfg.tray.icon_recording,
                menu_path=MENU_PATH,
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

    async def _toggle_press_enter(self) -> None:
        new_value = not self.cfg.paste.press_enter_after
        self.cfg.paste.press_enter_after = new_value
        log.info("press_enter_after toggled via tray menu: %s", new_value)
        try:
            config_mod.update_press_enter_after(new_value)
        except Exception:
            log.exception("failed to persist press_enter_after to config.toml")
        if self.menu:
            self.menu.notify_toggle_changed(1)

    async def _quit_action(self) -> None:
        log.info("quit requested via tray menu")
        os.kill(os.getpid(), posix_signal.SIGTERM)

    async def _start(self) -> None:
        try:
            if self.cfg.sounds.enabled:
                sounds.play(self.start_sound, self.cfg.sounds.volume)
            self.recorder.start()
            self.recording = True
            if self.tray:
                self.tray.set_state("recording")
            log.info("recording started")
            if self.cfg.recording.max_duration_sec > 0:
                self._watchdog = asyncio.create_task(self._auto_stop_after(self.cfg.recording.max_duration_sec))
        except Exception:
            log.exception("start failed")
            self.recording = False
            if self.tray:
                self.tray.set_state("idle")

    async def _auto_stop_after(self, seconds: int) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        log.warning("recording exceeded max_duration_sec=%ds, auto-stopping", seconds)
        await self.toggle()

    async def _stop(self) -> None:
        wav_path = self._wav_path()
        try:
            self.recording = False
            if self._watchdog and not self._watchdog.done():
                self._watchdog.cancel()
            self._watchdog = None
            if self.tray:
                self.tray.set_state("idle")
            if self.cfg.sounds.enabled:
                sounds.play(self.stop_sound, self.cfg.sounds.volume)

            self.recorder.stop(wav_path)
            log.info("recording saved: %s", wav_path)

            silent, reason = recorder.is_silent(
                wav_path,
                rms_threshold=self.cfg.recording.silence_rms_threshold,
                min_duration_ms=self.cfg.recording.min_duration_ms,
            )
            if silent:
                log.info("skipping transcription: %s", reason)
                return

            text = await transcribe.transcribe(self.cfg.server.url, wav_path)
            log.info("transcribed (raw): %r", text)

            steps = [
                transform.Transformation(t.name, t.find, t.replace, t.regex)
                for t in self.cfg.transformations
            ]
            text = transform.apply_pipeline(text, steps)
            log.info("transcribed (final): %r", text)

            for pattern in self.cfg.recording.discard_if_matches:
                if re.match(pattern, text):
                    log.info("discarding hallucination match (%r): %r", pattern, text)
                    return

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
