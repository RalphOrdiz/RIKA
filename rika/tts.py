"""Text-to-speech with swappable engines and an ordered fallback chain.

    kokoro - Kokoro-82M via kokoro-onnx (offline, CPU). Default. Built-in voices
             can be blended (weighted average of style vectors). Optional
             Rubber Band pitch shift (pedalboard) for a brighter voice.
    edge   - Microsoft Edge neural voices (online)
    sapi   - Windows SAPI via pyttsx3 (offline, always available)
    piper  - local Piper model (offline)

Fallback order comes from config: tts.engine first, then tts.fallback
(default ["edge", "sapi"]).

Adding an engine (for example ElevenLabs, using a voice you are licensed to
use - no cloning): subclass TTSEngine, implement ``synthesize(text, out_path)``
(write a .wav or .mp3), ``settings_key()`` (anything that changes the sound)
and optionally ``prepare()``; read its settings from the matching config
section (e.g. tts.elevenlabs.voice_id / api_key) and register the class in
ENGINES. Cache, playback, stop and fallback are shared.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np

from rika import paths
from rika.audio import play_file

log = logging.getLogger(__name__)

StatusCallback = Callable[[str | None], None]  # "Downloading voice 40%" ... None when done


class EngineNotReady(Exception):
    """The engine is still downloading/loading; use the next engine for now."""


def speakable(text: str) -> str:
    """Strip characters that only make sense on screen ("Printing now~!" -> "Printing now!")."""
    text = text.replace("~", "")
    return re.sub(r"\s+", " ", text).strip()


def split_chunks(text: str, min_len: int = 14) -> list[str]:
    """Split a live line at sentence ends and commas so playback can start sooner."""
    parts = re.split(r"(?<=[.!?,])\s+", text)
    chunks: list[str] = []
    for p in parts:
        if chunks and (len(chunks[-1]) < min_len or len(p) < 6):
            chunks[-1] = f"{chunks[-1]} {p}"
        else:
            chunks.append(p)
    return [c for c in chunks if c.strip()]


def write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ============================================================================ engines

class TTSEngine:
    name = "base"
    ext = ".wav"

    def __init__(self, cfg: dict):
        self.cfg = cfg  # the whole "tts" config section

    def settings_key(self) -> str:
        return ""

    def is_ready(self) -> bool:
        return True

    def prepare(self, status: StatusCallback | None = None) -> None:
        """Download/load anything slow. Called once in the background."""

    def synthesize(self, text: str, out_path: Path) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------- Kokoro

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_VOICES_FILE = "voices-v1.0.bin"
KOKORO_RATE = 24000

_kokoro_cache: dict[tuple[str, str], object] = {}
_kokoro_lock = threading.Lock()


def kokoro_dir() -> Path:
    path = paths.data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path, label: str, status: StatusCallback | None) -> None:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("Downloading %s -> %s", url, dest)
    req = urllib.request.Request(url, headers={"User-Agent": "Rika/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last = -1
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
            done += len(block)
            pct = int(done * 100 / total) if total else 0
            if status and pct != last:
                last = pct
                status(f"Downloading {label} {pct}%")
    if total and done < total:
        raise OSError(f"Download of {url} was incomplete ({done}/{total} bytes)")
    os.replace(tmp, dest)


def load_kokoro(model_name: str, status: StatusCallback | None = None):
    """Download (first run) and load Kokoro once per process; shared by Rika and the voice lab."""
    model = kokoro_dir() / model_name
    voices = kokoro_dir() / KOKORO_VOICES_FILE
    key = (str(model), str(voices))
    with _kokoro_lock:
        if key in _kokoro_cache:
            return _kokoro_cache[key]
        _download(f"{KOKORO_RELEASE}/{model_name}", model, "voice model", status)
        _download(f"{KOKORO_RELEASE}/{KOKORO_VOICES_FILE}", voices, "voices", status)
        if status:
            status("Loading voice")
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        opts = ort.SessionOptions()
        # leave some cores free so the PC (and Whisper) stay responsive while lines are pre-generated
        opts.intra_op_num_threads = max(2, (os.cpu_count() or 4) // 2)
        session = ort.InferenceSession(str(model), sess_options=opts, providers=["CPUExecutionProvider"])
        t0 = time.monotonic()
        kokoro = Kokoro.from_session(session, str(voices))
        kokoro.create("Hi.", voice=kokoro.get_voices()[0])  # warm-up
        log.info("Kokoro %s loaded in %.1fs", model_name, time.monotonic() - t0)
        _kokoro_cache[key] = kokoro
        return kokoro


def female_english_voices(kokoro) -> list[str]:
    return sorted(v for v in kokoro.get_voices() if v.startswith(("af_", "bf_")))


def parse_blend(spec: str) -> list[list]:
    """'af_bella:0.6,af_nicole:0.4' -> [['af_bella', 0.6], ['af_nicole', 0.4]] (weight defaults to 1)."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, weight = part.partition(":")
        out.append([name.strip(), float(weight) if weight.strip() else 1.0])
    if not out:
        raise ValueError("empty blend")
    return out


