"""Load non-secret project defaults without overriding shell configuration."""

from pathlib import Path
import os

from archive_manager.paths import PROJECT_ROOT

SETTINGS_ENV_PATH = Path(os.environ.get("ARCHIVE_SETTINGS_FILE", PROJECT_ROOT / "settings.env"))


def load_settings(path: Path = SETTINGS_ENV_PATH) -> None:
    """Load simple KEY=value settings, preserving existing environment values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


load_settings()
