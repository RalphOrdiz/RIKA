"""Load and validate config.json (created with defaults on first run).

Bad values never crash Rika: each one is replaced with its default and
reported in ``Config.problems`` (logged and mentioned at startup).
Only unreadable JSON raises :class:`ConfigError`.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rika import paths
from rika.errors import ConfigError

log = logging.getLogger(__name__)

CONFIG_VERSION = 2

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "accounts": {
        "rjoprintingservice": {
            "name": "RJO Printing Service",
            "email": "rjoprintingservice@gmail.com",
            "chrome_profile_dir": "Profile 2",
            "aliases": ["rjo", "rjo printing", "rjo printing service", "printing service", "shop"],
        }
    },
    "default_print_account": "rjoprintingservice",
    "printer": "EPSON L120 Series",
    "wake_word_sensitivity": 0.5,
    "wake_word": {
        "engine": "vosk",
        # Keep this to ONE word: homophones (rika/rica) split Vosk's confidence in half.
        "words": ["rika"],
        "openwakeword_model": "",
    },
    "hotkey": "ctrl+alt+r",
    "greet_on_wake": True,
    # After answering, keep listening this many seconds for a follow-up (no need to say "Rika" again).
    # Then she says "Just call my name, I'll be sleeping for a while!". 0 = one command per wake.
    "follow_up_seconds": 8,
    "tts": {
        "engine": "kokoro",
        "voices": [["af_bella", 0.7], ["af_nicole", 0.3]],
        "pitch_semitones": 2.0,
        "speed": 1.1,
        "preserve_formants": True,
        "fallback": ["edge", "sapi"],
        "kokoro_model": "kokoro-v1.0.onnx",
        "edge": {"voice": "en-US-AnaNeural", "pitch": "+15Hz", "rate": "+5%", "volume": "+0%"},
        "sapi": {"voice": "Zira", "rate": "+5%"},
        "piper": {"exe": "", "model": ""},
        # ElevenLabs (online). Put the API key in %APPDATA%\Rika\.env as ELEVENLABS_API_KEY=...
        # (ELEVENLABS_VOICE_ID in .env, if set, overrides voice_id here). Use engine "elevenlabs" to turn it on.
        "elevenlabs": {"voice_id": "", "api_key": "", "model_id": "eleven_multilingual_v2"},
    },
    "stt": {"model": "base.en", "beam_size": 5},
    "search_days": 14,
    "max_search_messages": 100,
    "fuzzy_threshold": 72,
    "silence_seconds": 1.5,
    "max_record_seconds": 15,
    "speech_start_timeout": 6,
    "mic_device": None,
    "download_retention_days": 7,
    "sumatra_path": "",
    "chrome_path": "",
    "maximize_gmail": True,  # open Gmail full screen (maximized)
}

ALLOWED_EXTENSIONS = (".pdf", ".doc", ".docx")
TTS_ENGINES = ("kokoro", "elevenlabs", "edge", "sapi", "piper")


@dataclass
class Account:
    key: str
    email: str
    chrome_profile_dir: str = ""
    aliases: list[str] = field(default_factory=list)
    name: str = ""

    @property
    def spoken_name(self) -> str:
        return self.name or self.key


class Config:
    def __init__(self, data: dict[str, Any], path: Path | None = None, problems: list[str] | None = None):
        self.data = data
        self.path = path
        self.problems = problems or []

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def accounts(self) -> dict[str, Account]:
        result = {}
        for key, val in self.data["accounts"].items():
            result[key] = Account(
                key=key,
                email=val.get("email", ""),
                chrome_profile_dir=val.get("chrome_profile_dir", ""),
                aliases=list(val.get("aliases", [])),
                name=str(val.get("name", "")),
            )
        return result

    @property
    def print_account(self) -> Account | None:
        return self.accounts.get(self.data["default_print_account"])

    @property
    def printer(self) -> str:
        return self.data["printer"]

    @property
    def tts(self) -> dict[str, Any]:
        return self.data["tts"]

    @property
    def stt(self) -> dict[str, Any]:
        return self.data["stt"]

    @property
    def wake_words(self) -> list[str]:
        return [w.lower() for w in self.data["wake_word"]["words"]]


def _deep_merge(defaults: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(defaults)
    for key, val in user.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict) and key != "accounts":
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


_NUMBER = (int, float)


def _check(data: dict[str, Any], problems: list[str]) -> None:
    """Replace invalid values with defaults, recording a problem for each."""

    def reset(key: str, why: str, section: str | None = None) -> None:
        if section:
            problems.append(f"{section}.{key}: {why} (using default)")
            data[section][key] = copy.deepcopy(DEFAULT_CONFIG[section][key])
        else:
            problems.append(f"{key}: {why} (using default)")
            data[key] = copy.deepcopy(DEFAULT_CONFIG[key])

    accounts = data.get("accounts")
    if not isinstance(accounts, dict) or not accounts:
        reset("accounts", "must be a non-empty object")
        accounts = data["accounts"]
    for key, acct in list(accounts.items()):
        if not isinstance(acct, dict) or not isinstance(acct.get("email"), str) or "@" not in acct.get("email", ""):
            problems.append(f"accounts.{key}: needs an \"email\" like name@gmail.com (account ignored)")
            del accounts[key]
            continue
        if not isinstance(acct.get("chrome_profile_dir", ""), str):
            problems.append(f"accounts.{key}.chrome_profile_dir must be text")
            acct["chrome_profile_dir"] = ""
        if not isinstance(acct.get("aliases", []), list):
            problems.append(f"accounts.{key}.aliases must be a list")
            acct["aliases"] = []
    if not accounts:
        reset("accounts", "no valid accounts left")

    if data.get("default_print_account") not in data["accounts"]:
        first = next(iter(data["accounts"]))
        problems.append(
            f"default_print_account '{data.get('default_print_account')}' is not in accounts (using '{first}')"
        )
        data["default_print_account"] = first

    if not isinstance(data.get("printer"), str):
        reset("printer", "must be text")

    sens = data.get("wake_word_sensitivity")
    if not isinstance(sens, _NUMBER) or not 0.0 <= sens <= 1.0:
        reset("wake_word_sensitivity", "must be a number between 0 and 1")

    ww = data["wake_word"]
    if not isinstance(ww, dict):
        reset("wake_word", "must be an object")
    else:
        if ww.get("engine") not in ("vosk", "openwakeword"):
            reset("engine", "must be 'vosk' or 'openwakeword'", "wake_word")
        if not isinstance(ww.get("words"), list) or not all(isinstance(w, str) for w in ww["words"]) or not ww["words"]:
            reset("words", "must be a list of words", "wake_word")

    if not isinstance(data.get("hotkey"), str) or not re.fullmatch(r"(?:(?:ctrl|alt|shift|win)\+)+[a-z0-9]", data["hotkey"].lower()):
        reset("hotkey", "must look like ctrl+alt+r")

    tts = data["tts"]
    if not isinstance(tts, dict):
        reset("tts", "must be an object")
    else:
        if tts.get("engine") not in TTS_ENGINES:
            reset("engine", f"must be one of {', '.join(TTS_ENGINES)}", "tts")
        voices = tts.get("voices")
        if (
            not isinstance(voices, list) or not voices
            or not all(
                isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)
                and isinstance(v[1], _NUMBER) and not isinstance(v[1], bool) and v[1] > 0
                for v in voices
            )
        ):
            reset("voices", 'must look like [["af_bella", 0.7], ["af_nicole", 0.3]]', "tts")
        for key, lo, hi in (("pitch_semitones", 0.0, 4.0), ("speed", 0.9, 1.3)):
            val = tts.get(key)
            if not isinstance(val, _NUMBER) or isinstance(val, bool) or not lo <= val <= hi:
                reset(key, f"must be a number from {lo} to {hi}", "tts")
        fb = tts.get("fallback")
        if not isinstance(fb, list) or not all(f in TTS_ENGINES for f in fb):
            reset("fallback", f"must be a list from {', '.join(TTS_ENGINES)}", "tts")
        edge = tts.get("edge")
        if not isinstance(edge, dict):
            reset("edge", "must be an object", "tts")
        else:
            for key, pattern, example in (("pitch", r"[+-]\d+Hz", "+15Hz"), ("rate", r"[+-]\d+%", "+5%"),
                                          ("volume", r"[+-]\d+%", "+0%")):
                if not re.fullmatch(pattern, str(edge.get(key, ""))):
                    problems.append(f"tts.edge.{key}: must look like {example} (using default)")
                    edge[key] = DEFAULT_CONFIG["tts"]["edge"][key]

    for key, lo, hi in (
        ("search_days", 1, 365),
        ("max_search_messages", 10, 500),
        ("fuzzy_threshold", 30, 100),
        ("silence_seconds", 0.3, 5),
        ("max_record_seconds", 3, 60),
        ("speech_start_timeout", 2, 30),
        ("follow_up_seconds", 0, 30),
        ("download_retention_days", 1, 365),
    ):
        val = data.get(key)
        if not isinstance(val, _NUMBER) or isinstance(val, bool) or not lo <= val <= hi:
            reset(key, f"must be a number from {lo} to {hi}")

    if data.get("mic_device") is not None and not isinstance(data["mic_device"], (int, str)):
        reset("mic_device", "must be null, a device number or a device name")


def load_env_files() -> list[Path]:
    """Read KEY=VALUE lines from .env files into os.environ (real environment variables win).

    Looked up in %APPDATA%\\Rika\\.env and next to Rika.exe / the project folder. Values are never logged.
    """
    import os

    loaded = []
    for env_path in (paths.data_dir() / ".env", paths.app_dir() / ".env"):
        try:
            text = env_path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
        loaded.append(env_path)
    if loaded:
        log.info("Loaded settings from %s", ", ".join(str(p) for p in loaded))
    return loaded


def load_config(path: Path | None = None) -> Config:
    load_env_files()
    path = path or paths.config_path()
    if not path.exists():
        log.info("No config found; writing default config to %s", path)
        save_config(DEFAULT_CONFIG, path)
    try:
        user = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON ({path}): line {exc.lineno} col {exc.colno}: {exc.msg}")
    if not isinstance(user, dict):
        raise ConfigError(f"config.json must contain a JSON object ({path})")
    if _migrate(user):
        backup = path.with_name("config.v1.backup.json")
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        save_config(user, path)
        log.info("Upgraded config.json to version %d (old copy: %s)", CONFIG_VERSION, backup)

    data = _deep_merge(DEFAULT_CONFIG, user)
    problems: list[str] = []
    _check(data, problems)
    for p in problems:
        log.warning("Config problem: %s", p)
    return Config(data, path, problems)


def save_config(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or paths.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


_V1_TTS_KEYS = ("voice", "pitch", "rate", "volume", "sapi_voice", "piper_exe", "piper_model")


def _migrate(user: dict[str, Any]) -> bool:
    """Upgrade an older config.json in place. Returns True if anything changed."""
    if user.get("config_version", 1) >= CONFIG_VERSION:
        return False
    tts = user.get("tts")
    if isinstance(tts, dict) and any(k in tts for k in _V1_TTS_KEYS):
        new = copy.deepcopy(DEFAULT_CONFIG["tts"])  # Kokoro becomes the default voice
        for key in ("voice", "pitch", "rate", "volume"):
            if key in tts:
                new["edge"][key] = tts[key]
        if tts.get("sapi_voice"):
            new["sapi"]["voice"] = tts["sapi_voice"]
        new["piper"] = {"exe": tts.get("piper_exe", ""), "model": tts.get("piper_model", "")}
        user["tts"] = new
    if isinstance(user.get("stt"), dict):
        user["stt"].pop("language", None)  # Rika is English-only now
    user["config_version"] = CONFIG_VERSION
    return True


def save_voice_choice(voices: list, pitch_semitones: float, speed: float, path: Path | None = None) -> None:
    """Write a voice-lab choice into config.json, keeping everything else the user wrote."""
    path = path or paths.config_path()
    user = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else copy.deepcopy(DEFAULT_CONFIG)
    _migrate(user)
    tts = user.setdefault("tts", {})
    tts["engine"] = "kokoro"
    tts["voices"] = [[str(n), round(float(w), 3)] for n, w in voices]
    tts["pitch_semitones"] = round(min(4.0, max(0.0, float(pitch_semitones))), 2)
    tts["speed"] = round(min(1.3, max(0.9, float(speed))), 2)
    save_config(user, path)
    log.info("Saved voice choice: %s pitch %+.1f speed %.2f", tts["voices"], tts["pitch_semitones"], tts["speed"])
