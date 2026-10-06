"""End-to-end smoke test of the voice controller with a fake microphone.

Feeds synthesized speech ("Rika" -> "print document one dot pdf" -> "yes")
through the real wake word, recorder, Whisper and Assistant. Playback, Gmail
and the printer are stubbed so nothing is played, downloaded or printed.

    python tools\\controller_smoketest.py
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np  # noqa: E402

from voice_selftest import synth  # noqa: E402

import rika.app as app_mod  # noqa: E402
import rika.tts as tts_mod  # noqa: E402
from rika.actions import printer  # noqa: E402
from rika.audio import FRAME_SAMPLES, MicHub  # noqa: E402
from rika.config import load_config  # noqa: E402
from rika.lines import LINES  # noqa: E402
from rika.logsetup import setup_logging  # noqa: E402
from rika.mailbox import Attachment  # noqa: E402


class FakeHub(MicHub):
    """Delivers scripted audio in real time. Each new recorder subscription gets the next clip."""

    def __init__(self, first_clip: np.ndarray, replies: list[np.ndarray]):
        super().__init__()
        self.first_clip = first_clip
        self.pending: list[np.ndarray] = []
        self.replies = list(replies)
        self.subs_seen = 0
        self._alive = True

    @property
    def ok(self) -> bool:
        return self._alive

    def start(self) -> None:
        threading.Thread(target=self._feed, daemon=True).start()

    def stop(self) -> None:
        self._alive = False

    def subscribe(self, maxsize: int = 400, tag: str = ""):
        q = super().subscribe(maxsize, tag)
        if tag == "continuation":  # rest of a one-breath command, not a new question
            return q
        self.subs_seen += 1
        if self.subs_seen == 1:  # the wake word detector is listening now -> say "Rika" after the greeting
            threading.Timer(3.0, lambda: self.pending.append(self.first_clip)).start()
        elif self.replies:
            clip = self.replies.pop(0)
            threading.Timer(0.4, lambda: self.pending.append(clip)).start()
        return q

    def _feed(self) -> None:
        rng = np.random.default_rng(0)
        buf = np.zeros(0, dtype=np.int16)
        while self._alive:
            if len(buf) < FRAME_SAMPLES:
                if self.pending:
                    clip = (self.pending.pop(0) * 32767 * 0.8).astype(np.int16)
                    buf = np.concatenate([buf, clip])
                else:
                    buf = np.concatenate([buf, (rng.normal(0, 40, FRAME_SAMPLES)).astype(np.int16)])
            frame, buf = buf[:FRAME_SAMPLES], buf[FRAME_SAMPLES:]
            self._callback(frame.reshape(-1, 1), FRAME_SAMPLES, None, None)
            time.sleep(FRAME_SAMPLES / 16000)


class FakeMailbox:
    def connect(self): ...

    def search_by_name(self, name):
        return [Attachment("m1", "a1", "document1.pdf", "Juan", "juan@example.com", datetime.now())]

    def recent_attachments(self, days, n):
        now = datetime.now()
        return [
            Attachment("m2", "a2", "flyer.pdf", "Pedro", "pedro@example.com", now),
            Attachment("m3", "a3", "resume.docx", "Maria", "maria@example.com", now),
        ]

    def download(self, att, dest):
        p = Path(dest) / att.filename
        p.write_bytes(b"%PDF")
        return p


def run_scenario(name: str, wake_text: str, replies: list[str], expect_print: bool,
                 expect_lines: tuple[str, ...] = ()) -> bool:
    """Talk to Rika; the scenario ends when she goes back to sleep ("Just call my name...")."""
    cfg = load_config()
    cfg.data["follow_up_seconds"] = 3  # shorter than the default 8 s to keep the test quick
    spoken: list[str] = []
    printed: list[str] = []

    tts_mod.play_file = lambda path, should_stop=None: time.sleep(0.2)
    # no real synthesis: every line "plays" a dummy file
    dummy = Path(tempfile.gettempdir()) / "rika_smoke.wav"
    dummy.write_bytes(b"RIFF")
    tts_mod.Speaker.prepare_async = lambda self, lines: None
    tts_mod.Speaker.audio_for = lambda self, text: dummy
    app_mod.play_chime = lambda path, wait=True: None
    printer.resolve_printer = lambda n: ("EPSON L120 Series", False)
    printer.check_printer_ready = lambda n: None
    printer.count_pages = lambda p: 3
    printer.print_document = lambda path, *a, **k: printed.append(Path(path).name) or "sent (fake)"

    hub = FakeHub(synth(wake_text, "en-PH-RosaNeural"), [synth(r, "en-PH-RosaNeural") for r in replies])
    ctl = app_mod.Controller(cfg, dry_run=True)
    ctl.hub = hub
    ctl.io.hub = hub
    ctl.assistant._mailbox_factory = FakeMailbox
    orig_say = ctl.io._say
    ctl.io._say = lambda text: (spoken.append(text), orig_say(text))[1]
    states: list[str] = []
    ctl.state_listeners.append(states.append)

    ctl.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(s in LINES["sleep"] + LINES["stop_sleep"] for s in spoken) and not ctl.busy.is_set():
            break
        time.sleep(0.2)
    time.sleep(0.5)
    ctl.shutdown()

    went_to_sleep = bool(spoken) and spoken[-1] in LINES["sleep"] + LINES["stop_sleep"]
    has_lines = all(any(s in LINES[k] for s in spoken) for k in expect_lines)
    ok = bool(printed) == expect_print and went_to_sleep and has_lines
    print(f"\n=== {name}: {'PASS' if ok else 'FAIL'} ===")
    for s in spoken:
        print(f"  Rika: {s}")
    print(f"  printed: {printed}")
    print(f"  states: {' -> '.join(dict.fromkeys(states))}")
    return ok


def main() -> int:
    setup_logging(verbose=False)
    results = [
        run_scenario("voice print, confirm yes", "Rika.", ["Print document one dot pdf.", "Yes."], True),
        run_scenario("voice print, say no", "Rika.", ["Print document one dot pdf.", "No."], False),
        run_scenario("help", "Rika.", ["What can you do?"], False),
        run_scenario("one breath: 'Rika, print ...'", "Rika, print document one dot pdf.", ["Yep."], True),
        # conversations: follow-ups without saying "Rika" again, then she goes to sleep
        run_scenario("any files? -> pick second -> yes -> silence", "Rika.",
                     ["Do you see a printable file in my Gmail?", "The second one.", "Yes."], True,
                     ("checking_inbox", "done", "sleep")),
        run_scenario("help -> thanks -> that's all", "Rika.", ["What are you capable of?", "Thank you!", "That's all."],
                     False, ("help", "thanks", "sleep")),
        run_scenario("out of scope -> silence", "Rika.", ["What's the weather today?"], False, ("out_of_scope", "sleep")),
        run_scenario("one breath stop", "Rika.", ["Stop."], False, ("stop_sleep",)),
    ]
    print(f"\n{sum(results)}/{len(results)} scenarios passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