def normalize_blend(voices: list) -> list[list]:
    total = sum(float(w) for _, w in voices)
    if total <= 0:
        raise ValueError("blend weights must add up to more than 0")
    return [[str(n), round(float(w) / total, 3)] for n, w in voices]


def blend_label(voices: list) -> str:
    voices = normalize_blend(voices)
    if len(voices) == 1:
        return voices[0][0]
    return " + ".join(f"{n} {round(w * 100)}%" for n, w in voices)


def blend_style(kokoro, voices: list) -> np.ndarray:
    known = set(kokoro.get_voices())
    missing = [n for n, _ in voices if n not in known]
    if missing:
        raise ValueError(f"Unknown Kokoro voice(s): {missing}. Female English voices: {female_english_voices(kokoro)}")
    voices = normalize_blend(voices)
    style = sum(float(w) * kokoro.get_voice_style(n) for n, w in voices)
    return np.asarray(style, dtype=np.float32)


def blend_lang(voices: list) -> str:
    """American (af_/am_) -> en-us, British (bf_/bm_) -> en-gb, decided by the heaviest voice."""
    top = max(voices, key=lambda v: float(v[1]))[0]
    return "en-gb" if top.startswith(("bf_", "bm_")) else "en-us"


def pitch_shift(audio: np.ndarray, rate: int, semitones: float, preserve_formants: bool = True) -> np.ndarray:
    """High-quality pitch shift (Rubber Band via pedalboard), keeping the length the same."""
    if abs(semitones) < 0.01:
        return audio
    import pedalboard

    out = pedalboard.time_stretch(
        audio.astype(np.float32)[None, :], float(rate), 1.0, float(semitones),
        high_quality=True, preserve_formants=preserve_formants,
    )
    return out[0]


class KokoroEngine(TTSEngine):
    name = "kokoro"
    ext = ".wav"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._kokoro = None
        self._style = None
        self.preparing = threading.Event()

    @property
    def voices(self) -> list:
        return self.cfg.get("voices") or [["af_bella", 1.0]]

    def settings_key(self) -> str:
        c = self.cfg
        return (
            f"{c.get('kokoro_model')}|{normalize_blend(self.voices)}|{c.get('pitch_semitones')}|"
            f"{c.get('speed')}|{c.get('preserve_formants')}"
        )

    def is_ready(self) -> bool:
        return self._kokoro is not None

    def prepare(self, status: StatusCallback | None = None) -> None:
        self.preparing.set()
        try:
            kokoro = load_kokoro(self.cfg.get("kokoro_model", "kokoro-v1.0.onnx"), status)
            self._style = blend_style(kokoro, self.voices)
            self._kokoro = kokoro
            log.info("Kokoro voice: %s, pitch %+.1f, speed %.2f", blend_label(self.voices),
                     float(self.cfg.get("pitch_semitones", 0)), float(self.cfg.get("speed", 1.0)))
        finally:
            self.preparing.clear()

    def render(self, text: str, voices: list | None = None, speed: float | None = None,
               pitch: float | None = None, preserve_formants: bool | None = None) -> np.ndarray:
        """Return float32 audio at 24 kHz. Defaults come from config; the voice lab overrides them."""
        if self._kokoro is None:
            if self.preparing.is_set() or not _kokoro_files_present(self):
                raise EngineNotReady("Kokoro is still loading")
            self.prepare()  # used as a fallback voice: load on first use (~1 s)
        voices = voices or self.voices
        style = self._style if voices is self.voices else blend_style(self._kokoro, voices)
        speed = float(self.cfg.get("speed", 1.0) if speed is None else speed)
        audio, rate = self._kokoro.create(speakable(text), voice=style, speed=speed, lang=blend_lang(voices))
        pitch = float(self.cfg.get("pitch_semitones", 0.0) if pitch is None else pitch)
        formants = bool(self.cfg.get("preserve_formants", True) if preserve_formants is None else preserve_formants)
        return pitch_shift(np.asarray(audio, dtype=np.float32), rate, pitch, formants)

    def synthesize(self, text: str, out_path: Path) -> None:
        write_wav(out_path, self.render(text), KOKORO_RATE)


