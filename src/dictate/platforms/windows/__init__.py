"""Windows backend: pynput hotkey + pyperclip/pynput paste + pystray + sounddevice.

Autostart: drop a .lnk into
%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup pointing at
`pythonw.exe -m dictate` (or the PyInstaller .exe). pythonw avoids the
console window flash; -m dictate still works.

Packaging (future): PyInstaller --windowed --onefile.
"""
from ..base import Backend
from .hotkey import PynputHotkey
from .paste import WindowsPaste
from .sounds import SounddeviceSounds
from .tray import PystrayTray


async def create_backend(cfg) -> Backend:
    tray = PystrayTray() if cfg.tray.enabled else None
    hotkey = PynputHotkey()

    async def _shutdown() -> None:
        hotkey.stop()
        if tray is not None:
            tray.stop()

    return Backend(
        sounds=SounddeviceSounds(),
        paste=WindowsPaste(
            restore_clipboard=cfg.paste.restore_clipboard,
            restore_delay_ms=cfg.paste.restore_delay_ms,
        ),
        hotkey=hotkey,
        tray=tray,
        shutdown=_shutdown,
    )
