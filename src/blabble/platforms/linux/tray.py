"""StatusNotifierItem + DBusMenu for KDE Plasma's system tray.

SNI exposes the icon (idle vs recording) and dispatches left-click to a
toggle callback. DBusMenu (com.canonical.dbusmenu) provides the right-click
menu — flat list with standard items, separators, and checkmark toggles.
"""
import asyncio
import logging
from collections.abc import Callable

from dbus_next import Message, PropertyAccess, Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method, signal

from ..base import MenuItemSpec, TrayState

log = logging.getLogger(__name__)

SNI_PATH = "/StatusNotifierItem"
SNI_IFACE = "org.kde.StatusNotifierItem"
WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
MENU_PATH = "/MenuBar"
MENU_IFACE = "com.canonical.dbusmenu"


class _DBusMenu(ServiceInterface):
    def __init__(self, items: list[MenuItemSpec]):
        super().__init__(MENU_IFACE)
        self._items = {it.id: it for it in items}
        self._order = [it.id for it in items]
        self._revision = 0
        self._pending_updates: list = []

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "u":
        return 3

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> "s":
        return "ltr"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "normal"

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "as":
        return []

    @method()
    def GetLayout(self, parentId: "i", recursionDepth: "i", propertyNames: "as") -> "u(ia{sv}av)":
        wanted = list(propertyNames) if propertyNames else None
        if parentId != 0:
            return [self._revision, [parentId, {}, []]]
        children = []
        for item_id in self._order:
            it = self._items[item_id]
            children.append(Variant("(ia{sv}av)", [item_id, _item_props(it, wanted), []]))
        root_props = {"children-display": Variant("s", "submenu")}
        return [self._revision, [0, root_props, children]]

    @method()
    def GetGroupProperties(self, ids: "ai", propertyNames: "as") -> "a(ia{sv})":
        wanted = list(propertyNames) if propertyNames else None
        target = list(ids) if ids else list(self._items.keys())
        out = []
        for item_id in target:
            it = self._items.get(item_id)
            if it is not None:
                out.append([item_id, _item_props(it, wanted)])
        return out

    @method()
    def GetProperty(self, id: "i", name: "s") -> "v":
        it = self._items.get(id)
        if it is not None:
            props = _item_props(it, [name])
            if name in props:
                return props[name]
        return Variant("s", "")

    @method()
    def Event(self, id: "i", eventId: "s", data: "v", timestamp: "u"):
        if eventId != "clicked":
            return
        it = self._items.get(id)
        if it is None or it.on_click is None:
            return
        asyncio.create_task(self._dispatch(it))

    async def _dispatch(self, item: MenuItemSpec) -> None:
        try:
            await item.on_click()
        except Exception:
            log.exception("menu click handler failed for item %d", item.id)

    @method()
    def EventGroup(self, events: "a(isvu)") -> "ai":
        failed = []
        for evt in events:
            try:
                self.Event(evt[0], evt[1], evt[2], evt[3])
            except Exception:
                failed.append(evt[0])
        return failed

    @method()
    def AboutToShow(self, id: "i") -> "b":
        return False

    @method()
    def AboutToShowGroup(self, ids: "ai") -> "aiai":
        return [[], []]

    @signal()
    def ItemsPropertiesUpdated(self) -> "a(ia{sv})a(ias)":
        upd = self._pending_updates
        self._pending_updates = []
        return [upd, []]

    @signal()
    def LayoutUpdated(self) -> "ui":
        return [self._revision, 0]

    def notify_changed(self, item_id: int) -> None:
        it = self._items.get(item_id)
        if it is None:
            return
        self._pending_updates = [[item_id, _item_props(it, None)]]
        self.ItemsPropertiesUpdated()


def _item_props(item: MenuItemSpec, requested: list[str] | None) -> dict[str, Variant]:
    props: dict[str, Variant] = {}
    if item.item_type == "separator":
        props["type"] = Variant("s", "separator")
    else:
        props["label"] = Variant("s", item.label)
    props["enabled"] = Variant("b", True)
    props["visible"] = Variant("b", True)
    if item.toggle:
        props["toggle-type"] = Variant("s", "checkmark")
        on = bool(item.get_toggle_state and item.get_toggle_state())
        props["toggle-state"] = Variant("i", 1 if on else 0)
    if requested:
        return {k: v for k, v in props.items() if k in requested}
    return props


class _StatusNotifierItem(ServiceInterface):
    def __init__(self, idle_icon: str, recording_icon: str, error_icon: str):
        super().__init__(SNI_IFACE)
        self._idle_icon = idle_icon
        self._recording_icon = recording_icon
        self._error_icon = error_icon
        self._state: TrayState = "idle"
        self._tooltip_title = "blabble · idle"
        self.on_activate: Callable[[], None] | None = None

    def set_state(self, state: TrayState) -> None:
        self._state = state
        if state == "recording":
            self._tooltip_title = "blabble · recording"
        elif state == "error":
            self._tooltip_title = "blabble · transcription failed"
        else:
            self._tooltip_title = "blabble · idle"
        self.NewIcon()
        self.NewToolTip()

    @property
    def _current_icon(self) -> str:
        if self._state == "recording":
            return self._recording_icon
        if self._state == "error":
            return self._error_icon
        return self._idle_icon

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "blabble"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return "blabble"

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
        return MENU_PATH

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "u":
        return 0

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


class SniTray:
    def __init__(self, bus: MessageBus, idle_icon: str, recording_icon: str, error_icon: str):
        self._bus = bus
        self._sni = _StatusNotifierItem(idle_icon, recording_icon, error_icon)
        self._menu: _DBusMenu | None = None

    async def setup(
        self,
        menu_items: list[MenuItemSpec],
        on_activate: Callable[[], None],
    ) -> None:
        self._menu = _DBusMenu(menu_items)
        self._bus.export(MENU_PATH, self._menu)

        self._sni.on_activate = on_activate
        self._bus.export(SNI_PATH, self._sni)

        reply = await self._bus.call(
            Message(
                destination=WATCHER_BUS,
                path=WATCHER_PATH,
                interface=WATCHER_IFACE,
                member="RegisterStatusNotifierItem",
                signature="s",
                body=[self._bus.unique_name],
            )
        )
        if reply is None or reply.message_type.name == "ERROR":
            err = getattr(reply, "body", ["unknown"])[0] if reply else "no reply"
            log.warning("StatusNotifierWatcher registration failed: %s", err)
        else:
            log.info("registered tray icon with %s as %s", WATCHER_BUS, self._bus.unique_name)

    def set_state(self, state: TrayState) -> None:
        self._sni.set_state(state)

    def notify_menu_item_changed(self, item_id: int) -> None:
        if self._menu is not None:
            self._menu.notify_changed(item_id)