# ---------------------------------------------------------------------------- Edge

class EdgeEngine(TTSEngine):
    name = "edge"
    ext = ".mp3"

    @property
    def opts(self) -> dict:
        return self.cfg.get("edge", {})

    def settings_key(self) -> str:
        o = self.opts
        return f"{o.get('voice')}|{o.get('pitch')}|{o.get('rate')}|{o.get('volume')}"

    def synthesize(self, text: str, out_path: Path) -> None:
        import edge_tts

        o = self.opts

        async def run() -> None:
            comm = edge_tts.Communicate(
                speakable(text),
                o.get("voice", "en-US-AnaNeural"),
                rate=o.get("rate", "+0%"),
                pitch=o.get("pitch", "+0Hz"),
                volume=o.get("volume", "+0%"),
            )
            await asyncio.wait_for(comm.save(str(out_path)), timeout=15)

        asyncio.run(run())
        if not out_path.exists() or out_path.stat().st_size < 500:
            raise RuntimeError("edge-tts returned no audio")


# ---------------------------------------------------------------------------- SAPI

class SapiEngine(TTSEngine):
    """pyttsx3/SAPI must stay on one COM thread, so all calls go through a single worker."""

    name = "sapi"
    ext = ".wav"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sapi", initializer=self._init_com)
        self._engine = None

    @staticmethod
    def _init_com() -> None:
        import pythoncom

        pythoncom.CoInitialize()

    @property
    def opts(self) -> dict:
        return self.cfg.get("sapi", {})

    def settings_key(self) -> str:
        return f"{self.opts.get('voice')}|{self.opts.get('rate')}"

    def _get_engine(self):
        if self._engine is None:
            import pyttsx3

            eng = pyttsx3.init("sapi5")
            wanted = (self.opts.get("voice") or "").lower()
            voices = eng.getProperty("voices")
            chosen = next((v for v in voices if wanted and wanted in v.name.lower()), None)
            chosen = chosen or next((v for v in voices if "zira" in v.name.lower()), None)
            if chosen:
                eng.setProperty("voice", chosen.id)
            rate_pct = int(str(self.opts.get("rate", "+0%")).rstrip("%") or 0)
            eng.setProperty("rate", int(185 * (1 + rate_pct / 100)))
            self._engine = eng
            log.info("SAPI voice: %s", chosen.name if chosen else "(default)")
        return self._engine

    def synthesize(self, text: str, out_path: Path) -> None:
        def work() -> None:
            eng = self._get_engine()
            eng.save_to_file(speakable(text), str(out_path))
            eng.runAndWait()

        self._pool.submit(work).result(timeout=30)
        if not out_path.exists() or out_path.stat().st_size < 100:
            raise RuntimeError("SAPI produced no audio")


# ---------------------------------------------------------------------------- Piper

class PiperEngine(TTSEngine):
    """Offline neural TTS: https://github.com/rhasspy/piper (piper.exe + a .onnx voice)."""

    name = "piper"
    ext = ".wav"

    @property
    def opts(self) -> dict:
        return self.cfg.get("piper", {})

    def settings_key(self) -> str:
        return f"{self.opts.get('model')}"

    def synthesize(self, text: str, out_path: Path) -> None:
        exe, model = self.opts.get("exe"), self.opts.get("model")
        if not exe or not model or not Path(exe).exists() or not Path(model).exists():
            raise RuntimeError(f"Piper not configured (exe={exe!r}, model={model!r})")
        subprocess.run(
            [exe, "--model", model, "--output_file", str(out_path)],
            input=speakable(text).encode("utf-8"), check=True, timeout=30,
            capture_output=True, creationflags=0x08000000,
        )


