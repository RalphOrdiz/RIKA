"""Microphone hub, silence-detecting recorder, and audio playback.

One PortAudio input stream is shared: the wake-word detector and the command
recorder each subscribe to their own queue of 30 ms int16 frames (16 kHz mono).
Playback of MP3/WAV uses Windows MCI (winmm) - no extra codecs needed.
"""
from __future__ import annotations

import ctypes
import itertools
import logging
import queue
import threading
import time
import winsound
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np

from rika.errors import Cancelled, NoMicrophone

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480  # 30 ms


def list_input_devices() -> str:
    import sounddevice as sd

    lines = []
    try:
        default_in = sd.default.device[0]
        for idx, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                api = sd.query_hostapis(dev["hostapi"])["name"]
                mark = "*" if idx == default_in else " "
                lines.append(f"{mark} {idx:3}  {dev['name']}  [{api}]")
    except Exception as exc:
        return f"Could not list microphones: {exc}"
    lines.append('\n* = default. Put the number (or part of the name) in config.json -> "mic_device".')
    return "\n".join(lines)


class MicHub:
    """Shared microphone stream with a watchdog that reopens it if the mic disappears."""

    def __init__(self, device: int | str | None = None, on_error: Callable[[Exception], None] | None = None):
        self.device = device
        self.on_error = on_error
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._stream = None
        self._src_rate = SAMPLE_RATE
        self._last_frame = 0.0
        self._running = False
        self._error_reported = False
        self.noise_floor = 150.0  # int16 RMS, adapted continuously

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._running = True
        try:
            self._open()
        except NoMicrophone as exc:
            self._report(exc)
        threading.Thread(target=self._watchdog, name="mic-watchdog", daemon=True).start()

    def stop(self) -> None:
        self._running = False
        self._close()

    @property
    def ok(self) -> bool:
        return self._stream is not None and time.monotonic() - self._last_frame < 3.0

    def _open(self) -> None:
        import sounddevice as sd

        self._close()
        try:
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES,
                    device=self.device, callback=self._callback,
                )
                self._src_rate = SAMPLE_RATE
            except sd.PortAudioError:
                info = sd.query_devices(self.device, "input")
                rate = int(info["default_samplerate"])
                stream = sd.InputStream(
                    samplerate=rate, channels=1, dtype="int16", blocksize=int(rate * 0.03),
                    device=self.device, callback=self._callback,
                )
                self._src_rate = rate
                log.info("Mic does not support 16 kHz; capturing at %d Hz and resampling", rate)
            stream.start()
        except Exception as exc:  # PortAudioError, ValueError (no device), ...
            raise NoMicrophone(f"Cannot open microphone {self.device!r}: {exc}")
        self._stream = stream
        self._last_frame = time.monotonic()
        if self._error_reported:
            log.info("Microphone is back")
        self._error_reported = False
        log.info("Microphone opened: %s", getattr(stream, "device", self.device))

    def _close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _watchdog(self) -> None:
        while self._running:
            time.sleep(2.0)
            if not self._running:
                break
            if self._stream is None or not self._stream.active or time.monotonic() - self._last_frame > 3.0:
                try:
                    self._open()
                except NoMicrophone as exc:
                    self._report(exc)
                    time.sleep(8.0)

    def _report(self, exc: Exception) -> None:
        if self._error_reported:
            return
        self._error_reported = True
        log.error("%s", exc)
        if self.on_error:
            try:
                self.on_error(exc)
            except Exception:
                log.exception("mic on_error callback failed")

    # ------------------------------------------------------------ data
    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("mic status: %s", status)
        data = indata[:, 0].copy()
        if self._src_rate != SAMPLE_RATE:
            n = int(round(len(data) * SAMPLE_RATE / self._src_rate))
            data = np.interp(
                np.linspace(0, len(data) - 1, n), np.arange(len(data)), data.astype(np.float32)
            ).astype(np.int16)
        self._last_frame = time.monotonic()
        rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) if len(data) else 0.0
        # fast to follow quiet, slow to follow loud -> tracks the background noise level
        if rms < self.noise_floor:
            self.noise_floor = 0.9 * self.noise_floor + 0.1 * rms
        else:
            self.noise_floor = 0.998 * self.noise_floor + 0.002 * rms
        self.noise_floor = max(self.noise_floor, 20.0)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass

    def subscribe(self, maxsize: int = 400, tag: str = "") -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)


