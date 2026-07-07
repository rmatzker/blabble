"""Runtime platform dispatcher.

Lazily imports the right backend for the current OS. Platform-specific
deps (dbus_next on Linux, pynput/pystray on Win/Mac) are only loaded
when the matching backend is constructed, so a Linux install never
needs to ship Windows libraries and vice versa.
"""
from __future__ import annotations

import sys

from .base import Backend


async def create_backend(cfg) -> Backend:
    if sys.platform.startswith("linux"):
        from . import linux
        return await linux.create_backend(cfg)
    if sys.platform == "win32":
        from . import windows
        return await windows.create_backend(cfg)
    if sys.platform == "darwin":
        from . import macos
        return await macos.create_backend(cfg)
    raise RuntimeError(f"unsupported platform: {sys.platform}")
