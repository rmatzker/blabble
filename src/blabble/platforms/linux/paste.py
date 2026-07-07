"""Linux paste via wl-copy + ydotool (or ydotool/wtype direct typing)."""
import asyncio
import logging
import shutil

log = logging.getLogger(__name__)


class WaylandPaste:
    def __init__(
        self,
        method: str = "clipboard",
        restore_clipboard: bool = True,
        restore_delay_ms: int = 400,
    ):
        # method:
        #   "clipboard" — wl-copy + ydotool Ctrl+Shift+V (fastest, lossless)
        #   "ydotool"   — type char-by-char via uinput (drops chars at low key-delay on KWin)
        #   "wtype"     — wlroots compositors only (KWin lacks zwp_virtual_keyboard_v1)
        self.method = method
        self.restore_clipboard = restore_clipboard
        self.restore_delay_ms = restore_delay_ms

    async def paste_text(self, text: str, *, press_enter_after: bool = False) -> None:
        if not text:
            return

        # Only the "clipboard" method actually touches the system clipboard;
        # ydotool/wtype just type the text, so no snapshot needed for those.
        snapshot = None
        if self.method == "clipboard" and self.restore_clipboard:
            snapshot = await _snapshot_clipboard()

        ok = False
        if self.method == "clipboard":
            ok = await _paste_clipboard(text)
        elif self.method == "ydotool":
            ok = await _type_ydotool(text)
        elif self.method == "wtype":
            ok = await _type_wtype(text)
        else:
            log.error("unknown paste method: %s", self.method)
            return

        if ok and press_enter_after:
            await _press_enter()

        if snapshot is not None and ok:
            # Wait for the target app to consume the paste before we overwrite
            # the clipboard with the saved contents. Too short = app pastes the
            # restored (old) content; too long = user notices their copy is gone.
            await asyncio.sleep(self.restore_delay_ms / 1000.0)
            await _restore_clipboard(snapshot)


async def _snapshot_clipboard() -> list[tuple[str, bytes]] | None:
    """Capture current clipboard as [(mime_type, bytes), ...].

    Returns None on hard failure (tools missing). Returns [] when the clipboard
    is empty — distinct from None so we still know to "restore" by clearing.
    """
    if not shutil.which("wl-paste"):
        return None

    list_proc = await asyncio.create_subprocess_exec(
        "wl-paste", "--list-types",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(list_proc.communicate(), timeout=1.0)
    except asyncio.TimeoutError:
        list_proc.kill()
        await list_proc.wait()
        return None
    if list_proc.returncode != 0:
        # Empty clipboard exits non-zero on some wl-clipboard versions.
        return []

    types = [t.strip() for t in out.decode(errors="replace").splitlines() if t.strip()]
    if not types:
        return []

    # Pick a single representative type rather than every offered MIME — wl-copy
    # only publishes one type per invocation, and trying to multiplex requires
    # holding wl-copy alive as a server. Text is what users actually copy 99%
    # of the time; if it's an image, we capture and restore the image bytes.
    preferred = _pick_mime(types)
    if preferred is None:
        return []

    paste_proc = await asyncio.create_subprocess_exec(
        "wl-paste", "-t", preferred, "-n",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        data, _ = await asyncio.wait_for(paste_proc.communicate(), timeout=2.0)
    except asyncio.TimeoutError:
        paste_proc.kill()
        await paste_proc.wait()
        return None
    if paste_proc.returncode != 0:
        return []
    return [(preferred, data)]


def _pick_mime(types: list[str]) -> str | None:
    for pref in ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING"):
        if pref in types:
            return pref
    # Fallback: first non-internal type.
    for t in types:
        if not t.startswith("x-kde-") and t != "TIMESTAMP" and t != "TARGETS":
            return t
    return None


async def _restore_clipboard(snapshot: list[tuple[str, bytes]]) -> None:
    if not shutil.which("wl-copy"):
        return
    if not snapshot:
        # Original clipboard was empty — clear ours so we don't leak the dictation.
        clear = await asyncio.create_subprocess_exec(
            "wl-copy", "--clear",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(clear.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            clear.kill()
            await clear.wait()
        return

    mime, data = snapshot[0]
    proc = await asyncio.create_subprocess_exec(
        "wl-copy", "-t", mime,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if proc.stdin is not None:
        proc.stdin.write(data)
        await proc.stdin.drain()
        proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        log.error("wl-copy restore didn't exit within 2s; killing")
        proc.kill()
        await proc.wait()


async def _press_enter() -> None:
    if not shutil.which("ydotool"):
        return
    # KEY_ENTER = 28
    proc = await asyncio.create_subprocess_exec(
        "ydotool", "key", "--key-delay", "20", "28:1", "28:0",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except asyncio.TimeoutError:
        log.error("ydotool enter timed out; killing")
        proc.kill()
        await proc.wait()
        return
    if proc.returncode != 0:
        log.error("ydotool enter failed (rc=%d): %s",
                  proc.returncode, stderr.decode(errors="replace"))


async def _paste_clipboard(text: str) -> bool:
    if not shutil.which("wl-copy"):
        log.error("wl-copy not installed (need wl-clipboard package)")
        return False
    if not shutil.which("ydotool"):
        log.error("ydotool not installed")
        return False

    # wl-copy daemonizes (double-fork) so its stdout/stderr fds stay open
    # in the daemon child. If we pipe them, asyncio.communicate() waits for
    # EOF that never comes and hangs forever. Inherit/devnull all three.
    copy = await asyncio.create_subprocess_exec(
        "wl-copy",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if copy.stdin is not None:
        copy.stdin.write(text.encode("utf-8"))
        await copy.stdin.drain()
        copy.stdin.close()
    try:
        await asyncio.wait_for(copy.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        log.error("wl-copy didn't exit within 2s; killing")
        copy.kill()
        await copy.wait()
        return False
    if copy.returncode != 0:
        log.error("wl-copy failed (rc=%d)", copy.returncode)
        return False

    # Wait for the clipboard manager to actually pick up the new content
    # before firing the paste chord; without this delay Konsole pastes the
    # *previous* clipboard contents (or nothing).
    await asyncio.sleep(0.15)

    # Linux input event codes: KEY_LEFTCTRL=29, KEY_LEFTSHIFT=42, KEY_V=47
    # 50ms between events — KWin's input pipeline on Plasma 6.6 needs noticeable
    # spacing between modifier press and target key for synthesized chords to
    # register in apps like Konsole.
    proc = await asyncio.create_subprocess_exec(
        "ydotool", "key", "--key-delay", "50",
        "29:1", "42:1", "47:1", "47:0", "42:0", "29:0",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except asyncio.TimeoutError:
        log.error("ydotool key timed out; killing")
        proc.kill()
        await proc.wait()
        return False
    if proc.returncode != 0:
        log.error("ydotool key failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace"))
        return False
    return True


async def _type_ydotool(text: str) -> bool:
    if not shutil.which("ydotool"):
        log.error("ydotool not installed")
        return False
    proc = await asyncio.create_subprocess_exec(
        "ydotool", "type", "--key-delay", "8", "--", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("ydotool failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace"))
        return False
    return True


async def _type_wtype(text: str) -> bool:
    if not shutil.which("wtype"):
        log.error("wtype not installed")
        return False
    proc = await asyncio.create_subprocess_exec(
        "wtype", "--", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("wtype failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace"))
        return False
    return True
