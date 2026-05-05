"""StatusNotifierItem implementation for KDE Plasma's system tray.

Exposes a tray icon that swaps between idle/recording icons.
Left-click toggles recording. To quit: ``systemctl --user stop dictate``.
"""
import logging
from collections.abc import Callable

from dbus_next import Message, PropertyAccess
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method, signal

log = logging.getLogger(__name__)

SNI_PATH = "/StatusNotifierItem"
SNI_IFACE = "org.kde.StatusNotifierItem"
WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"


class StatusNotifierItem(ServiceInterface):
    def __init__(self, idle_icon: str, recording_icon: str):
        super().__init__(SNI_IFACE)
        self._idle_icon = idle_icon
        self._recording_icon = recording_icon
        self._state = "idle"
        self._tooltip_title = "dictate · idle"
        self.on_activate: Callable[[], None] | None = None

    # ---- mutable state ----
    def set_state(self, state: str) -> None:
        self._state = state
        if state == "recording":
            self._tooltip_title = "dictate · recording"
        else:
            self._tooltip_title = "dictate · idle"
        self.NewIcon()
        self.NewToolTip()

    @property
    def _current_icon(self) -> str:
        return self._recording_icon if self._state == "recording" else self._idle_icon

    # ---- read-only properties ----
    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "dictate"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return "dictate"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "Active"

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return self._current_icon

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def AttentionMovieName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(sa(iiay)ss)":
        return [self._current_icon, [], self._tooltip_title, ""]

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "o":
        return "/NoMenu"

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "u":
        return 0

    # ---- methods ----
    @method()
    def Activate(self, x: "i", y: "i"):
        if self.on_activate is not None:
            self.on_activate()

    @method()
    def SecondaryActivate(self, x: "i", y: "i"):
        pass

    @method()
    def ContextMenu(self, x: "i", y: "i"):
        pass

    @method()
    def Scroll(self, delta: "i", orientation: "s"):
        pass

    # ---- signals (notify watcher of changes) ----
    @signal()
    def NewIcon(self):
        pass

    @signal()
    def NewToolTip(self):
        pass

    @signal()
    def NewAttentionIcon(self):
        pass

    @signal()
    def NewOverlayIcon(self):
        pass

    @signal()
    def NewStatus(self) -> "s":
        return "Active"

    @signal()
    def NewTitle(self):
        pass


async def register_with_watcher(bus: MessageBus, sni: StatusNotifierItem) -> None:
    """Export the SNI on our bus and register with KDE's watcher."""
    bus.export(SNI_PATH, sni)
    reply = await bus.call(
        Message(
            destination=WATCHER_BUS,
            path=WATCHER_PATH,
            interface=WATCHER_IFACE,
            member="RegisterStatusNotifierItem",
            signature="s",
            body=[bus.unique_name],
        )
    )
    if reply is None or reply.message_type.name == "ERROR":
        err = getattr(reply, "body", ["unknown"])[0] if reply else "no reply"
        log.warning("StatusNotifierWatcher registration failed: %s", err)
    else:
        log.info("registered tray icon with %s as %s", WATCHER_BUS, bus.unique_name)
