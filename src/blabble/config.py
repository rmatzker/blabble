import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _platform_paths() -> tuple[Path, Path]:
    """(config_dir, data_dir) per OS, following each platform's convention."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return appdata / "blabble", local / "blabble"
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "blabble"
        return base, base
    # Linux / other POSIX
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return xdg_config / "blabble", xdg_data / "blabble"


CONFIG_DIR, LOCAL_DATA = _platform_paths()
CONFIG_PATH = CONFIG_DIR / "config.toml"
SOUNDS_DIR = LOCAL_DATA / "sounds"
RECORDINGS_DIR = LOCAL_DATA / "recordings"


def _default_hotkey() -> str:
    if sys.platform == "win32":
        # Win+Space is taken by Windows IME switcher; Ctrl+Shift+Space is free.
        return "Ctrl+Shift+Space"
    if sys.platform == "darwin":
        # Cmd+Space is Spotlight; Ctrl+Shift+Space avoids the system grabs.
        return "Ctrl+Shift+Space"
    # Linux: Meta+Space is the existing default and is portal-managed by KDE.
    return "Meta+Space"


def _default_recording_device() -> str:
    # Linux uses PulseAudio's "pulse" device explicitly; Win/Mac use the
    # system default mic ("" is normalized to None for sounddevice).
    return "pulse" if sys.platform.startswith("linux") else ""


def _default_paste_method() -> str:
    # Only meaningful on Linux today (clipboard/ydotool/wtype). Win/Mac
    # backends ignore this field — they have one canonical paste path.
    return "clipboard"


@dataclass
class ServerConfig:
    url: str = "http://localhost:8080"
    connect_timeout: float = 3.0      # fail fast if backend is unreachable
    read_timeout: float = 60.0        # how long to wait for transcription
    retries: int = 1                  # extra attempts on transient errors (0 = no retry)


DEFAULT_HALLUCINATION_PATTERNS = [
    r"^\s*\*[^*]+\*\s*$",           # *punch*, *cough*, *music*
    r"^\s*\[[^\]]+\]\s*$",          # [BLANK_AUDIO], [Music], [silence]
    r"^\s*\(\s*[^)]*\s*\)\s*$",     # ( ), (silence)
    r"^\s*Thanks?\s+for\s+watching.*$",
    r"^\s*Thank\s+you\.?\s*$",
    r"^\s*you\.?\s*$",
    r"^\s*\.?\s*$",                 # bare period or empty
]


@dataclass
class RecordingConfig:
    device: str = field(default_factory=_default_recording_device)
    sample_rate: int = 16000
    keep_local_copies: bool = False
    max_local_copies: int = 50
    silence_rms_threshold: float = 200.0
    min_duration_ms: int = 250
    max_duration_sec: int = 300
    discard_if_matches: list[str] = field(default_factory=lambda: list(DEFAULT_HALLUCINATION_PATTERNS))


@dataclass
class HotkeyConfig:
    toggle: str = field(default_factory=_default_hotkey)


@dataclass
class SoundsConfig:
    enabled: bool = True
    start: str = "auto"
    stop: str = "auto"
    volume: float = 0.5


@dataclass
class PasteConfig:
    method: str = field(default_factory=_default_paste_method)
    press_enter_after: bool = False
    restore_clipboard: bool = True
    restore_delay_ms: int = 400


@dataclass
class TrayConfig:
    enabled: bool = True
    icon_idle: str = "audio-input-microphone"
    icon_recording: str = "media-record"
    icon_error: str = "dialog-warning"


@dataclass
class TransformationCfg:
    name: str
    find: str
    replace: str
    regex: bool = False


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    sounds: SoundsConfig = field(default_factory=SoundsConfig)
    paste: PasteConfig = field(default_factory=PasteConfig)
    tray: TrayConfig = field(default_factory=TrayConfig)
    transformations: list[TransformationCfg] = field(default_factory=list)


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        raw = tomllib.load(f)

    return Config(
        server=ServerConfig(**raw.get("server", {})),
        recording=RecordingConfig(**raw.get("recording", {})),
        hotkey=HotkeyConfig(**raw.get("hotkey", {})),
        sounds=SoundsConfig(**raw.get("sounds", {})),
        paste=PasteConfig(**raw.get("paste", {})),
        tray=TrayConfig(**raw.get("tray", {})),
        transformations=[TransformationCfg(**t) for t in raw.get("transformations", [])],
    )


def update_press_enter_after(value: bool, path: Path = CONFIG_PATH) -> None:
    """Rewrite press_enter_after in config.toml without touching comments or other keys."""
    if not path.exists():
        return
    text = path.read_text()
    new_value = "true" if value else "false"
    pattern = re.compile(r"^(\s*press_enter_after\s*=\s*)(?:true|false)(.*)$", re.MULTILINE)
    new_text, n = pattern.subn(rf"\g<1>{new_value}\g<2>", text)
    if n == 0:
        if "[paste]" in new_text:
            new_text = new_text.replace("[paste]", f"[paste]\npress_enter_after = {new_value}", 1)
        else:
            new_text = new_text.rstrip() + f"\n\n[paste]\npress_enter_after = {new_value}\n"
    path.write_text(new_text)
