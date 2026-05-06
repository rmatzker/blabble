import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "dictate" / "config.toml"
LOCAL_DATA = Path.home() / ".local" / "share" / "dictate"
SOUNDS_DIR = LOCAL_DATA / "sounds"
RECORDINGS_DIR = LOCAL_DATA / "recordings"


@dataclass
class ServerConfig:
    url: str = "http://localhost:8080"


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
    device: str = "pulse"
    sample_rate: int = 16000
    keep_local_copies: bool = False
    max_local_copies: int = 50
    silence_rms_threshold: float = 200.0
    min_duration_ms: int = 250
    max_duration_sec: int = 300
    discard_if_matches: list[str] = field(default_factory=lambda: list(DEFAULT_HALLUCINATION_PATTERNS))


@dataclass
class HotkeyConfig:
    toggle: str = "Meta+Space"


@dataclass
class SoundsConfig:
    enabled: bool = True
    start: str = "auto"
    stop: str = "auto"
    volume: float = 0.5


@dataclass
class PasteConfig:
    method: str = "clipboard"
    press_enter_after: bool = False


@dataclass
class TrayConfig:
    enabled: bool = True
    icon_idle: str = "audio-input-microphone"
    icon_recording: str = "media-record"


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
