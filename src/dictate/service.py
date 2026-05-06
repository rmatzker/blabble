import asyncio
import logging
import os
import re
import signal as signal_mod
import sys
import uuid
from pathlib import Path

from . import config as config_mod
from . import recorder, sounds_gen, transcribe, transform
from .platforms import create_backend
from .platforms.base import Backend, MenuItemSpec, ShortcutSpec

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
        self.backend: Backend | None = None
        self._lock = asyncio.Lock()
        self._watchdog: asyncio.Task | None = None

    def _resolve_sound(self, value: str, default_name: str) -> Path:
        if value == "auto":
            return config_mod.SOUNDS_DIR / default_name
        return Path(value).expanduser()

    async def run(self) -> None:
        sounds_gen.ensure_blips(
            config_mod.SOUNDS_DIR / "start.wav",
            config_mod.SOUNDS_DIR / "stop.wav",
        )

        self.backend = await create_backend(self.cfg)

        if self.backend.tray is not None:
            menu_items = [
                MenuItemSpec(
                    id=1,
                    label="Auto-Enter after paste",
                    toggle=True,
                    get_toggle_state=lambda: self.cfg.paste.press_enter_after,
                    on_click=self._toggle_press_enter,
                ),
                MenuItemSpec(id=2, item_type="separator"),
                MenuItemSpec(
                    id=3,
                    label="Quit",
                    on_click=self._quit_action,
                ),
            ]
            await self.backend.tray.setup(
                menu_items,
                on_activate=lambda: asyncio.create_task(self.toggle()),
            )

        await self.backend.hotkey.setup([
            ShortcutSpec(
                id="toggle",
                description="Start/stop dictation",
                preferred_trigger=self.cfg.hotkey.toggle,
            )
        ])
        self.backend.hotkey.on_shortcut("toggle", self.toggle)

        log.info("dictate ready (hotkey=%s)", self.cfg.hotkey.toggle)

        await _wait_for_shutdown_signal()
        log.info("shutdown signal received")
        if self.backend.shutdown is not None:
            try:
                await self.backend.shutdown()
            except Exception:
                log.exception("backend shutdown failed")

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
        if self.backend and self.backend.tray:
            self.backend.tray.notify_menu_item_changed(1)

    async def _quit_action(self) -> None:
        log.info("quit requested via tray menu")
        os.kill(os.getpid(), signal_mod.SIGTERM)

    async def _start(self) -> None:
        try:
            if self.cfg.sounds.enabled:
                self.backend.sounds.play(self.start_sound, self.cfg.sounds.volume)
            self.recorder.start()
            self.recording = True
            if self.backend.tray:
                self.backend.tray.set_state("recording")
            log.info("recording started")
            if self.cfg.recording.max_duration_sec > 0:
                self._watchdog = asyncio.create_task(self._auto_stop_after(self.cfg.recording.max_duration_sec))
        except Exception:
            log.exception("start failed")
            self.recording = False
            if self.backend.tray:
                self.backend.tray.set_state("idle")

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
            if self.backend.tray:
                self.backend.tray.set_state("idle")
            if self.cfg.sounds.enabled:
                self.backend.sounds.play(self.stop_sound, self.cfg.sounds.volume)

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
                await self.backend.paste.paste_text(
                    text,
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
            d = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp")
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


async def _wait_for_shutdown_signal() -> None:
    """Block until SIGINT/SIGTERM. Falls back to signal.signal() on Windows
    where loop.add_signal_handler is unsupported."""
    loop = asyncio.get_event_loop()
    stop = loop.create_future()

    def _set():
        if not stop.done():
            stop.set_result(None)

    sigs = (signal_mod.SIGINT, signal_mod.SIGTERM)
    if sys.platform == "win32":
        # add_signal_handler raises NotImplementedError on Windows ProactorEventLoop.
        # signal.signal() works but only fires between bytecode instructions, so we
        # also poll periodically as a safety net.
        for sig in sigs:
            try:
                signal_mod.signal(sig, lambda *_: loop.call_soon_threadsafe(_set))
            except (ValueError, OSError):
                pass
        while not stop.done():
            try:
                await asyncio.wait_for(asyncio.shield(stop), timeout=0.5)
            except asyncio.TimeoutError:
                continue
    else:
        for sig in sigs:
            loop.add_signal_handler(sig, _set)
        await stop


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    cfg = config_mod.load()
    asyncio.run(DictateService(cfg).run())
