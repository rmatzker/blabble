# dictate

Local-first voice dictation client. Press a hotkey, speak, get text typed at your cursor.

Points at a self-hosted whisper.cpp endpoint. First-class on Linux (KDE Plasma 6 / Wayland); Windows support added in v0.2 (macOS planned).

## What it does

1. Press `Meta+Space` (configurable in System Settings → Shortcuts).
2. A short upward blip plays. Recording starts. Tray icon turns red.
3. Press `Meta+Space` again. Downward blip. Recording stops.
4. Audio uploads to `localhost`. Whisper.cpp transcribes.
5. Optional regex transformations clean up the text (e.g. strip per-segment newlines).
6. Text is typed at the focused cursor via `wtype`.

No toast notifications. Ever. Audio cues + tray icon convey state.

## Requirements

- Plasma 6 / Wayland, KDE GlobalShortcuts portal (you have this)
- `wtype` (typing simulation): `sudo pacman -S wtype`
- `libpulse` (paplay for sound cues): `sudo pacman -S libpulse`
- Python 3.13+

## Install

```bash
./scripts/install.sh
```

That:
- Creates a Python venv at `~/.local/share/dictate/venv/` (local disk, not NFS).
- Copies `config.toml.example` → `~/.config/dictate/config.toml` if absent.
- Installs the systemd user unit and enables `dictate.service`.

On first start KDE shows a portal dialog: confirm `Meta+Space`. After that the binding lives in KWin's settings — re-bind whenever via *System Settings → Shortcuts → search "dictate."*

### Windows

Tested on Windows 11 with Python 3.13.

```powershell
git clone https://github.com/rmatzker/dictate.git
cd dictate
py -3.13 -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m dictate
```

Dependencies install automatically via platform markers (`pynput`, `pyperclip`, `pystray`, `pillow`, `sounddevice`).

Defaults on Windows:
- Hotkey: `Ctrl+Shift+Space` (configurable in `%APPDATA%\dictate\config.toml`)
- Config: `%APPDATA%\dictate\config.toml`
- Data: `%LOCALAPPDATA%\dictate\` (sounds, optional recordings)
- Tray: red dot when recording, gray otherwise

To autostart at login, drop a shortcut to `pythonw.exe -m dictate` (use `pythonw`, not `python`, to suppress the console window) into `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.

## Config

`~/.config/dictate/config.toml`. See `config.toml.example` for the full template.

Everything is overridable: server URL, sample rate, hotkey, audio cue paths, tray icons, transformations.

## Operations

```
systemctl --user status dictate          # is it running?
systemctl --user restart dictate         # after editing config
journalctl --user -u dictate -f          # tail logs
systemctl --user stop dictate            # quit (also: middle-click tray icon when implemented)
```

## File layout

```
~/Projects/dictate/                      # this repo (NFS-backed, fine for code)
~/.local/share/dictate/venv/             # local-disk Python env
~/.local/share/dictate/sounds/           # generated start.wav / stop.wav
~/.local/share/dictate/recordings/       # only if keep_local_copies=true
~/.config/dictate/config.toml            # your settings
~/.config/systemd/user/dictate.service   # service unit
```

## Why not Whispering?

It works fine, but it's a Tauri webview that fires 5 dbus toast notifications per recording with no off-switch, plus the bundled audio cues are silenced by a Tauri asset-protocol scope bug. This client is ~700 lines of Python pointing at the same whisper.cpp endpoint, with full ownership of every behavior.

## Uninstall

```bash
./scripts/uninstall.sh
```
