"""Wake word detection.

Default: Vosk small English model in grammar mode, restricted to the wake
words + "stop"/"cancel" + "[unk]". If you train a custom openWakeWord model
for "Rika" (.onnx), set wake_word.engine = "openwakeword" and
wake_word.openwakeword_model = "<path>" in config.json.

Sensitivity (0..1): higher = triggers more easily (and more false alarms).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from rika import paths
from rika.audio import SAMPLE_RATE, MicHub

log = logging.getLogger(__name__)

VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
STOP_WORDS = ("stop", "cancel")
# Near-homophones that "absorb" similar sounds so they don't get forced onto "rika".
DECOYS = ("ricky", "rick", "erica", "rita", "rico", "rico's", "riker", "reaper", "regga", "eureka", "really")

# on_wake(command, confidence, audio):
#   command is "stop" for "Rika, stop/cancel", else None
#   utterance is set when the user kept talking after "Rika" in one breath
#   ("Rika, print document1.pdf"), so the command can go straight to Whisper.
MAX_UTTERANCE_FRAMES = 700  # ~21 s of 30 ms frames


@dataclass
class Utterance:
    audio: np.ndarray          # float32 16 kHz: everything Vosk heard so far, incl. "Rika"
    continuation: queue.Queue  # mic frames from the moment of detection (caller must unsubscribe)


WakeCallback = Callable[[str | None, float, "Utterance | None"], None]


def build_grammar(model, words: list[str]) -> str:
    decoys = [d for d in DECOYS if d not in words and model.vosk_model_find_word(d) >= 0]
    return json.dumps(list(words) + list(STOP_WORDS) + decoys + ["[unk]"])


class WakeWordDetector(threading.Thread):
    def __init__(self, hub: MicHub, on_wake: WakeCallback, sensitivity: float = 0.5):
        super().__init__(name="wakeword", daemon=True)
        self.hub = hub
        self.on_wake = on_wake
        self.sensitivity = sensitivity
        self.paused = threading.Event()
        self._halt = threading.Event()
        self._last_fire = 0.0

    def set_sensitivity(self, value: float) -> None:
        self.sensitivity = max(0.0, min(1.0, value))

    def stop(self) -> None:
        self._halt.set()

    def _fire(self, command: str | None, conf: float, utterance: Utterance | None = None) -> None:
        now = time.monotonic()
        if now - self._last_fire < 1.5:
            if utterance is not None:
                self.hub.unsubscribe(utterance.continuation)
            return
        self._last_fire = now
        log.info(
            "Wake word detected (command=%s, conf=%.2f, one-breath=%s)",
            command, conf, f"{len(utterance.audio) / SAMPLE_RATE:.1f}s" if utterance else "no",
        )
        try:
            self.on_wake(command, conf, utterance)
        except Exception:
            log.exception("on_wake handler failed")
            if utterance is not None:
                self.hub.unsubscribe(utterance.continuation)


class VoskWakeWord(WakeWordDetector):
    def __init__(self, hub: MicHub, on_wake: WakeCallback, sensitivity: float, words: list[str]):
        super().__init__(hub, on_wake, sensitivity)
        import vosk

        vosk.SetLogLevel(-1)
        model_dir = paths.models_dir() / VOSK_MODEL_NAME
        if not model_dir.exists():
            raise FileNotFoundError(f"Vosk model missing: {model_dir} (run tools\\download_models.py)")
        self.model = vosk.Model(str(model_dir))
        known = [w for w in words if self.model.vosk_model_find_word(w) >= 0]
        missing = sorted(set(words) - set(known))
        if missing:
            log.warning("Wake words not in Vosk vocabulary (ignored): %s", missing)
        self.words = known or ["rika"]
        self.grammar = build_grammar(self.model, self.words)
        log.info("Vosk wake words: %s (sensitivity %.2f)", self.words, sensitivity)

    @property
    def min_conf(self) -> float:
        return 0.95 - 0.7 * self.sensitivity  # 0.5 -> 0.60, 1.0 -> 0.25, 0.0 -> 0.95

    def _new_recognizer(self):
        import vosk

        rec = vosk.KaldiRecognizer(self.model, SAMPLE_RATE, self.grammar)
        rec.SetWords(True)
        return rec

    def check_result(self, result: dict) -> tuple[bool, str | None, float, bool]:
        """Inspect a final Vosk result -> (fire?, command, confidence, more speech after the wake word?)."""
        words = result.get("result", [])
        for i, w in enumerate(words):
            if w.get("word") in self.words and w.get("conf", 0.0) >= self.min_conf:
                tail = words[i + 1 :]
                command = "stop" if any(x.get("word") in STOP_WORDS for x in tail[:2]) else None
                has_tail = bool(tail) and tail[-1].get("end", 0.0) - w.get("end", 0.0) >= 0.4
                return True, command, float(w.get("conf", 0.0)), has_tail
        return False, None, 0.0, False

    def run(self) -> None:
        q = self.hub.subscribe(maxsize=200)
        rec = self._new_recognizer()
        utterance: list[np.ndarray] = []
        try:
            while not self._halt.is_set():
                try:
                    frame = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self.paused.is_set():
                    utterance.clear()
                    continue
                utterance.append(frame)
                if len(utterance) > MAX_UTTERANCE_FRAMES:
                    del utterance[: len(utterance) - MAX_UTTERANCE_FRAMES]
                if rec.AcceptWaveform(frame.tobytes()):
                    result = json.loads(rec.Result())
                    if result.get("text"):
                        log.debug("vosk: %s", result)
                    fire, command, conf, has_tail = self.check_result(result)
                    if fire:
                        utt = None
                        if has_tail and command is None:
                            utt = Utterance(
                                np.concatenate(utterance).astype(np.float32) / 32768.0,
                                self.hub.subscribe(tag="continuation"),
                            )
                        self._fire(command, conf, utt)
                    utterance = []
                elif self.sensitivity >= 0.8:
                    partial = json.loads(rec.PartialResult()).get("partial", "")
                    if any(w in partial.split() for w in self.words):
                        rec = self._new_recognizer()
                        utterance = []
                        self._fire(None, 0.0)
        finally:
            self.hub.unsubscribe(q)


class OpenWakeWord(WakeWordDetector):
    """Adapter for a custom openWakeWord model (pip install openwakeword)."""

    CHUNK = 1280  # 80 ms at 16 kHz

    def __init__(self, hub: MicHub, on_wake: WakeCallback, sensitivity: float, model_path: str):
        super().__init__(hub, on_wake, sensitivity)
        from openwakeword.model import Model

        self.model = Model(wakeword_models=[model_path], inference_framework="onnx")
        log.info("openWakeWord model loaded: %s", model_path)

    def run(self) -> None:
        q = self.hub.subscribe(maxsize=200)
        buf = np.zeros(0, dtype=np.int16)
        threshold = 1.0 - self.sensitivity
        try:
            while not self._halt.is_set():
                try:
                    frame = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self.paused.is_set():
                    buf = np.zeros(0, dtype=np.int16)
                    continue
                buf = np.concatenate([buf, frame])
                while len(buf) >= self.CHUNK:
                    chunk, buf = buf[: self.CHUNK], buf[self.CHUNK :]
                    scores = self.model.predict(chunk)
                    best = max(scores.values()) if scores else 0.0
                    if best >= threshold:
                        self.model.reset()
                        self._fire(None, float(best))
        finally:
            self.hub.unsubscribe(q)


def create_detector(cfg, hub: MicHub, on_wake: WakeCallback) -> WakeWordDetector:
    ww = cfg["wake_word"]
    sens = float(cfg["wake_word_sensitivity"])
    if ww.get("engine") == "openwakeword" and ww.get("openwakeword_model"):
        try:
            return OpenWakeWord(hub, on_wake, sens, ww["openwakeword_model"])
        except Exception as exc:
            log.error("openWakeWord unavailable (%s); falling back to Vosk", exc)
    return VoskWakeWord(hub, on_wake, sens, cfg.wake_words)
