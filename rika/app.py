"""Voice controller: wires mic, wake word, hotkey, STT, TTS and the Assistant.

Threads:
    PortAudio callback -> MicHub queues
    wakeword           -> Vosk/openWakeWord, calls _on_wake()
    hotkey             -> Win32 message loop, calls trigger()
    session            -> runs one conversation at a time (never the tray thread)
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from rika import paths
from rika.assistant import Assistant
from rika.audio import MicHub, play_chime, record_utterance
from rika.config import Config
from rika.errors import Cancelled, NoMicrophone, RikaError
from rika.hotkey import HotkeyListener
from rika.interaction import VoiceIO
from rika.lines import join_names, line, prerender_lines
from rika.semantic import SemanticMatcher
from rika.stt import Transcriber, build_prompt
from rika.tts import Speaker, prune_cache
from rika.wakeword import Utterance, create_detector

log = logging.getLogger(__name__)


class Controller:
    def __init__(
        self,
        cfg: Config,
        dry_run: bool = False,
        notify: Callable[[str], None] | None = None,
        echo: bool = False,
    ):
        self.cfg = cfg
        self.notify = notify or (lambda msg: None)
        self.state = "starting"
        self.state_listeners: list[Callable[[str], None]] = []
        self.voice_status: str | None = None  # "Downloading voice model 40%" etc. (shown in the tray tooltip)
        self.status_listeners: list[Callable[[str | None], None]] = []
        self.speaker = Speaker(cfg.tts, on_status=self._on_voice_status)
        self.hub = MicHub(cfg["mic_device"], on_error=self._on_mic_error)
        self.transcriber = Transcriber(cfg.stt, build_prompt(cfg.accounts))
        self.io = VoiceIO(self.speaker, self.hub, self.transcriber, cfg, on_state=self._set_state, echo=echo)
        self.semantic = SemanticMatcher()
        self.assistant = Assistant(cfg, self.io, dry_run=dry_run, semantic=self.semantic)
        self.jobs: queue.Queue = queue.Queue()
        self.busy = threading.Event()
        self.paused = threading.Event()
        self.detector = None
        self.hotkey = HotkeyListener(cfg["hotkey"], lambda: self.trigger("hotkey"))
        self._worker = threading.Thread(target=self._work, name="session", daemon=True)
        self._chime = paths.assets_dir() / "chime.wav"

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._worker.start()
        self.transcriber.load_async()
        self.semantic.load_async()
        prune_cache()
        self.speaker.prepare_async(self.prerender_list())
        self.hub.start()
        try:
            self.detector = create_detector(self.cfg, self.hub, self._on_wake)
            self.detector.start()
        except Exception as exc:
            log.exception("Wake word detector failed to start")
            self.notify(f"Wake word is unavailable ({exc}). Use {self.cfg['hotkey'].upper()} instead.")
        self.hotkey.start()
        self.jobs.put(self._startup_job)

    def shutdown(self) -> None:
        log.info("Shutting down")
        self.interrupt()
        if self.detector:
            self.detector.stop()
        self.hotkey.stop()
        self.hub.stop()
        self.speaker.close()
        self.jobs.put(None)

    def reload(self, new_cfg: Config) -> None:
        """Apply a reloaded config. Hotkey, mic, wake words and Whisper model need a restart."""
        restart_keys = ("hotkey", "mic_device", "wake_word", "stt")
        changed = [k for k in restart_keys if new_cfg.data.get(k) != self.cfg.data.get(k)]
        voice_changed = new_cfg.data.get("tts") != self.cfg.data.get("tts")
        self.cfg.data = new_cfg.data  # same Config object is shared with the Assistant and VoiceIO
        self.cfg.problems = new_cfg.problems
        old = self.speaker
        self.speaker = Speaker(self.cfg.tts, on_status=self._on_voice_status)
        self.io.speaker = self.speaker
        old.close()
        self.speaker.prepare_async(self.prerender_list())
        if voice_changed:
            self._enqueue(lambda: self._say_only(line("new_voice")))
        self.transcriber.prompt = build_prompt(self.cfg.accounts)
        self.assistant._mailbox = None
        if self.detector:
            self.detector.set_sensitivity(float(self.cfg["wake_word_sensitivity"]))
        if changed:
            log.warning("Settings changed that need a restart of Rika: %s", changed)
            self.notify(f"Restart Rika to apply: {', '.join(changed)}")
        log.info("Config reloaded")

    def prerender_list(self) -> list[str]:
        """Every line Rika may say except those with file/sender names, filled with config values."""
        from rika.actions.printer import short_printer_name

        names = [a.spoken_name for a in self.cfg.accounts.values()]
        values = {
            "account": names,
            "accounts": [join_names(names)],
            "printer": [short_printer_name(self.cfg.printer)] if self.cfg.printer else [],
            "days": [str(self.cfg["search_days"])],
            "count": ["2", "3"],
        }
        return prerender_lines(values)

    def _on_voice_status(self, status: str | None) -> None:
        self.voice_status = status
        for fn in list(self.status_listeners):
            try:
                fn(status)
            except Exception:
                log.exception("status listener failed")

    def _startup_job(self) -> None:
        self.io.cancel_event.clear()
        self.io.say(line("ready"))
        if self.cfg.problems:
            self.io.say(line("config_problems"))

    # ------------------------------------------------------------ state
    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        for fn in list(self.state_listeners):
            try:
                fn(state)
            except Exception:
                log.exception("state listener failed")

    def idle_state(self) -> str:
        return "paused" if self.paused.is_set() else "idle"

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.paused.set()
        else:
            self.paused.clear()
        if self.detector:
            (self.detector.paused.set if paused else self.detector.paused.clear)()
        log.info("Wake word %s", "paused" if paused else "resumed")
        if not self.busy.is_set():
            self._set_state(self.idle_state())

    # ------------------------------------------------------------ triggers
    def _on_wake(self, command: str | None, conf: float, utterance: Utterance | None = None) -> None:
        def drop() -> None:
            if utterance is not None:
                self.hub.unsubscribe(utterance.continuation)

        if self.paused.is_set() or self.state == "listening":
            return drop()  # paused, or the user is already talking to Rika
        if self.speaker.said_own_name_recently():
            log.info("Ignoring wake word: that was Rika saying her own name")
            return drop()
        if self.speaker.speaking.is_set() and 0 < conf < 0.9:
            log.info("Ignoring low-confidence wake while speaking (probably echo)")
            return drop()
        if command == "stop":
            self.interrupt()
            self._enqueue(lambda: self._say_only(line("stop_sleep")))
            return drop()
        self.trigger("wake", utterance=utterance)

    def trigger(self, source: str = "manual", utterance: Utterance | None = None) -> None:
        """Start a listening session (wake word, hotkey, or tray menu)."""
        was_busy = self.busy.is_set()
        if was_busy:
            log.info("Trigger (%s) while busy -> interrupting current action", source)
            self.interrupt()
        queued = self._enqueue(lambda: self._session(source, interrupted=was_busy, utterance=utterance))
        if not queued and utterance is not None:
            self.hub.unsubscribe(utterance.continuation)

    def interrupt(self) -> None:
        if self.busy.is_set():
            self.io.cancel_event.set()
            self.speaker.stop()
            self.assistant.interrupted = True

    def _enqueue(self, job: Callable[[], None]) -> bool:
        if self.jobs.qsize() == 0:
            self.jobs.put(job)
            return True
        return False

    # ------------------------------------------------------------ worker
    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                break
            self.busy.set()
            try:
                job()
            except Cancelled:
                pass
            except Exception:
                log.exception("Session crashed")
            finally:
                self.busy.clear()
                self._set_state(self.idle_state())

    def _say_only(self, text: str) -> None:
        self.io.cancel_event.clear()
        self.io.say(text)

    def _session(self, source: str, interrupted: bool, utterance: Utterance | None = None) -> None:
        self.io.cancel_event.clear()
        log.info("Session started by %s", source)
        try:
            self._set_state("listening")
            if utterance is not None:
                play_chime(self._chime, wait=False)  # don't block: the user may still be talking
                text = self._one_breath_command(utterance)
            else:
                play_chime(self._chime)
                text = ""
            if not text:
                if self.cfg["greet_on_wake"]:
                    self.io.say(line("wake"))
                text = self.io.listen()
            if not text:
                self.io.say(line("cancelled") if interrupted else line("sleep"))
                return
            self.assistant.interrupted = interrupted
            self._converse(text)
        except Cancelled:
            log.info("Session cancelled")
        except NoMicrophone as exc:
            log.error("%s", exc)
            self._speak_error(exc)
        except RikaError as exc:
            log.error("%s: %s", type(exc).__name__, exc)
            self._speak_error(exc)
        except Exception:
            log.exception("Unexpected error in session")
            self._speak_error(RikaError("unexpected"))

    # Intents that end the conversation (their reply already says "call my name").
    _ENDING_INTENTS = {"sleep", "stop"}

    def _converse(self, text: str) -> None:
        """Handle a request, then keep listening for follow-ups without the wake word.

        Ends - with a "Just call my name, I'll be sleeping for a while!" style line -
        when the user goes quiet, says "that's all"/"stop", or Rika twice in a row
        can't understand (e.g. people talking to each other in the shop).
        """
        follow_up = float(self.cfg["follow_up_seconds"])
        misses = 0
        while True:
            self._set_state("working")
            intent = self.assistant.handle(text)
            if intent.name in self._ENDING_INTENTS:
                return
            if follow_up <= 0:
                return
            misses = misses + 1 if intent.name == "unknown" else 0
            if misses >= 2:
                self.io.say(line("sleep"))
                return
            text = self.io.listen(start_timeout=follow_up)
            if not text:
                self.io.say(line("sleep"))
                return
            log.info("Follow-up: %r", text)

    def _one_breath_command(self, utterance: Utterance) -> str:
        """'Rika, print document1.pdf' said in one go: transcribe it instead of asking again."""
        import numpy as np

        from rika.intents import clean_utterance

        # Vosk may cut the utterance at a short pause - keep listening until the user is really done.
        rest = record_utterance(
            self.hub,
            self.io.cancel_event,
            silence_s=max(0.6, float(self.cfg["silence_seconds"]) - 0.5),
            max_s=float(self.cfg["max_record_seconds"]),
            q=utterance.continuation,
            already_speaking=True,
        )
        audio = np.concatenate([utterance.audio, rest]) if rest is not None and len(rest) else utterance.audio
        self._set_state("thinking")
        text = self.transcriber.transcribe(audio)
        if not clean_utterance(text, self.assistant.wake_words):
            return ""  # it was only "Rika" -> greet and listen as usual
        if self.io.echo:
            print(f"You:  {text}", flush=True)
        log.info("One-breath command: %r", text)
        return text

    def _speak_error(self, exc: RikaError) -> None:
        msg = line(exc.line_key, **exc.fmt)
        self.notify(msg)
        try:
            self.io.cancel_event.clear()
            self.io.say(msg)
        except Cancelled:
            pass

    def _on_mic_error(self, exc: Exception) -> None:
        msg = line("error_no_mic")
        self.notify(msg)
        self._set_state("error")
        self._enqueue(lambda: self._say_only(msg))


def run_console(cfg: Config, dry_run: bool = False) -> int:
    """Voice mode in a console window - handy for testing the mic and wake word."""
    ctl = Controller(cfg, dry_run=dry_run, notify=lambda m: print(f"[notice] {m}", flush=True), echo=True)
    ctl.state_listeners.append(lambda s: print(f"  ({s})", flush=True))
    ctl.start()
    print(f"Rika voice console. Say 'Rika' or press {cfg['hotkey'].upper()}. Press Ctrl+C to quit.", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    ctl.shutdown()
    return 0
