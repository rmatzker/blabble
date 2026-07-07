"""KDE GlobalShortcuts portal client.

Registers shortcuts via the freedesktop portal so they're managed by KWin
and reconfigurable in System Settings → Shortcuts.
"""
import asyncio
import logging
import secrets
from typing import Any

from dbus_next import Message, MessageType, Variant
from dbus_next import introspection as intr
from dbus_next.aio import MessageBus

from ..base import AsyncCallback, ShortcutSpec

log = logging.getLogger(__name__)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"
APP_ID = "blabble"

# dbus-next's introspection parser rejects hyphenated property names
# that the portal exposes (e.g. `power-saver-enabled`). Hand-rolled XML
# scoped to just the interfaces we actually call avoids the parse error.
_PORTAL_INTROSPECTION_XML = """
<node>
  <interface name="org.freedesktop.portal.GlobalShortcuts">
    <method name="CreateSession">
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <method name="BindShortcuts">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a(sa{sv})" name="shortcuts" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <method name="ListShortcuts">
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <signal name="Activated">
      <arg type="o" name="session_handle"/>
      <arg type="s" name="shortcut_id"/>
      <arg type="t" name="timestamp"/>
      <arg type="a{sv}" name="options"/>
    </signal>
    <signal name="Deactivated">
      <arg type="o" name="session_handle"/>
      <arg type="s" name="shortcut_id"/>
      <arg type="t" name="timestamp"/>
      <arg type="a{sv}" name="options"/>
    </signal>
    <signal name="ShortcutsChanged">
      <arg type="o" name="session_handle"/>
      <arg type="a(sa{sv})" name="shortcuts"/>
    </signal>
  </interface>
</node>
"""


class PortalHotkey:
    def __init__(self, bus: MessageBus):
        self.bus = bus
        self.session_handle: str | None = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._activated_callbacks: dict[str, AsyncCallback] = {}
        self._gs_iface = None

    async def setup(self, shortcuts: list[ShortcutSpec]) -> None:
        await self._add_match(
            f"type='signal',interface='{REQUEST_IFACE}',member='Response'"
        )
        self.bus.add_message_handler(self._on_message)

        introspection = intr.Node.parse(_PORTAL_INTROSPECTION_XML)
        proxy = self.bus.get_proxy_object(PORTAL_BUS, PORTAL_PATH, introspection)
        self._gs_iface = proxy.get_interface(GS_IFACE)
        self._gs_iface.on_activated(self._on_activated)

        await self._register_app_id()
        self.session_handle = await self._create_session()
        log.info("portal session: %s", self.session_handle)

        existing = await self._list_shortcuts()
        existing_ids = {sid for sid, _ in existing}
        missing = [s for s in shortcuts if s.id not in existing_ids]

        if missing:
            await self._bind_shortcuts(missing)
            log.info("shortcuts bound (new): %s", [s.id for s in missing])
        else:
            log.info("shortcuts already bound from prior session: %s",
                     [s.id for s in shortcuts])

    def on_shortcut(self, shortcut_id: str, callback: AsyncCallback) -> None:
        self._activated_callbacks[shortcut_id] = callback

    async def _add_match(self, rule: str) -> None:
        await self.bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            )
        )

    def _on_message(self, message: Message) -> None:
        if message.message_type != MessageType.SIGNAL:
            return
        if message.interface != REQUEST_IFACE or message.member != "Response":
            return
        future = self._pending_requests.pop(message.path, None)
        if future is None or future.done():
            return
        response_code, results = message.body
        if response_code == 0:
            future.set_result(results)
        else:
            future.set_exception(
                RuntimeError(f"portal Response code={response_code} on {message.path}")
            )

    def _on_activated(
        self,
        session_handle: str,
        shortcut_id: str,
        timestamp: int,
        options: dict[str, Any],
    ) -> None:
        if session_handle != self.session_handle:
            return
        cb = self._activated_callbacks.get(shortcut_id)
        if cb is None:
            log.debug("activated %s but no handler", shortcut_id)
            return
        asyncio.create_task(cb())

    async def _await_response(self, request_path: str, timeout: float = 30.0) -> dict:
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_path] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_path, None)
            raise

    async def _register_app_id(self) -> None:
        # xdg-desktop-portal >= 1.22 makes GlobalShortcuts.CreateSession fail
        # with "An app id is required" for unsandboxed apps (no Flatpak/Snap
        # id to infer). Declaring one via the host Registry portal satisfies
        # the requirement. Missing on older portals — treat that as harmless.
        try:
            await self.bus.call(
                Message(
                    destination=PORTAL_BUS,
                    path=PORTAL_PATH,
                    interface=REGISTRY_IFACE,
                    member="Register",
                    signature="sa{sv}",
                    body=[APP_ID, {}],
                )
            )
        except Exception as e:
            log.warning("could not register app id via host portal: %s", e)

    async def _create_session(self) -> str:
        # session_handle_token MUST be stable across restarts on KDE — Plasma's
        # portal stores per-app bindings under [token_<session_handle_token>]
        # in kglobalshortcutsrc. A fresh random token every run = a fresh
        # consent prompt every run, plus an orphan group left behind.
        opts = {
            "handle_token": Variant("s", secrets.token_hex(8)),
            "session_handle_token": Variant("s", "blabble"),
        }
        request_path = await self._gs_iface.call_create_session(opts)
        result = await self._await_response(request_path)
        return result["session_handle"].value

    async def _list_shortcuts(self) -> list[tuple[str, dict]]:
        opts = {"handle_token": Variant("s", secrets.token_hex(8))}
        request_path = await self._gs_iface.call_list_shortcuts(self.session_handle, opts)
        result = await self._await_response(request_path)
        shortcuts_var = result.get("shortcuts")
        if shortcuts_var is None:
            return []
        return [(sid, props) for sid, props in shortcuts_var.value]

    async def _bind_shortcuts(self, shortcuts: list[ShortcutSpec]) -> None:
        binding = []
        for s in shortcuts:
            opts: dict[str, Variant] = {
                "description": Variant("s", s.description or s.id),
            }
            if s.preferred_trigger:
                opts["preferred_trigger"] = Variant("s", s.preferred_trigger)
            binding.append([s.id, opts])

        opts = {"handle_token": Variant("s", secrets.token_hex(8))}
        request_path = await self._gs_iface.call_bind_shortcuts(
            self.session_handle,
            binding,
            "",
            opts,
        )
        await self._await_response(request_path)
