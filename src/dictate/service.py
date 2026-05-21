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
        self._shutdown: asyncio.Future | None = None

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

        self._shutdown = asyncio.get_event_loop().create_future()
        _install_signal_handlers(self._shutdown)
        await self._shutdown
        log.info("shutdown requested")
        if self.backend.shutdown is not None:
            try:
                await self.backend.shutdown()
            except Exception:
                log.exception("backend shutdown failed")

    async def toggle(self) -> None:
        # The lock only guards the start/stop *transition* — not the
        # transcription that follows a stop. Otherwise a hung backend
        # would block the hotkey for the full read timeout.
        async with self._lock:
            if self.recording:
                wav_path = await self._stop_recording()
            else:
                await self._start()
                return
        if wav_path is not None:
            await self._transcribe_and_paste(wav_path)

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
        if self._shutdown is not None and not self._shutdown.done():
            self._shutdown.set_result(None)

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

    async def _stop_recording(self) -> Path | None:
        """End the current recording and return the WAV path to transcribe,
        or None if there's nothing to send (silence, or recorder failure).
        Runs under the toggle lock — keep it fast."""
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
                self._cleanup_wav(wav_path)
                return None
            return wav_path
        except Exception:
            log.exception("stop failed")
            self._cleanup_wav(wav_path)
            return None

    async def _transcribe_and_paste(self, wav_path: Path) -> None:
        """Sent the WAV to the backend, transform the result, and paste.
        Runs *outside* the toggle lock so a hung/unreachable backend doesn't
        block the hotkey."""
        try:
            text = await transcribe.transcribe(
                self.cfg.server.url,
                wav_path,
                connect_timeout=self.cfg.server.connect_timeout,
                read_timeout=self.cfg.server.read_timeout,
                retries=self.cfg.server.retries,
            )
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
                # When auto-enter is off, append a trailing space so back-to-back
                # dictations don't run together (we have no way to inspect the
                # cursor's surroundings before pasting). Enter mode handles the
                # separation itself, so no space needed there.
                if not self.cfg.paste.press_enter_after and not text.endswith((" ", "\t", "\n")):
                    text = text + " "
                await self.backend.paste.paste_text(
                    text,
                    press_enter_after=self.cfg.paste.press_enter_after,
                )
        except Exception:
            log.exception("transcription/paste failed")
            self._signal_error()
        finally:
            # Whether transcription succeeded or not, treat the WAV the same:
            # _wav_path already chose /tmp vs. RECORDINGS_DIR based on
            # keep_local_copies, so users who opted into keeping recordings
            # get failures saved with rotation; everyone else gets a clean
            # delete and no orphan files piling up.
            self._cleanup_wav(wav_path)

    def _cleanup_wav(self, wav_path: Path) -> None:
        if not self.cfg.recording.keep_local_copies:
            wav_path.unlink(missing_ok=True)
        else:
            self._enforce_max_copies()

    def _signal_error(self) -> None:
        if not self.backend or not self.backend.tray:
            return
        self.backend.tray.set_state("error")
        asyncio.create_task(self._clear_error_after(5.0))

    async def _clear_error_after(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        # Only clear if we're not mid-recording — don't stomp a fresh start.
        if not self.recording and self.backend and self.backend.tray:
            self.backend.tray.set_state("idle")

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


def _install_signal_handlers(shutdown: asyncio.Future) -> None:
    """Wire SIGINT/SIGTERM to set the shutdown future. Uses asyncio's
    add_signal_handler on POSIX, signal.signal() on Windows (where the
    asyncio API raises NotImplementedError)."""
    loop = asyncio.get_event_loop()

    def _set():
        if not shutdown.done():
            shutdown.set_result(None)

    sigs = (signal_mod.SIGINT, signal_mod.SIGTERM)
    if sys.platform == "win32":
        for sig in sigs:
            try:
                signal_mod.signal(sig, lambda *_: loop.call_soon_threadsafe(_set))
            except (ValueError, OSError):
                pass
    else:
        for sig in sigs:
            loop.add_signal_handler(sig, _set)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    cfg = config_mod.load()
    asyncio.run(DictateService(cfg).run())
