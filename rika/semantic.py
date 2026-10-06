"""Meaning-based intent matching with a small local sentence-embedding model.

all-MiniLM-L6-v2 (ONNX, ~23 MB, Apache-2.0) turns a sentence into a vector; sentences
that mean the same thing get similar vectors, even with different words. Rika compares
what you said to the example phrasings in intent_examples.py and picks the closest
meaning. Runs offline on the CPU in a few milliseconds.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from rika import paths
from rika.intent_examples import EXAMPLES

log = logging.getLogger(__name__)

MODEL_DIRNAME = "minilm-l6-v2"
HF_REPO = "Xenova/all-MiniLM-L6-v2"
MODEL_FILE = "model_quantized.onnx"

# Minimum similarity to trust a match. Actions that change something need more certainty.
THRESHOLDS = {"default": 0.55, "close_gmail": 0.62, "print_file": 0.60, "open_gmail": 0.58}
MARGIN = 0.04        # best intent must beat the runner-up (a different intent) by this much...
CLEAR_MARGIN = 0.12  # ...or be this far ahead, in which case a slightly lower score is fine
CLEAR_SLACK = 0.07

# Sentence models blur opposites ("open the email" vs "close the email"); an explicit verb decides.
_CLOSE_WORDS = ("close", "shut", "exit", "quit", "hide", "get rid of")
_OPEN_WORDS = ("open", "show", "bring up", "pull up", "launch", "go to", "take me to", "see the", "start")
_MAIL_WORDS = ("gmail", "email", "e-mail", "mail", "inbox", "chrome", "browser", "rjo", "printing service")


def _has(text: str, words: tuple[str, ...]) -> bool:
    import re

    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)


def model_dir() -> Path | None:
    for base in (paths.models_dir(), paths.data_dir() / "models"):
        d = base / MODEL_DIRNAME
        if (d / MODEL_FILE).exists() and (d / "tokenizer.json").exists():
            return d
    return None


def download_model() -> Path:
    """Fetch the model into %APPDATA%\\Rika\\models (used when it isn't bundled)."""
    import shutil

    from huggingface_hub import hf_hub_download

    d = paths.data_dir() / "models" / MODEL_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    for remote, local in ((f"onnx/{MODEL_FILE}", MODEL_FILE), ("tokenizer.json", "tokenizer.json")):
        shutil.copy(hf_hub_download(HF_REPO, remote), d / local)
    return d


class SemanticMatcher:
    def __init__(self, examples: dict[str, list[str]] | None = None):
        self.examples = examples or EXAMPLES
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self.error: Exception | None = None
        self._session = None
        self._tok = None
        self._labels: list[str] = []
        self._matrix: np.ndarray | None = None

    # ------------------------------------------------------------ loading
    def load(self, allow_download: bool = True) -> bool:
        try:
            d = model_dir() or (download_model() if allow_download else None)
            if d is None:
                raise FileNotFoundError("sentence model not found (run tools\\download_models.py)")
            import onnxruntime as ort
            from tokenizers import Tokenizer

            t0 = time.monotonic()
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            self._session = ort.InferenceSession(str(d / MODEL_FILE), sess_options=opts, providers=["CPUExecutionProvider"])
            self._inputs = {i.name for i in self._session.get_inputs()}
            tok = Tokenizer.from_file(str(d / "tokenizer.json"))
            tok.enable_truncation(max_length=64)
            tok.enable_padding()
            self._tok = tok
            labels, texts = [], []
            for intent, phrases in self.examples.items():
                for p in phrases:
                    labels.append(intent)
                    texts.append(p)
            self._labels = labels
            self._matrix = self.embed(texts)
            log.info("Meaning matcher ready (%d examples) in %.1fs", len(texts), time.monotonic() - t0)
            return True
        except Exception as exc:
            self.error = exc
            log.warning("Meaning matcher unavailable, using rules only: %s", exc)
            return False
        finally:
            self._ready.set()

    def load_async(self) -> None:
        threading.Thread(target=self.load, name="semantic-load", daemon=True).start()

    @property
    def ready(self) -> bool:
        return self._matrix is not None

    # ------------------------------------------------------------ inference
    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            enc = self._tok.encode_batch([t.lower() for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feeds)[0]  # [batch, tokens, 384]
        m = mask[..., None].astype(np.float32)
        vec = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)  # mean pooling
        return vec / np.linalg.norm(vec, axis=1, keepdims=True)

    def scores(self, text: str) -> dict[str, float]:
        """Best similarity per intent (0..1)."""
        sims = self._matrix @ self.embed([text])[0]
        best: dict[str, float] = {}
        for label, s in zip(self._labels, sims):
            if s > best.get(label, -1.0):
                best[label] = float(s)
        return best

    def classify(self, text: str) -> tuple[str | None, float]:
        """(intent, score) if confident, else (None, best score)."""
        if not self.ready or not text.strip():
            return None, 0.0
        scores = self.scores(text)
        lowered = text.lower()
        # open vs close: let the verb decide, then compare against everything else
        if {"open_gmail", "close_gmail"} <= scores.keys():
            if _has(lowered, _CLOSE_WORDS) and not _has(lowered, _OPEN_WORDS):
                scores["close_gmail"] = max(scores["close_gmail"], scores["open_gmail"])
                scores["open_gmail"] = 0.0
            elif _has(lowered, _OPEN_WORDS) and not _has(lowered, _CLOSE_WORDS):
                scores["open_gmail"] = max(scores["close_gmail"], scores["open_gmail"])
                scores["close_gmail"] = 0.0
            elif scores["close_gmail"] > scores["open_gmail"]:
                scores["close_gmail"] = 0.0  # no closing verb at all: never close anything on a guess
            if _has(lowered, _MAIL_WORDS) and (_has(lowered, _CLOSE_WORDS) or _has(lowered, _OPEN_WORDS)):
                scores.pop("out_of_scope", None)  # "close the email window" is about Gmail, not "open youtube"
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        (best, s1), (_, s2) = ranked[0], ranked[1]
        need = THRESHOLDS.get(best, THRESHOLDS["default"])
        log.debug("meaning: %r -> %s", text, ", ".join(f"{k}={v:.2f}" for k, v in ranked[:3]))
        if (s1 >= need and s1 - s2 >= MARGIN) or (s1 >= need - CLEAR_SLACK and s1 - s2 >= CLEAR_MARGIN):
            return best, s1
        return None, s1