def record_utterance(
    hub: MicHub,
    cancel_event: threading.Event | None = None,
    silence_s: float = 1.5,
    max_s: float = 15.0,
    start_timeout_s: float = 6.0,
    q: queue.Queue | None = None,
    already_speaking: bool = False,
) -> np.ndarray | None:
    """Record until the speaker stops talking. Returns float32 audio in [-1, 1], or None if no speech.

    ``q``/``already_speaking`` continue an utterance that is already in progress
    (the wake word detector subscribed ``q`` the moment it heard "Rika, ...").
    """
    if not hub.ok:
        if q is not None:
            hub.unsubscribe(q)
        raise NoMicrophone("Microphone is not delivering audio")
    q = q or hub.subscribe()
    frame_s = FRAME_SAMPLES / SAMPLE_RATE
    preroll: deque = deque(maxlen=int(0.3 / frame_s))
    frames: list[np.ndarray] = []
    started = already_speaking
    voiced_run = 0
    silent_for = 0.0
    t_start = time.monotonic()
    t_speech = t_start
    start_thr = max(hub.noise_floor * 3.0, 300.0)
    end_thr = max(hub.noise_floor * 2.0, 200.0)
    log.debug("Recording: noise_floor=%.0f start_thr=%.0f end_thr=%.0f", hub.noise_floor, start_thr, end_thr)
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            try:
                frame = q.get(timeout=1.0)
            except queue.Empty:
                raise NoMicrophone("Microphone stopped delivering audio while recording")
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
            now = time.monotonic()
            if not started:
                preroll.append(frame)
                voiced_run = voiced_run + 1 if rms > start_thr else 0
                if voiced_run >= 3:  # ~90 ms of speech
                    started = True
                    t_speech = now
                    frames.extend(preroll)
                elif now - t_start > start_timeout_s:
                    log.info("No speech within %.1fs", start_timeout_s)
                    return None
                continue
            frames.append(frame)
            silent_for = silent_for + len(frame) / SAMPLE_RATE if rms < end_thr else 0.0
            if silent_for >= silence_s or now - t_speech > max_s:
                break
    finally:
        hub.unsubscribe(q)
    if not frames:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(frames).astype(np.float32) / 32768.0
    # drop most of the trailing silence
    trim = int(max(0.0, silent_for - 0.3) * SAMPLE_RATE)
    if trim:
        audio = audio[:-trim]
    log.info("Recorded %.1fs of audio", len(audio) / SAMPLE_RATE)
    return audio


# ------------------------------------------------------------------ playback

_winmm = ctypes.windll.winmm
_alias_counter = itertools.count()


def _mci(command: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(command, buf, 255, None)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        _winmm.mciGetErrorStringW(err, msg, 255)
        raise OSError(f"MCI error {err} for '{command}': {msg.value}")
    return buf.value


def play_file(path: Path, should_stop: Callable[[], bool] | None = None) -> None:
    """Play an MP3/WAV file, blocking until done or until ``should_stop()`` returns True."""
    alias = f"rika{next(_alias_counter)}"
    _mci(f'open "{path}" type mpegvideo alias {alias}')
    try:
        _mci(f"play {alias}")
        time.sleep(0.05)
        while True:
            if should_stop is not None and should_stop():
                _mci(f"stop {alias}")
                break
            if _mci(f"status {alias} mode") != "playing":
                break
            time.sleep(0.03)
    finally:
        try:
            _mci(f"close {alias}")
        except OSError:
            pass


def play_chime(path: Path, wait: bool = True) -> None:
    try:
        winsound.PlaySound(str(path), winsound.SND_FILENAME | (0 if wait else winsound.SND_ASYNC))
    except RuntimeError as exc:
        log.warning("Could not play chime %s: %s", path, exc)
