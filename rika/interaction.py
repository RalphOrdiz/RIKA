"""How Rika talks and listens.

The Assistant only ever calls ``say()`` and ``ask()``, so the exact same
conversation logic runs in --text mode (keyboard) and voice mode (mic + TTS).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from rika.errors import Cancelled

log = logging.getLogger(__name__)


class Interaction:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def say(self, text: str) -> None:
        self.check_cancel()
        log.info("Rika: %s", text)
        self._say(text)
        self.check_cancel()

    def ask(self, prompt: str, hint: str = "") -> str | None:
        """Say ``prompt`` and return the user's reply (None if nothing was heard)."""
        self.say(prompt)
        return self.listen(hint)

    def listen(self, hint: str = "", start_timeout: float | None = None) -> str | None:
        """Wait for the user to speak (``start_timeout`` seconds, default from config) and return the text."""
        self.check_cancel()
        reply = self._listen(hint, start_timeout)
        self.check_cancel()
        log.info("User said: %r", reply)
        return reply

    # subclasses implement these
    def _say(self, text: str) -> None:
        raise NotImplementedError

    def _listen(self, hint: str, start_timeout: float | None = None) -> str | None:
        raise NotImplementedError


class TextIO(Interaction):
    """Typed conversation for --text mode. Optionally speaks too (--speak)."""

    def __init__(self, speaker=None, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print):
        super().__init__()
        self.speaker = speaker
        self._input = input_fn
        self._output = output_fn

    def _say(self, text: str) -> None:
        self._output(f"Rika: {text}")
        if self.speaker is not None:
            self.speaker.say(text, cancel_event=self.cancel_event)

    def _listen(self, hint: str, start_timeout: float | None = None) -> str | None:
        try:
            return self._input("You: ").strip() or None
        except EOFError:
            raise Cancelled()


class VoiceIO(Interaction):
    """Speak with TTS, listen with the mic + Whisper."""

    def __init__(self, speaker, hub, transcriber, cfg, on_state: Callable[[str], None] | None = None, echo: bool = False):
        super().__init__()
        self.speaker = speaker
        self.hub = hub
        self.transcriber = transcriber
        self.cfg = cfg
        self.on_state = on_state or (lambda s: None)
        self.echo = echo

    def _say(self, text: str) -> None:
        if self.echo:
            print(f"Rika: {text}", flush=True)
        self.on_state("speaking")
        self.speaker.say(text, cancel_event=self.cancel_event)

    def _listen(self, hint: str, start_timeout: float | None = None) -> str | None:
        from rika.audio import record_utterance

        self.on_state("listening")
        audio = record_utterance(
            self.hub,
            self.cancel_event,
            silence_s=float(self.cfg["silence_seconds"]),
            max_s=float(self.cfg["max_record_seconds"]),
            start_timeout_s=float(start_timeout or self.cfg["speech_start_timeout"]),
        )
        if audio is None:
            return None
        self.on_state("thinking")
        text = self.transcriber.transcribe(audio, hint)
        if self.echo:
            print(f"You:  {text or '(nothing)'}", flush=True)
        return text or None


class ScriptedIO(Interaction):
    """For tests: replies come from a list, everything said is recorded."""

    def __init__(self, replies: list[str | None] | None = None):
        super().__init__()
        self.replies = list(replies or [])
        self.said: list[str] = []

    def _say(self, text: str) -> None:
        self.said.append(text)

    def _listen(self, hint: str, start_timeout: float | None = None) -> str | None:
        if not self.replies:
            return None
        return self.replies.pop(0)