# ---------------------------------------------------------------------------- ElevenLabs

class ElevenLabsEngine(TTSEngine):
    """ElevenLabs text-to-speech (online) with a voice from your ElevenLabs account.

    Voice ID: env ELEVENLABS_VOICE_ID, else config tts.elevenlabs.voice_id.
    API key:  env ELEVENLABS_API_KEY (e.g. from %APPDATA%\\Rika\\.env), else tts.elevenlabs.api_key.
    The key is only ever sent in the request header - never logged or put in error messages.
    """

    name = "elevenlabs"
    ext = ".mp3"
    API = "https://api.elevenlabs.io/v1"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._rejected: str | None = None  # set when ElevenLabs refuses the key/voice for good

    @property
    def opts(self) -> dict:
        return self.cfg.get("elevenlabs", {})

    @property
    def voice_id(self) -> str:
        return (os.environ.get("ELEVENLABS_VOICE_ID") or self.opts.get("voice_id") or "").strip()

    @property
    def model_id(self) -> str:
        return (self.opts.get("model_id") or "eleven_multilingual_v2").strip()

    def _key(self) -> str:
        return (os.environ.get("ELEVENLABS_API_KEY") or self.opts.get("api_key") or "").strip()

    def _voice_settings(self) -> dict:
        keys = ("stability", "similarity_boost", "style", "speed", "use_speaker_boost")
        return {k: self.opts[k] for k in keys if self.opts.get(k) is not None}

    def settings_key(self) -> str:
        return f"{self.voice_id}|{self.model_id}|{sorted(self._voice_settings().items())}"

    def is_ready(self) -> bool:
        return bool(self._key() and self.voice_id) and self._rejected is None

    def _request(self, method: str, path: str, body: dict | None = None, accept: str = "application/json") -> bytes:
        import json
        import urllib.error

        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self.API}{path}", data=data, method=method)
        req.add_header("xi-api-key", self._key())
        req.add_header("Accept", accept)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("detail", "")
                detail = detail.get("message", detail) if isinstance(detail, dict) else detail
            except ValueError:
                detail = ""
            if exc.code in (401, 403):
                self._rejected = f"API key rejected ({exc.code}) {detail}".strip()
            elif exc.code == 404:
                self._rejected = f"voice {self.voice_id} not found in this ElevenLabs account"
            raise RuntimeError(f"ElevenLabs HTTP {exc.code}: {detail or exc.reason}") from None

    def prepare(self, status: StatusCallback | None = None) -> None:
        if not self._key():
            raise RuntimeError("No ElevenLabs API key - add ELEVENLABS_API_KEY=... to %APPDATA%\\Rika\\.env")
        if not self.voice_id:
            raise RuntimeError("No ElevenLabs voice id - set tts.elevenlabs.voice_id in config.json")
        try:
            import json

            info = json.loads(self._request("GET", f"/voices/{self.voice_id}"))
            log.info("ElevenLabs voice: %s (%s), model %s", info.get("name", "?"), self.voice_id, self.model_id)
        except RuntimeError:
            if self._rejected:
                raise RuntimeError(f"ElevenLabs: {self._rejected}") from None
            log.warning("Could not check the ElevenLabs voice now; will try when speaking")
        except OSError as exc:  # offline - not fatal, fallbacks cover it
            log.warning("ElevenLabs not reachable right now (%s); using fallback voices meanwhile", exc)

    def synthesize(self, text: str, out_path: Path) -> None:
        if self._rejected:
            raise RuntimeError(f"ElevenLabs: {self._rejected}")
        body = {"text": speakable(text), "model_id": self.model_id}
        settings = self._voice_settings()
        if settings:
            body["voice_settings"] = settings
        audio = self._request(
            "POST", f"/text-to-speech/{self.voice_id}?output_format=mp3_44100_128", body, accept="audio/mpeg"
        )
        if len(audio) < 500:
            raise RuntimeError("ElevenLabs returned no audio")
        out_path.write_bytes(audio)


