"""Local speech-to-text with faster-whisper (CPU, int8)."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from rika import paths

log = logging.getLogger(__name__)

# Whisper sometimes "hears" these in noise; treat as nothing when the clip is short.
# ("Thank you" is NOT filtered: people really say it to Rika, and audio only reaches
# Whisper after the recorder detected speech.)
HALLUCINATIONS = {"thanks for watching!", "thanks for watching.", "you", "."}


def build_prompt(accounts: dict) -> str:
    names = []
    for a in accounts.values():
        names += [a.key, a.name, *a.aliases[:3]]
    names = [n for n in dict.fromkeys(names) if n]
    return (
        "Rika, print document1.pdf. Rika, print report.docx. Please print resume.pdf. "
        f"Open {names[0] if names else 'RJO'} Gmail. Gmail, PDF, DOCX, DOC, print, cancel, stop. "
        f"Accounts: {', '.join(names)}."
    )


class Transcriber:
    def __init__(self, stt_cfg: dict, prompt: str):
        self.model_name = stt_cfg.get("model", "base.en")
        self.beam_size = int(stt_cfg.get("beam_size", 5))
        self.prompt = prompt
        self._model = None
        self._ready = threading.Event()
        self._error: Exception | None = None

    def _model_source(self) -> str:
        local = paths.models_dir() / f"faster-whisper-{self.model_name}"
        if (local / "model.bin").exists():
            return str(local)
        log.warning("Whisper model not bundled at %s; downloading '%s' on first use", local, self.model_name)
        return self.model_name

    def load(self) -> None:
        try:
            from faster_whisper import WhisperModel

            t0 = time.monotonic()
            self._model = WhisperModel(
                self._model_source(),
                device="cpu",
                compute_type="int8",
                download_root=str(paths.data_dir() / "models"),
            )
            log.info("Whisper '%s' loaded in %.1fs", self.model_name, time.monotonic() - t0)
        except Exception as exc:
            log.exception("Could not load Whisper model")
            self._error = exc
        finally:
            self._ready.set()

    def load_async(self) -> None:
        threading.Thread(target=self.load, name="whisper-load", daemon=True).start()

    def transcribe(self, audio: np.ndarray, hint: str = "") -> str:
        if not self._ready.wait(timeout=120):
            raise RuntimeError("Whisper model is still loading")
        if self._model is None:
            raise RuntimeError(f"Whisper model failed to load: {self._error}")
        language = "en"  # Rika is English-only
        prompt = f"{self.prompt} {hint}".strip()
        t0 = time.monotonic()
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self.beam_size,
            initial_prompt=prompt,
            condition_on_previous_text=False,
            without_timestamps=True,
            temperature=0.0,
            vad_filter=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("Whisper (%.1fs for %.1fs audio): %r", time.monotonic() - t0, len(audio) / 16000, text)
        if text.lower() in HALLUCINATIONS and len(audio) < 16000 * 2.5:
            return ""
        return text
