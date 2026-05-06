"""DBusMenu (com.canonical.dbusmenu) implementation for tray right-click menus.

Just enough of the protocol for KDE Plasma's tray:
- Static flat menu (no submenus)
- Standard items, separators, and checkmark toggles
- Toggle state read live via a getter callback (so the menu reflects current config)
- Click events dispatched to async callbacks
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dbus_next import PropertyAccess, Variant
from dbus_next.service import ServiceInterface, dbus_property, method, signal

log = logging.getLogger(__name__)

MENU_PATH = "/MenuBar"
MENU_IFACE = "com.canonical.dbusmenu"


@dataclass
class MenuItem:
    id: int
    label: str = ""
    item_type: str = "standard"  # "standard" or "separator"
    toggle_type: str = ""        # "" or "checkmark"
    enabled: bool = True
    visible: bool = True
    on_click: Callable[[], Awaitable[None]] | None = None
    get_toggle_state: Callable[[], bool] | None = None

    def properties(self, requested: list[str] | None = None) -> dict[str, Variant]:
        props: dict[str, Variant] = {}
        if self.item_type == "separator":
            props["type"] = Variant("s", "separator")
        else:
            props["label"] = Variant("s", self.label)
        props["enabled"] = Variant("b", self.enabled)
        props["visible"] = Variant("b", self.visible)
        if self.toggle_type:
            props["toggle-type"] = Variant("s", self.toggle_type)
            on = bool(self.get_toggle_state and self.get_toggle_state())
            props["toggle-state"] = Variant("i", 1 if on else 0)
        if requested:
            return {k: v for k, v in props.items() if k in requested}
        return props


class DBusMenu(ServiceInterface):
    def __init__(self, items: list[MenuItem]):
        super().__init__(MENU_IFACE)
        self._items = {it.id: it for it in items}
        self._order = [it.id for it in items]
        self._revision = 0
        self._pending_updates: list = []

    # ---- properties ----
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

    # ---- methods ----
    @method()
    def GetLayout(self, parentId: "i", recursionDepth: "i", propertyNames: "as") -> "u(ia{sv}av)":
        wanted = list(propertyNames) if propertyNames else None
        if parentId != 0:
            return [self._revision, [parentId, {}, []]]
        children = []
        for item_id in self._order:
            it = self._items[item_id]
            children.append(Variant("(ia{sv}av)", [item_id, it.properties(wanted), []]))
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
                out.append([item_id, it.properties(wanted)])
        return out

    @method()
    def GetProperty(self, id: "i", name: "s") -> "v":
        it = self._items.get(id)
        if it is not None:
            props = it.properties([name])
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

    async def _dispatch(self, item: MenuItem) -> None:
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

    # ---- signals ----
    @signal()
    def ItemsPropertiesUpdated(self) -> "a(ia{sv})a(ias)":
        upd = self._pending_updates
        self._pending_updates = []
        return [upd, []]

    @signal()
    def LayoutUpdated(self) -> "ui":
        return [self._revision, 0]

    # ---- helpers ----
    def notify_toggle_changed(self, item_id: int) -> None:
        """Tell Plasma a property changed so the menu re-renders without reopening."""
        it = self._items.get(item_id)
        if it is None:
            return
        self._pending_updates = [[item_id, it.properties()]]
        self.ItemsPropertiesUpdated()
