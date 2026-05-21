"""Windows paste: clipboard via pyperclip + Ctrl+V via pynput."""
import asyncio
import logging

import pyperclip
from pynput.keyboard import Controller, Key

log = logging.getLogger(__name__)

_kb = Controller()


class WindowsPaste:
    def __init__(self, restore_clipboard: bool = True, restore_delay_ms: int = 400):
        self.restore_clipboard = restore_clipboard
        self.restore_delay_ms = restore_delay_ms

    async def paste_text(self, text: str, *, press_enter_after: bool = False) -> None:
        if not text:
            return

        # pyperclip only sees CF_UNICODETEXT — if the clipboard held an image
        # or file list, we capture None here and the user loses it. Text
        # snippets (URLs, code, notes) round-trip cleanly, which covers the
        # case Rob actually hit.
        prior: str | None = None
        if self.restore_clipboard:
            try:
                prior = pyperclip.paste()
            except pyperclip.PyperclipException:
                prior = None

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

        if self.restore_clipboard and prior is not None:
            await asyncio.sleep(self.restore_delay_ms / 1000.0)
            try:
                pyperclip.copy(prior)
            except pyperclip.PyperclipException:
                log.exception("clipboard restore failed")