ENGINES: dict[str, type[TTSEngine]] = {
    "kokoro": KokoroEngine,
    "edge": EdgeEngine,
    "sapi": SapiEngine,
    "piper": PiperEngine,
    "elevenlabs": ElevenLabsEngine,
}


# ============================================================================ speaker

class Speaker:
    """Speaks with the first working engine in the chain; cached, streamed and interruptible."""

    def __init__(self, tts_cfg: dict, on_status: StatusCallback | None = None):
        self.cfg = tts_cfg
        self.on_status = on_status or (lambda s: None)
        order = [tts_cfg.get("engine", "kokoro"), *tts_cfg.get("fallback", ["edge", "sapi"])]
        if "sapi" not in order:
            order.append("sapi")  # always have an offline last resort
        self.chain: list[TTSEngine] = [ENGINES[n](tts_cfg) for n in dict.fromkeys(order) if n in ENGINES]
        self.primary = self.chain[0]
        self._down_until: dict[str, float] = {}
        self._interrupt = threading.Event()
        self._say_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._prepared = threading.Event()
        self._closed = False
        self.speaking = threading.Event()
        self.current_text = ""
        self.name_said_at = 0.0  # when she last finished saying her own name (wake-word echo guard)

    # ------------------------------------------------------------ preparation
    def prepare_async(self, lines: list[str]) -> None:
        """Load the primary engine (downloading on first run) and pre-render ``lines``."""
        threading.Thread(target=self._prepare, args=(lines,), name="tts-prepare", daemon=True).start()

    def load_primary(self, status: StatusCallback | None = None) -> bool:
        """Load the main engine now (blocking). False if it failed and fallbacks will be used."""
        self.last_error = ""
        try:
            self.primary.prepare(status)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            log.error("TTS engine '%s' could not start (%s)", self.primary.name, exc)
            return False
        finally:
            self._prepared.set()

    def _prepare(self, lines: list[str]) -> None:
        # Start the main voice; if it can't (e.g. no ElevenLabs key yet), pre-render with the first fallback that can.
        engine = None
        for e in self.chain:
            if e.name == "sapi" and e is not self.primary:
                break  # the Windows voice is instant; nothing worth pre-rendering
            try:
                e.prepare(self.on_status)
                engine = e
                break
            except Exception as exc:
                log.error("TTS engine '%s' could not start (%s)", e.name, exc)
        self._prepared.set()
        if engine is None:
            self.on_status(None)
            return
        if engine is not self.primary:
            log.warning("Using fallback voice '%s' until '%s' works", engine.name, self.primary.name)
        todo = [t for t in dict.fromkeys(speakable(l) for l in lines) if not self._cache_path(engine, t).exists()]
        if todo:
            log.info("Pre-rendering %d lines (%d characters) with %s", len(todo), sum(map(len, todo)), engine.name)
        for i, text in enumerate(todo, 1):
            if self._closed:
                return
            self.on_status(f"Preparing voice lines {i}/{len(todo)}")
            try:
                with self._render_lock:
                    self._render(engine, text)
            except Exception as exc:
                log.warning("Could not pre-render %r: %s", text, exc)
                break
        if todo:
            log.info("Pre-rendered %d voice lines with %s", len(todo), engine.name)
        self.on_status(None)

    # ------------------------------------------------------------ cache
    def _cache_path(self, engine: TTSEngine, text: str) -> Path:
        digest = hashlib.sha1(f"{engine.name}|{engine.settings_key()}|{text}".encode("utf-8")).hexdigest()[:20]
        return paths.tts_cache_dir() / f"{engine.name}_{digest}{engine.ext}"

    def _render(self, engine: TTSEngine, text: str) -> Path:
        path = self._cache_path(engine, text)
        if path.exists() and path.stat().st_size > 0:
            return path
        tmp = path.with_name(f"{path.stem}.{threading.get_ident()}.tmp{engine.ext}")
        try:
            engine.synthesize(text, tmp)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def _usable(self) -> list[TTSEngine]:
        now = time.monotonic()
        out = []
        for e in self.chain:
            if self._down_until.get(e.name, 0) > now:
                continue
            if e is self.primary and not e.is_ready():
                # loading takes a second or two - worth waiting for; a first-run download is not
                if isinstance(e, KokoroEngine) and e.preparing.is_set() and _kokoro_files_present(e):
                    self._wait_ready(e, 8.0)
                if not e.is_ready():
                    continue
            out.append(e)
        return out

    @staticmethod
    def _wait_ready(engine: TTSEngine, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end and not engine.is_ready():
            time.sleep(0.05)
        return engine.is_ready()

    def audio_for(self, text: str) -> Path:
        """Cached or freshly rendered audio for ``text`` from the first engine that works."""
        text = speakable(text)
        if self.is_cached(text):  # pre-rendered in the main voice: plays even while the engine loads
            return self._cache_path(self.primary, text)
        # In order of preference: each engine's own cached clip, else render with it, else the next engine.
        # (Never reuse a fallback voice's cached clip while the main voice can still speak.)
        last: Exception | None = None
        for e in self._usable():
            try:
                with self._render_lock:
                    return self._render(e, text)
            except EngineNotReady as exc:
                last = exc
            except Exception as exc:
                last = exc
                log.warning("TTS engine '%s' failed (%s); trying the next one for 60s", e.name, exc)
                self._down_until[e.name] = time.monotonic() + 60
        raise RuntimeError(f"No TTS engine could speak: {last}")

    def is_cached(self, text: str) -> bool:
        p = self._cache_path(self.primary, speakable(text))
        return p.exists() and p.stat().st_size > 0

    # ------------------------------------------------------------ speaking
    def say(self, text: str, cancel_event: threading.Event | None = None) -> None:
        with self._say_lock:
            self._interrupt.clear()

            def stop() -> bool:
                return self._interrupt.is_set() or (cancel_event is not None and cancel_event.is_set())

            text = speakable(text)
            self.current_text = text
            self.speaking.set()
            try:
                if self.is_cached(text) or len(text) < 40:
                    paths_iter = iter([self._safe_audio(text)])
                else:
                    paths_iter = self._stream(split_chunks(text), stop)
                for p in paths_iter:
                    if p is None or stop():
                        break
                    try:
                        play_file(p, should_stop=stop)
                    except OSError as exc:
                        log.error("Audio playback failed: %s", exc)
                        break
            finally:
                if "rika" in text.lower():
                    self.name_said_at = time.monotonic()
                self.speaking.clear()

    def said_own_name_recently(self, within_s: float = 2.5) -> bool:
        """True while (or shortly after) Rika says "Rika" - so her own voice can't wake her."""
        if self.speaking.is_set() and "rika" in self.current_text.lower():
            return True
        return time.monotonic() - self.name_said_at < within_s

    def _safe_audio(self, text: str) -> Path | None:
        try:
            return self.audio_for(text)
        except Exception:
            log.exception("Could not synthesize speech for %r", text)
            return None

    def _stream(self, chunks: list[str], stop: Callable[[], bool]):
        """Render chunk N+1 while chunk N plays."""
        q: queue.Queue = queue.Queue()

        def produce() -> None:
            for c in chunks:
                if stop():
                    break
                p = self._safe_audio(c)
                q.put(p)
                if p is None:
                    return
            q.put(None)

        threading.Thread(target=produce, name="tts-stream", daemon=True).start()
        while True:
            p = q.get()
            if p is None:
                return
            yield p

    def stop(self) -> None:
        self._interrupt.set()

    def close(self) -> None:
        self._closed = True
        self.stop()


def _kokoro_files_present(engine: KokoroEngine) -> bool:
    return (kokoro_dir() / engine.cfg.get("kokoro_model", "kokoro-v1.0.onnx")).exists() and (
        kokoro_dir() / KOKORO_VOICES_FILE
    ).exists()


def prune_cache(max_age_days: int = 60) -> None:
    cutoff = time.time() - max_age_days * 86400
    for f in paths.tts_cache_dir().glob("*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
