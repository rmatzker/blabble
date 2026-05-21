"""Cross-platform protocols for the OS-coupled parts of dictate.

Each backend (linux/windows/macos) implements these so service.py can
orchestrate the recording pipeline without importing any OS-specific code.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

AsyncCallback = Callable[[], Awaitable[None]]
TrayState = Literal["idle", "recording", "error"]


@dataclass
class ShortcutSpec:
    id: str
    description: str
    preferred_trigger: str


@dataclass
class MenuItemSpec:
    id: int
    label: str = ""
    item_type: Literal["standard", "separator"] = "standard"
    toggle: bool = False
    get_toggle_state: Callable[[], bool] | None = None
    on_click: AsyncCallback | None = None


@runtime_checkable
class Sounds(Protocol):
    def play(self, path: Path, volume: float) -> None: ...


@runtime_checkable
class Paste(Protocol):
    async def paste_text(
        self, text: str, *, press_enter_after: bool = False
    ) -> None: ...


@runtime_checkable
class Hotkey(Protocol):
    async def setup(self, shortcuts: list[ShortcutSpec]) -> None: ...
    def on_shortcut(self, shortcut_id: str, callback: AsyncCallback) -> None: ...


@runtime_checkable
class Tray(Protocol):
    async def setup(
        self,
        menu_items: list[MenuItemSpec],
        on_activate: Callable[[], None],
    ) -> None: ...
    def set_state(self, state: TrayState) -> None: ...
    def notify_menu_item_changed(self, item_id: int) -> None: ...


@dataclass
class Backend:
    sounds: Sounds
    paste: Paste
    hotkey: Hotkey
    tray: Tray | None
    # Platform-specific cleanup hook (e.g., closing a DBus connection).
    # Returns when shutdown is complete.
    shutdown: Callable[[], Awaitable[None]] | None = None
