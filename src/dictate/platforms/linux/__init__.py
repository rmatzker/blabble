"""Linux backend: dbus_next + paplay + ydotool/wl-copy + KDE portal hotkeys."""
from dbus_next import BusType
from dbus_next.aio import MessageBus

from ..base import Backend
from .hotkey import PortalHotkey
from .paste import WaylandPaste
from .sounds import PaplaySounds
from .tray import SniTray


async def create_backend(cfg) -> Backend:
    bus = await MessageBus(bus_type=BusType.SESSION).connect()

    tray = None
    if cfg.tray.enabled:
        tray = SniTray(bus, cfg.tray.icon_idle, cfg.tray.icon_recording)

    async def _shutdown() -> None:
        bus.disconnect()

    return Backend(
        sounds=PaplaySounds(),
        paste=WaylandPaste(method=cfg.paste.method),
        hotkey=PortalHotkey(bus),
        tray=tray,
        shutdown=_shutdown,
    )
