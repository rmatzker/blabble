"""Windows tray icon via pystray.

pystray runs the icon's message pump on a worker thread (run_detached);
menu callbacks fire on that thread, so we marshal them back onto the
asyncio loop with call_soon_threadsafe.
"""
import asyncio
import logging
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from ..base import MenuItemSpec, TrayState

log = logging.getLogger(__name__)

_ICON_SIZE = 64


def _make_icon(color: tuple[int, int, int]) -> Image.Image:
    """Solid circle on transparent background. Crisp at 16/24/32px tray sizes."""
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, _ICON_SIZE - 4, _ICON_SIZE - 4), fill=(*color, 255))
    return img


_IDLE_IMAGE = _make_icon((140, 140, 140))      # gray
_RECORDING_IMAGE = _make_icon((220, 50, 50))   # red
_ERROR_IMAGE = _make_icon((230, 160, 30))      # amber


class PystrayTray:
    def __init__(self):
        self._loop = asyncio.get_event_loop()
        self._icon: pystray.Icon | None = None
        self._state: TrayState = "idle"
        self._items: list[MenuItemSpec] = []
        self._on_activate: Callable[[], None] | None = None

    async def setup(
        self,
        menu_items: list[MenuItemSpec],
        on_activate: Callable[[], None],
    ) -> None:
        self._items = menu_items
        self._on_activate = on_activate

        menu = pystray.Menu(
            pystray.MenuItem(
                "dictate (toggle)",
                self._wrap_activate(),
                default=True,  # left-click = this item
            ),
            pystray.Menu.SEPARATOR,
            *[self._build_pystray_item(it) for it in menu_items],
        )

        self._icon = pystray.Icon(
            "dictate",
            icon=_IDLE_IMAGE,
            title="dictate · idle",
            menu=menu,
        )
        self._icon.run_detached()
        log.info("pystray icon started")

    def _build_pystray_item(self, spec: MenuItemSpec):
        if spec.item_type == "separator":
            return pystray.Menu.SEPARATOR
        kwargs = {}
        if spec.toggle and spec.get_toggle_state is not None:
            getter = spec.get_toggle_state
            kwargs["checked"] = lambda _item, _getter=getter: _getter()
        return pystray.MenuItem(
            spec.label,
            self._wrap_callback(spec),
            **kwargs,
        )

    def _wrap_callback(self, spec: MenuItemSpec):
        def _fire(_icon=None, _item=None):
            if spec.on_click is None:
                return
            self._loop.call_soon_threadsafe(asyncio.create_task, spec.on_click())
        return _fire

    def _wrap_activate(self):
        def _fire(_icon=None, _item=None):
            if self._on_activate is not None:
                self._loop.call_soon_threadsafe(self._on_activate)
        return _fire

    def set_state(self, state: TrayState) -> None:
        self._state = state
        if self._icon is None:
            return
        if state == "recording":
            self._icon.icon = _RECORDING_IMAGE
            self._icon.title = "dictate · recording"
        elif state == "error":
            self._icon.icon = _ERROR_IMAGE
            self._icon.title = "dictate · transcription failed"
        else:
            self._icon.icon = _IDLE_IMAGE
            self._icon.title = "dictate · idle"

    def notify_menu_item_changed(self, item_id: int) -> None:
        # pystray re-evaluates `checked` lambdas when the menu reopens
        # and on update_menu(); poke it so the toggle reflects state
        # changes triggered programmatically.
        if self._icon is not None:
            self._icon.update_menu()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
