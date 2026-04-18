from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclasses.dataclass
class CuspConfig:
    # Audio capture
    audio_device: str | int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    blocksize: int = 1024

    # AirPlay target(s). First entry is the group leader; remaining are followers.
    airplay_target: list[str] | None = None
    airplay_password: str | None = None

    # Behavior
    auto_reconnect: bool = True
    reconnect_delay: float = 5.0
    silence_threshold: float = 0.01
    idle_timeout: float = 30.0
    target_refresh_interval: float = 300.0
    log_level: str = "INFO"
    log_file: str | None = None


def _normalize_targets(value: object) -> list[str]:
    """Normalize a TOML `airplay.target` value (string or array) to a list."""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [str(v) for v in value]
    else:
        raise TypeError(
            "airplay.target must be a string or array of strings, "
            f"got {type(value).__name__}"
        )
    return [s.strip() for s in items if s.strip()]


def _find_config_file() -> Path | None:
    candidates = [
        Path("cusp.toml"),
        Path.home() / ".config" / "cusp" / "cusp.toml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_config(config_path: str | None = None, **cli_overrides: object) -> CuspConfig:
    """Load config from TOML file, then apply CLI overrides."""
    data: dict[str, object] = {}

    path = Path(config_path) if config_path else _find_config_file()
    if path and path.is_file():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        # Flatten nested TOML sections into flat config fields
        audio = raw.get("audio", {})
        airplay = raw.get("airplay", {})
        behavior = raw.get("behavior", {})

        if "device" in audio:
            dev = audio["device"]
            data["audio_device"] = (
                int(dev) if isinstance(dev, str) and dev.isdigit() else dev
            )
        if "sample_rate" in audio:
            data["sample_rate"] = audio["sample_rate"]
        if "channels" in audio:
            data["channels"] = audio["channels"]
        if "blocksize" in audio:
            data["blocksize"] = audio["blocksize"]
        if "target" in airplay:
            data["airplay_target"] = _normalize_targets(airplay["target"])
        if "password" in airplay:
            data["airplay_password"] = airplay["password"]
        for key in (
            "auto_reconnect",
            "reconnect_delay",
            "silence_threshold",
            "idle_timeout",
            "target_refresh_interval",
            "log_level",
            "log_file",
        ):
            if key in behavior:
                data[key] = behavior[key]

    # CLI overrides (None means "not provided")
    for key, value in cli_overrides.items():
        if value is not None:
            data[key] = value

    return CuspConfig(**data)
