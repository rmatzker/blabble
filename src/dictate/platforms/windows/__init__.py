"""Windows backend stub. Implementation lands in v0.2 Phase B.

Planned libraries:
- pynput      → global hotkey + Ctrl+V synthesis
- pyperclip   → clipboard set
- pystray     → tray icon + menu
- winsound    → blip playback (or reuse sounddevice for consistency)

Autostart: drop a .lnk into the Startup folder
(%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup).

Packaging: PyInstaller --windowed --onefile (avoids console flash).
"""
from ..base import Backend


async def create_backend(cfg) -> Backend:
    raise NotImplementedError(
        "Windows backend is not yet implemented (planned for v0.2 Phase B). "
        "See platforms/windows/__init__.py for the design notes."
    )
