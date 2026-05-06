"""Windows paste: clipboard via pyperclip + Ctrl+V via pynput."""
import asyncio
import logging

import pyperclip
from pynput.keyboard import Controller, Key

log = logging.getLogger(__name__)

_kb = Controller()


class WindowsPaste:
    async def paste_text(self, text: str, *, press_enter_after: bool = False) -> None:
        if not text:
            return
        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException:
            log.exception("clipboard set failed")
            return

        # Win32's clipboard is event-driven; some apps need a beat to
        # observe the WM_CLIPBOARDUPDATE before they'll honor the paste.
        await asyncio.sleep(0.05)

        with _kb.pressed(Key.ctrl):
            _kb.press("v")
            _kb.release("v")

        if press_enter_after:
            await asyncio.sleep(0.02)
            _kb.press(Key.enter)
            _kb.release(Key.enter)
