"""Offline check of the voice pipeline without a microphone.

Synthesises spoken commands with several Edge voices (incl. Filipino-English),
then runs them through the real Vosk wake-word detector, Whisper, and the
intent parser.  Needs internet (for edge-tts) the first time; audio is cached.

    python tools\\voice_selftest.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rika.config import load_config  # noqa: E402
from rika.intents import account_terms, parse, parse_yes_no, parse_choice  # noqa: E402
from rika.stt import Transcriber, build_prompt  # noqa: E402
from rika.tts import EdgeEngine  # noqa: E402

VOICES = ["en-US-GuyNeural", "en-PH-RosaNeural", "en-PH-JamesNeural", "en-US-JennyNeural"]
COMMANDS = [
    ("Rika, print document one dot pdf.", "print_file", "document1.pdf"),
    ("Rika, open RJO printing service Gmail.", "open_gmail", None),
    ("Rika, print report dot docx.", "print_file", "report.docx"),
    ("Rika, could you print resume dot pdf?", "print_file", "resume.pdf"),
    ("Rika, what can you do?", "help", None),
    ("Rika, stop.", "stop", None),
]
NEGATIVES = [
    "Erica went to the market.", "The printer is ready.", "Ricky, can you pass the paper?", "Really nice weather.",
    "Rita printed the receipt.", "Eureka, I found it!", "Rick, open the door.", "Pakiabot naman yung papel.",
]
REPLIES = [("Yes.", "yesno", True), ("Sure!", "yesno", True), ("Nope.", "yesno", False), ("No.", "yesno", False), ("The second one.", "choice", 1)]

CACHE = ROOT / "build" / "selftest_audio"


def decode(path: Path) -> np.ndarray:
    import av

    out = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            out += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        out += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    return np.concatenate(out).astype(np.float32) / 32768.0


def synth(text: str, voice: str) -> np.ndarray:
    import hashlib

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{voice}_{hashlib.md5(text.encode()).hexdigest()[:10]}.mp3"
    if not path.exists():
        EdgeEngine({"voice": voice, "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}).synthesize(text, path)
    return decode(path)


def vosk_wake(model, words, audio: np.ndarray, sensitivity=0.5):
    import vosk

    from rika.wakeword import STOP_WORDS, build_grammar

    grammar = build_grammar(model, words)
    rec = vosk.KaldiRecognizer(model, 16000, grammar)
    rec.SetWords(True)
    pcm = (np.concatenate([np.zeros(8000), audio, np.zeros(16000)]) * 32767).astype(np.int16).tobytes()
    results = []
    for i in range(0, len(pcm), 960):
        if rec.AcceptWaveform(pcm[i : i + 960]):
            results.append(json.loads(rec.Result()))
    results.append(json.loads(rec.FinalResult()))
    min_conf = 0.95 - 0.7 * sensitivity
    for r in results:
        for j, w in enumerate(r.get("result", [])):
            if w["word"] in words and w["conf"] >= min_conf:
                after = [x["word"] for x in r["result"][j + 1 : j + 3]]
                return True, w["conf"], ("stop" if any(a in STOP_WORDS for a in after) else None)
    best = max((w["conf"] for r in results for w in r.get("result", []) if w["word"] in words), default=0.0)
    return False, best, None


def main() -> int:
    import vosk

    vosk.SetLogLevel(-1)
    cfg = load_config()
    model = vosk.Model(str(ROOT / "models" / "vosk-model-small-en-us-0.15"))
    words = [w for w in cfg.wake_words if model.vosk_model_find_word(w) >= 0]
    print(f"Wake words: {words}")
    tr = Transcriber(cfg.stt, build_prompt(cfg.accounts))
    tr.load()
    terms = account_terms(cfg.accounts)
    ok = total = 0

    print("== Commands (wake word + whisper + intent) ==")
    for voice in VOICES:
        for text, want_intent, want_file in COMMANDS:
            audio = synth(text, voice)
            woke, conf, cmd = vosk_wake(model, words, audio)
            t0 = time.monotonic()
            heard = tr.transcribe(audio)
            dt = time.monotonic() - t0
            intent = parse(heard, account_terms=terms)
            good = woke and intent.name == want_intent and (want_file is None or intent.slots.get("filename") == want_file)
            ok += good
            total += 1
            print(f"{'PASS' if good else 'FAIL'} {voice:18} wake={woke!s:5}({conf:.2f}{',stop' if cmd else ''}) "
                  f"stt={dt:.1f}s {heard!r} -> {intent.name} {intent.slots.get('filename', intent.slots.get('account', ''))}")

    print("\n== Negatives (wake word should NOT fire) ==")
    for voice in VOICES[:2]:
        for text in NEGATIVES:
            woke, conf, _ = vosk_wake(model, words, synth(text, voice))
            ok += not woke
            total += 1
            print(f"{'PASS' if not woke else 'FAIL'} {voice:18} {text!r} wake={woke} best_conf={conf:.2f}")

    print("\n== Replies ==")
    for voice in VOICES[:2]:
        for text, kind, want in REPLIES:
            heard = tr.transcribe(synth(text, voice), hint="Yes. No. Yeah. Nope. Sure. Cancel. First, second, third.")
            got = parse_yes_no(heard) if kind == "yesno" else parse_choice(heard, 3)
            ok += got == want
            total += 1
            print(f"{'PASS' if got == want else 'FAIL'} {voice:18} {text!r} heard {heard!r} -> {got}")

    print(f"\n{ok}/{total} passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
