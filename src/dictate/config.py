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


@dataclass
class RecordingConfig:
    device: str = "pulse"
    sample_rate: int = 16000
    keep_local_copies: bool = False
    max_local_copies: int = 50
    silence_rms_threshold: float = 200.0
    min_duration_ms: int = 250


@dataclass
class HotkeyConfig:
    toggle: str = "Meta+Space"


@dataclass
class SoundsConfig:
    enabled: bool = True
    start: str = "auto"
    stop: str = "auto"


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
