"""Filesystem locations.

User data (config, tokens, logs, downloads, TTS cache) lives in %APPDATA%\\Rika.
Bundled read-only resources (assets, models, vendor tools) live next to the
code when running from source, or inside the PyInstaller bundle when frozen.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Rika"


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Directory holding Rika.exe (frozen) or the project root (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Directory that holds bundled data files (PyInstaller's _internal dir when frozen)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def assets_dir() -> Path:
    return bundle_dir() / "rika" / "assets"


def models_dir() -> Path:
    return bundle_dir() / "models"


def vendor_dir() -> Path:
    return bundle_dir() / "vendor"


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir() -> Path:
    path = data_dir() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tts_cache_dir() -> Path:
    path = data_dir() / "tts_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "rika.log"


def print_log_path() -> Path:
    return data_dir() / "print_log.csv"


def token_path() -> Path:
    return data_dir() / "token.json"


def credentials_path() -> Path:
    """credentials.json from %APPDATA%\\Rika, falling back to the app folder."""
    primary = data_dir() / "credentials.json"
    if primary.exists():
        return primary
    secondary = app_dir() / "credentials.json"
    return secondary if secondary.exists() else primary
