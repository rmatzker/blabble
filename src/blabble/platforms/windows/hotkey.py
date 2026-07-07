"""Windows global hotkey via pynput.

pynput's GlobalHotKeys runs a listener on its own daemon thread and
invokes callbacks from there. We bridge them back onto the asyncio
loop with call_soon_threadsafe so the rest of the service runs on
a single thread.
"""
import asyncio
import logging
from collections.abc import Callable

from pynput import keyboard

from ..base import AsyncCallback, ShortcutSpec

log = logging.getLogger(__name__)


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

    async def setup(self, shortcuts: list[ShortcutSpec]) -> None:
        bindings: dict[str, Callable[[], None]] = {}
        for s in shortcuts:
            combo = _to_pynput(s.preferred_trigger)
            bindings[combo] = self._make_thread_safe_dispatcher(s.id)
            log.info("binding %s → %s", s.preferred_trigger, combo)

        self._listener = keyboard.GlobalHotKeys(bindings)
        # daemon thread; auto-cleaned on process exit
        self._listener.start()

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
