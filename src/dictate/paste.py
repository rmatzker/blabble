import asyncio
import logging
import shutil

log = logging.getLogger(__name__)


async def paste_at_cursor(
    text: str, *, method: str = "clipboard", press_enter_after: bool = False
) -> None:
    """Deliver text to the focused cursor.

    method:
      - "clipboard": wl-copy the text + ydotool simulates Ctrl+Shift+V.
        Fastest and lossless regardless of length. Replaces clipboard contents.
      - "ydotool":   ydotool types char-by-char via uinput. At delays <12ms
        on KWin, release events get coalesced and characters (often spaces)
        drop on long strings.
      - "wtype":     wtype on wlroots compositors only — KWin lacks
        zwp_virtual_keyboard_v1.
    """
    if not text:
        return

    if method == "clipboard":
        await _paste_clipboard(text)
    elif method == "ydotool":
        await _type_ydotool(text)
    elif method == "wtype":
        await _type_wtype(text)
    else:
        log.error("unknown paste method: %s", method)
        return

    if press_enter_after:
        await _press_enter()


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


async def _paste_clipboard(text: str) -> None:
    if not shutil.which("wl-copy"):
        log.error("wl-copy not installed (need wl-clipboard package)")
        return
    if not shutil.which("ydotool"):
        log.error("ydotool not installed")
        return

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
        return
    if copy.returncode != 0:
        log.error("wl-copy failed (rc=%d)", copy.returncode)
        return

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
        return
    if proc.returncode != 0:
        log.error("ydotool key failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace"))


async def _type_ydotool(text: str) -> None:
    if not shutil.which("ydotool"):
        log.error("ydotool not installed")
        return
    proc = await asyncio.create_subprocess_exec(
        "ydotool", "type", "--key-delay", "8", "--", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("ydotool failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace"))


async def _type_wtype(text: str) -> None:
    if not shutil.which("wtype"):
        log.error("wtype not installed")
        return
    proc = await asyncio.create_subprocess_exec(
        "wtype", "--", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error("wtype failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace"))
