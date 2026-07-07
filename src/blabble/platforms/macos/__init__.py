"""macOS backend stub. Implementation lands in v0.2 Phase C.

Planned libraries:
- pynput      → global hotkey + Cmd+V synthesis (requires Accessibility grant)
- pyperclip   → clipboard set (uses pbcopy under the hood)
- rumps       → native NSStatusItem menu bar (preferred over pystray for Mac feel)
- afplay      → blip playback (subprocess, ships with macOS) or reuse sounddevice

Permissions required (one-time, per-bundle-identity):
- Accessibility    → for global hotkey + Cmd+V synthesis
- Microphone       → triggered on first sounddevice access
Both attach to the binary, so a proper .app bundle (py2app or
PyInstaller --windowed) is required for the grant to persist.

Autostart: ~/Library/LaunchAgents/com.matzker.blabble.plist with RunAtLoad=true.

Packaging: PyInstaller --windowed (Apple Silicon + Intel via separate builds,
or universal2 if dependency wheels permit).
"""
from ..base import Backend


async def create_backend(cfg) -> Backend:
    raise NotImplementedError(
        "macOS backend is not yet implemented (planned for v0.2 Phase C). "
        "See platforms/macos/__init__.py for the design notes."
    )
