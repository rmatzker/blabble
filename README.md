# blabble

Local-first voice dictation client. Press a hotkey, speak, get text typed at your cursor.

Points at a self-hosted [whisper.cpp](https://github.com/ggerganov/whisper.cpp) HTTP endpoint. First-class on Linux (KDE Plasma 6 / Wayland); Windows support added in v0.2 (macOS planned).

## How it works

```
   ┌──────────────────┐    audio (WAV)    ┌────────────────────┐
   │ blabble (client) │ ────────────────► │ whisper.cpp server │
   │  hotkey + mic    │ ◄──────────────── │   (your machine    │
   │  + text paste    │   transcribed     │    or a homelab)   │
   └──────────────────┘      text         └────────────────────┘
```

1. Press `Meta+Space` (Linux) / `Ctrl+Shift+Space` (Windows). Configurable.
2. A short upward blip plays. Recording starts. Tray icon turns red.
3. Press the hotkey again. Downward blip. Recording stops.
4. The WAV is uploaded to your whisper.cpp `/inference` endpoint.
5. Optional regex transformations clean up the text (e.g. strip per-segment newlines, glue split contractions).
6. Text is pasted at the focused cursor (via `wtype` / `wl-copy` on Linux, or the Windows clipboard).

No toast notifications. Ever. Audio cues + tray icon convey state.

---

## Part 1 — Set up the whisper.cpp server

The client is useless without a backend to talk to. Easiest path: build whisper.cpp on whatever machine has the most idle CPU/GPU (a desktop, a homelab box, even the same laptop), and run its bundled HTTP server.

### 1. Build whisper.cpp

```bash
git clone https://github.com/ggerganov/whisper.cpp.git
cd whisper.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j --config Release
```

For CUDA acceleration add `-DGGML_CUDA=1`; for Apple Silicon Metal is on by default; for Vulkan add `-DGGML_VULKAN=1`. CPU-only is fine for the smaller models.

### 2. Download a model

```bash
./models/download-ggml-model.sh large-v3-turbo
```

Recommendations:
- **`large-v3-turbo`** — best balance of speed and accuracy. ~1.5 GB. What this project is tuned for.
- **`medium.en`** — English-only, decent on modest CPUs.
- **`base.en`** — fastest, lower accuracy. Fine for short utterances.

Models land in `models/ggml-<name>.bin`.

### 3. Run the HTTP server

```bash
./build/bin/whisper-server \
    --host 0.0.0.0 \
    --port 8080 \
    --model models/ggml-large-v3-turbo.bin
```

That exposes an OpenAI-compatible endpoint at `POST http://<host>:8080/v1/audio/transcriptions` accepting `multipart/form-data` with a `file` field — which is exactly what the blabble client posts to.

### 4. (Optional) Put it behind HTTPS

If the server isn't on the same machine, terminate TLS with Caddy / nginx / Cloudflare Tunnel so you can point blabble at `https://whisper.example.com` instead of a bare IP. Otherwise just use `http://localhost:8080` from the client config.

### 5. (Optional) Run it as a service

A minimal systemd unit on the server side:

```ini
[Unit]
Description=whisper.cpp HTTP server
After=network.target

[Service]
ExecStart=/opt/whisper.cpp/build/bin/whisper-server --host 0.0.0.0 --port 8080 --model /opt/whisper.cpp/models/ggml-large-v3-turbo.bin
Restart=on-failure
User=whisper

[Install]
WantedBy=multi-user.target
```

---

## Part 2 — Install the client

### Linux (Plasma 6 / Wayland)

Requirements:
- Plasma 6 / Wayland, KDE GlobalShortcuts portal
- `wtype` (typing simulation): `sudo pacman -S wtype` (or your distro's equivalent)
- `libpulse` (paplay for sound cues): `sudo pacman -S libpulse`
- `wl-clipboard` + `ydotool` if you want the clipboard paste method (faster, default)
- Python 3.13+

```bash
git clone https://github.com/rmatzker/blabble.git
cd blabble
./scripts/install.sh
```

That:
- Creates a Python venv at `~/.local/share/blabble/venv/` (local disk, not NFS).
- Copies `config.toml.example` → `~/.config/blabble/config.toml` if absent.
- Installs the systemd user unit and enables `blabble.service`.

On first start KDE shows a portal dialog: confirm `Meta+Space`. After that the binding lives in KWin's settings — re-bind anytime via *System Settings → Shortcuts → search "blabble."*

### Windows

Tested on Windows 11 with Python 3.13.

```powershell
git clone https://github.com/rmatzker/blabble.git
cd blabble
py -3.13 -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python -m blabble
```

Dependencies install automatically via platform markers (`pynput`, `pyperclip`, `pystray`, `pillow`, `sounddevice`).

Defaults on Windows:
- Hotkey: `Ctrl+Shift+Space` (configurable in `%APPDATA%\blabble\config.toml`; set `toggle = "RightCtrl"` to trigger on a lone tap of Right Ctrl instead)
- Config: `%APPDATA%\blabble\config.toml`
- Data: `%LOCALAPPDATA%\blabble\` (sounds, optional recordings)
- Tray: red dot when recording, gray otherwise

To autostart at login, drop a shortcut to `pythonw.exe -m blabble` (use `pythonw`, not `python`, to suppress the console window) into `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.

---

## Part 3 — Configure `config.toml`

The file is copied from `config.toml.example` on first install. Location:

- Linux: `~/.config/blabble/config.toml`
- Windows: `%APPDATA%\blabble\config.toml`

After editing, restart the client: `systemctl --user restart blabble` (Linux) or relaunch the process (Windows).

The bits you'll actually want to touch:

### `[server]` — point at your whisper.cpp

```toml
[server]
url = "http://localhost:8080"   # or https://whisper.example.com
connect_timeout = 3.0
read_timeout = 60.0             # bump if you use a slower model
retries = 1                     # extra attempts on timeouts / HTTP 5xx
```

### `[recording]` — input handling and safety nets

```toml
[recording]
device = "pulse"                # Linux: PulseAudio / PipeWire device. Windows/macOS: "" = default mic (recommended); or a device-name substring, with fallback to the default mic if it doesn't match (Bluetooth names drift)
sample_rate = 16000             # whisper.cpp expects 16 kHz
keep_local_copies = false       # save WAVs to ~/.local/share/blabble/recordings/ if true
max_local_copies = 50           # ring-buffer size
silence_rms_threshold = 200.0   # skip transcription on near-silence (whisper hallucinates on silence)
min_duration_ms = 250           # skip accidental hotkey taps
max_duration_sec = 300          # auto-stop runaway recordings; 0 disables

# Drop transcriptions whose final text matches any of these regexes —
# these are common whisper.cpp training-data ghosts on near-silent input.
discard_if_matches = [
    '^\s*\*[^*]+\*\s*$',
    '^\s*\[[^\]]+\]\s*$',
    '^\s*Thanks?\s+for\s+watching.*$',
    '^\s*Thank\s+you\.?\s*$',
    '^\s*you\.?\s*$',
]
```

### `[hotkey]` — what triggers a recording

```toml
[hotkey]
toggle = "Meta+Space"           # Linux default; rebind in System Settings → Shortcuts
                                # Windows default: "Ctrl+Shift+Space"
```

On Windows, `toggle` can also be a single right-side modifier — `"RightCtrl"`, `"RightAlt"`, or `"RightShift"` — which fires on a lone *tap*: press and release within 0.5 s with no other key in between. Chords like Right Ctrl+C are unaffected, and the left-side key never triggers it.

### `[sounds]` — start/stop cues

```toml
[sounds]
enabled = true
start  = "auto"   # generated upward blip; or absolute path to a .wav
stop   = "auto"   # generated downward blip
volume = 0.5      # 0.0 silent → 1.0 file level
```

### `[paste]` — how transcribed text reaches your cursor (Linux only)

```toml
[paste]
method = "clipboard"            # "clipboard" — wl-copy + ydotool Ctrl+Shift+V (fastest)
                                # "ydotool"   — types char-by-char (drops chars at low key-delay)
                                # "wtype"     — wlroots compositors only
press_enter_after = false       # send Enter after paste (handy when dictating into chat/prompts)
restore_clipboard = true        # snapshot + restore the clipboard around the paste
restore_delay_ms = 400          # wait this long before restoring
```

### `[tray]` — icon names

```toml
[tray]
enabled = true
icon_idle      = "audio-input-microphone"   # freedesktop name on Linux; ignored on Windows
icon_recording = "media-record"
icon_error     = "dialog-warning"
```

### `[[transformations]]` — regex cleanups before paste

Whisper.cpp inserts a newline at every segment boundary, which splits contractions weirdly. These two rules fix the common cases:

```toml
[[transformations]]
name = "glue contractions"
find = "\\s*\\n+\\s*(?='[a-zA-Z])"   # "don\n't" → "don't"
replace = ""
regex = true

[[transformations]]
name = "strip newlines"
find = '\s*\n+\s*'                   # collapse remaining segment newlines into spaces
replace = " "
regex = true
```

Add your own to scrub filler words, normalize jargon, etc. They run in declaration order.

---

## Operations (Linux)

```
systemctl --user status blabble          # is it running?
systemctl --user restart blabble         # after editing config
journalctl --user -u blabble -f          # tail logs
systemctl --user stop blabble            # quit
```

## File layout

```
<repo>/                                  # this checkout
~/.local/share/blabble/venv/             # local-disk Python env
~/.local/share/blabble/sounds/           # generated start.wav / stop.wav
~/.local/share/blabble/recordings/       # only if keep_local_copies=true
~/.config/blabble/config.toml            # your settings
~/.config/systemd/user/blabble.service   # service unit
```

## Why not Whispering?

It works fine, but it's a Tauri webview that fires 5 dbus toast notifications per recording with no off-switch, plus the bundled audio cues are silenced by a Tauri asset-protocol scope bug. This client is ~700 lines of Python pointing at the same whisper.cpp endpoint, with full ownership of every behavior.

## Uninstall

```bash
./scripts/uninstall.sh
```
