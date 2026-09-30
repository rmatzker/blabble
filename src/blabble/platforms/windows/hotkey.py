"""Windows global hotkey via pynput.

pynput's GlobalHotKeys runs a listener on its own daemon thread and
invokes callbacks from there. We bridge them back onto the asyncio
loop with call_soon_threadsafe so the rest of the service runs on
a single thread.
"""
import asyncio
import logging
import time
from collections.abc import Callable

from pynput import keyboard

from ..base import AsyncCallback, ShortcutSpec

log = logging.getLogger(__name__)

# Single side-specific modifiers that can be bound as a lone *tap*
# (press + release with no other key in between). GlobalHotKeys can't do
# this: its listener canonicalizes ctrl_r → ctrl, losing the side.
_TAP_KEYS = {
    "rightctrl": keyboard.Key.ctrl_r,
    "rctrl": keyboard.Key.ctrl_r,
    "ctrl_r": keyboard.Key.ctrl_r,
    "rightalt": keyboard.Key.alt_r,
    "ralt": keyboard.Key.alt_r,
    "alt_r": keyboard.Key.alt_r,
    "rightshift": keyboard.Key.shift_r,
    "rshift": keyboard.Key.shift_r,
    "shift_r": keyboard.Key.shift_r,
}

# A hold longer than this isn't a tap (e.g. Ctrl held while mouse-clicking,
# which the keyboard listener can't see).
_TAP_MAX_SEC = 0.5


def _tap_key(combo: str) -> keyboard.Key | None:
    return _TAP_KEYS.get(combo.strip().lower().replace(" ", "").replace("+", ""))


def _to_pynput(combo: str) -> str:
    """Translate canonical "Ctrl+Shift+Space" → pynput "<ctrl>+<shift>+<space>".

    pynput parses single chars literally and named keys wrapped in
    angle brackets and lowercased.
    """
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    out = []
    for p in parts:
        low = p.lower()
        # Map Linux-style Meta to Windows key (pynput exposes it as <cmd>).
        if low in ("meta", "win", "windows", "super"):
            low = "cmd"
        if len(p) == 1:
            out.append(low)
        else:
            out.append(f"<{low}>")
    return "+".join(out)


class PynputHotkey:
    def __init__(self):
        self._loop = asyncio.get_event_loop()
        self._callbacks: dict[str, AsyncCallback] = {}
        self._listener: keyboard.GlobalHotKeys | None = None
        self._tap_listener: keyboard.Listener | None = None
        self._taps: dict[keyboard.Key, Callable[[], None]] = {}
        self._tap_armed: keyboard.Key | None = None
        self._tap_down_at = 0.0

    async def setup(self, shortcuts: list[ShortcutSpec]) -> None:
        bindings: dict[str, Callable[[], None]] = {}
        for s in shortcuts:
            tap = _tap_key(s.preferred_trigger)
            if tap is not None:
                self._taps[tap] = self._make_thread_safe_dispatcher(s.id)
                log.info("binding %s → tap %s", s.preferred_trigger, tap.name)
                continue
            combo = _to_pynput(s.preferred_trigger)
            bindings[combo] = self._make_thread_safe_dispatcher(s.id)
            log.info("binding %s → %s", s.preferred_trigger, combo)

        # daemon threads; auto-cleaned on process exit
        if bindings:
            self._listener = keyboard.GlobalHotKeys(bindings)
            self._listener.start()
        if self._taps:
            self._tap_listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._tap_listener.start()

    def _on_press(self, key) -> None:
        if key in self._taps:
            # Ignore auto-repeat while held; only the first press arms.
            if self._tap_armed != key:
                self._tap_armed = key
                self._tap_down_at = time.monotonic()
        else:
            # Any other key while held means it's a chord (Ctrl+C), not a tap.
            self._tap_armed = None

    def _on_release(self, key) -> None:
        if key in self._taps and self._tap_armed == key:
            self._tap_armed = None
            if time.monotonic() - self._tap_down_at <= _TAP_MAX_SEC:
                self._taps[key]()

    def on_shortcut(self, shortcut_id: str, callback: AsyncCallback) -> None:
        self._callbacks[shortcut_id] = callback

    def _make_thread_safe_dispatcher(self, shortcut_id: str) -> Callable[[], None]:
        def _fire():
            cb = self._callbacks.get(shortcut_id)
            if cb is None:
                log.debug("hotkey %s fired but no handler", shortcut_id)
                return
            self._loop.call_soon_threadsafe(asyncio.create_task, cb())
        return _fire

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        if self._tap_listener is not None:
            self._tap_listener.stop()
