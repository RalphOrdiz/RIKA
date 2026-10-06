"""Voice Lab: listen to Kokoro's built-in female voices side by side and pick Rika's voice.

Renders the same test lines for every built-in English female voice over a
grid of pitch (+0, +2, +3, +4 semitones) and speed (1.0, 1.15, 1.25), writes
voice_lab_output\\index.html to compare them, and (while running) serves that
page on http://127.0.0.1 so you can try blends and click "Use this voice".

    python voice_lab.py                         render everything, open the page
    python voice_lab.py --quick                 only pitch +0 / speed 1.0 (fast)
    python voice_lab.py --voices af_bella,af_sky
    python voice_lab.py --blend "af_bella:0.6,af_nicole:0.4"
    python voice_lab.py --use "af_bella:0.6,af_nicole:0.4" --pitch 2 --speed 1.15
    Rika.exe --voice-lab ...                    same, from the built app

Only Kokoro's built-in voices (Apache-2.0) are used - no voice cloning.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from rika import paths
from rika.config import Config, load_config, save_voice_choice
from rika.tts import (
    KOKORO_RATE, KokoroEngine, blend_label, female_english_voices, normalize_blend, parse_blend, pitch_shift,
    write_wav,
)

log = logging.getLogger(__name__)

TEST_LINES = [
    "Hi hi! I'm Rika, your printing assistant!",
    "Printing now~!",
    "Uh oh, I can't find that file...",
]
PITCHES = [0, 2, 3, 4]
SPEEDS = [1.0, 1.15, 1.25]
MANIFEST_VERSION = 1


def default_out_dir() -> Path:
    """voice_lab_output next to Rika (or the project), or in %APPDATA%\\Rika if that is read-only."""
    for base in (paths.app_dir(), paths.data_dir()):
        out = base / "voice_lab_output"
        try:
            (out / "clips").mkdir(parents=True, exist_ok=True)
            probe = out / ".write_test"
            probe.write_text("ok")
            probe.unlink()
            return out
        except OSError:
            continue
    raise OSError("No writable folder for voice_lab_output")


def group_id(voices: list) -> str:
    voices = normalize_blend(voices)
    if len(voices) == 1:
        return voices[0][0]
    return "blend_" + "_".join(f"{n}{round(w * 100)}" for n, w in voices)


def cell_key(pitch: float, speed: float) -> str:
    return f"{pitch:+g}|{speed:.2f}"


def clip_name(gid: str, pitch: float, speed: float, line_no: int) -> str:
    """e.g. af_bella__pitch+2__speed1.15__line1.wav"""
    return f"{gid}__pitch{pitch:+g}__speed{speed:.2f}__line{line_no}.wav"


class VoiceLab:
    def __init__(self, cfg: Config, out_dir: Path | None = None, quick: bool = False):
        self.cfg = cfg
        self.out_dir = out_dir or default_out_dir()
        self.clips_dir = self.out_dir / "clips"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.pitches = [0] if quick else PITCHES
        self.speeds = [1.0] if quick else SPEEDS
        self.engine = KokoroEngine(dict(cfg.tts))
        self.status = {"busy": False, "done": 0, "total": 0, "message": "Starting..."}
        self._lock = threading.RLock()
        self._jobs: list[tuple[list, str]] = []
        self._worker: threading.Thread | None = None
        self.manifest = self._load_manifest()

    # ------------------------------------------------------------ manifest
    def _load_manifest(self) -> dict:
        path = self.out_dir / "manifest.json"
        fresh = {"version": MANIFEST_VERSION, "lines": TEST_LINES, "pitches": PITCHES, "speeds": SPEEDS, "groups": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") == MANIFEST_VERSION and data.get("lines") == TEST_LINES:
                return data
        except (OSError, ValueError):
            pass
        return fresh

    def current_choice(self) -> dict:
        tts = self.cfg.tts
        return {
            "voices": normalize_blend(tts["voices"]),
            "label": blend_label(tts["voices"]),
            "pitch": tts["pitch_semitones"],
            "speed": tts["speed"],
            "engine": tts["engine"],
        }

    def state(self) -> dict:
        with self._lock:
            return {
                "manifest": self.manifest,
                "status": dict(self.status),
                "current": self.current_choice(),
                "voices": self.available_voices(),
            }

    def save(self) -> None:
        with self._lock:
            (self.out_dir / "manifest.json").write_text(json.dumps(self.manifest, indent=1), encoding="utf-8")
            (self.out_dir / "index.html").write_text(build_html(self.state(), api=False), encoding="utf-8")

    # ------------------------------------------------------------ rendering
    def prepare(self, status: Callable[[str | None], None] | None = None) -> None:
        def report(msg):
            with self._lock:
                self.status["message"] = msg or ""
            if status:
                status(msg)

        self.engine.prepare(report)
        report("Ready")

    def available_voices(self) -> list[str]:
        return female_english_voices(self.engine._kokoro) if self.engine.is_ready() else []

    def _group(self, voices: list, kind: str) -> dict:
        gid = group_id(voices)
        with self._lock:
            for g in self.manifest["groups"]:
                if g["id"] == gid:
                    return g
            g = {"id": gid, "label": blend_label(voices), "kind": kind, "voices": normalize_blend(voices),
                 "accent": "UK" if voices[0][0].startswith("bf_") else "US", "clips": {}}
            if kind == "blend":
                self.manifest["groups"].insert(0, g)
            else:
                self.manifest["groups"].append(g)
            return g

    def render_group(self, voices: list, kind: str = "voice") -> str:
        voices = normalize_blend(voices)
        group = self._group(voices, kind)
        formants = bool(self.cfg.tts.get("preserve_formants", True))
        for speed in self.speeds:
            for li, text in enumerate(TEST_LINES, 1):
                base = None
                for pitch in self.pitches:
                    name = clip_name(group["id"], pitch, speed, li)
                    path = self.clips_dir / name
                    if not path.exists():
                        if base is None:
                            base = self.engine.render(text, voices=voices, speed=speed, pitch=0)
                        write_wav(path, pitch_shift(base, KOKORO_RATE, pitch, formants), KOKORO_RATE)
                    with self._lock:
                        cell = group["clips"].setdefault(cell_key(pitch, speed), [None] * len(TEST_LINES))
                        cell[li - 1] = f"clips/{name}"
                        self.status["done"] += 1
                        self.status["message"] = f"Rendering {group['label']}"
        self.save()
        return group["id"]

    def queue(self, voices: list, kind: str = "voice") -> str:
        """Render in the background (one worker, in order)."""
        voices = normalize_blend(voices)
        with self._lock:
            self._jobs.append((voices, kind))
            self.status["total"] += len(self.pitches) * len(self.speeds) * len(TEST_LINES)
            self.status["busy"] = True
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="voice-lab", daemon=True)
                self._worker.start()
        return group_id(voices)

    def _work(self) -> None:
        while True:
            with self._lock:
                if not self._jobs:
                    self.status.update(busy=False, message="All clips ready")
                    return
                voices, kind = self._jobs.pop(0)
            try:
                self.render_group(voices, kind)
            except Exception as exc:
                log.exception("Voice lab render failed for %s", voices)
                with self._lock:
                    self.status["message"] = f"Error: {exc}"

    def queue_all(self, only: list[str] | None = None) -> None:
        for v in only or self.available_voices():
            self.queue([[v, 1.0]], "voice")

    def wait(self, progress: Callable[[dict], None] | None = None) -> None:
        while self._worker is not None and self._worker.is_alive():
            if progress:
                progress(dict(self.status))
            time.sleep(0.5)

    # ------------------------------------------------------------ choosing
    def validate_choice(self, voices: list, pitch: float, speed: float) -> list:
        voices = normalize_blend(voices)
        known = set(self.available_voices())
        unknown = [n for n, _ in voices if known and n not in known]
        if unknown:
            raise ValueError(f"Unknown voice(s): {', '.join(unknown)}")
        if not 0 <= float(pitch) <= 4:
            raise ValueError("Pitch must be between 0 and +4 semitones")
        if not 0.9 <= float(speed) <= 1.3:
            raise ValueError("Speed must be between 0.9 and 1.3")
        return voices

    def use(self, voices: list, pitch: float, speed: float) -> str:
        voices = self.validate_choice(voices, pitch, speed)
        save_voice_choice(voices, pitch, speed, self.cfg.path)
        fresh = load_config(self.cfg.path)
        with self._lock:
            self.cfg.data = fresh.data  # shared with Rika when the lab runs inside the tray app
        return f"Saved! Rika now uses {blend_label(voices)}, pitch {float(pitch):+g}, speed {float(speed):g}."


# ============================================================================ web page

def build_html(state: dict, api: bool, token: str = "") -> str:
    template = (paths.assets_dir() / "voice_lab.html").read_text(encoding="utf-8")
    boot = json.dumps({"api": api, "token": token, "state": state}).replace("</", "<\\/")
    return template.replace("/*__BOOT__*/null", boot)


class LabServer:
    """Serves the lab page on 127.0.0.1 with a per-session token for the buttons that change things."""

    def __init__(self, lab: VoiceLab, on_use: Callable[[str], None] | None = None):
        self.lab = lab
        self.on_use = on_use
        self.token = secrets.token_urlsafe(16)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                log.debug("voice lab http: " + fmt, *args)

            def _host_ok(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                return host in ("127.0.0.1", "localhost")

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj: dict) -> None:
                self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, b"forbidden", "text/plain")
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    html = build_html(server.lab.state(), api=True, token=server.token)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/api/state":
                    return self._json(200, server.lab.state())
                m = re.fullmatch(r"/clips/([\w.+\-]+\.wav)", path)
                if m and (server.lab.clips_dir / m.group(1)).is_file():
                    return self._send(200, (server.lab.clips_dir / m.group(1)).read_bytes(), "audio/wav")
                self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if not self._host_ok() or self.headers.get("X-Rika-Token") != server.token:
                    return self._json(403, {"ok": False, "error": "Forbidden"})
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if self.path == "/api/blend":
                        voices = parse_blend(str(body.get("blend", "")))
                        server.lab.validate_choice(voices, 0, 1.0)
                        gid = server.lab.queue(voices, "blend")
                        return self._json(200, {"ok": True, "id": gid})
                    if self.path == "/api/use":
                        msg = server.lab.use(body["voices"], float(body["pitch"]), float(body["speed"]))
                        if server.on_use:
                            server.on_use(msg)
                        return self._json(200, {"ok": True, "message": msg})
                    return self._json(404, {"ok": False, "error": "Unknown action"})
                except (ValueError, KeyError, TypeError) as exc:
                    return self._json(400, {"ok": False, "error": str(exc)})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="voice-lab-http", daemon=True)

    def start(self) -> None:
        self._thread.start()
        log.info("Voice lab at %s", self.url)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ============================================================================ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="voice_lab", description="Pick Rika's voice (Kokoro built-in voices only).")
    ap.add_argument("--quick", action="store_true", help="only pitch +0 and speed 1.0 (much faster)")
    ap.add_argument("--voices", help="comma-separated voices to render (default: all English female voices)")
    ap.add_argument("--blend", action="append", default=[], help='try a mix, e.g. "af_bella:0.6,af_nicole:0.4"')
    ap.add_argument("--use", metavar="BLEND", help='save this voice to config.json, e.g. "af_sky" or "af_bella:0.6,af_nicole:0.4"')
    ap.add_argument("--pitch", type=float, default=None, help="with --use: pitch in semitones (0 to 4)")
    ap.add_argument("--speed", type=float, default=None, help="with --use: speed (0.9 to 1.3)")
    ap.add_argument("--no-serve", action="store_true", help="just render the files and index.html, then exit")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--out", type=Path, help="output folder (default: voice_lab_output)")
    ap.add_argument("--config", type=Path, help="config.json to read/update")
    args = ap.parse_args(argv)

    from rika.logsetup import setup_logging

    setup_logging()
    cfg = load_config(args.config)

    if args.use:
        voices = parse_blend(args.use)
        pitch = cfg.tts["pitch_semitones"] if args.pitch is None else args.pitch
        speed = cfg.tts["speed"] if args.speed is None else args.speed
        lab = VoiceLab(cfg, args.out)
        try:
            lab.prepare()  # to check the voice names
        except Exception as exc:
            print(f"(could not load Kokoro to check names: {exc})")
        try:
            print(lab.use(voices, pitch, speed))
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        print("If Rika is running: tray menu > Settings > Reload settings.")
        return 0

    lab = VoiceLab(cfg, args.out, quick=args.quick)
    print(f"Voice lab output: {lab.out_dir}")
    last = [""]

    def status(msg):
        if msg and msg != last[0]:
            last[0] = msg
            print(f"\r{msg}            ", end="", flush=True)

    try:
        lab.prepare(status)
    except Exception as exc:
        print(f"\nERROR: could not load Kokoro: {exc}")
        return 1
    print()
    only = [v.strip() for v in args.voices.split(",")] if args.voices else None
    for spec in args.blend:
        lab.queue(parse_blend(spec), "blend")
    lab.queue_all(only)

    server = None
    if not args.no_serve:
        server = LabServer(lab, on_use=lambda m: print(f"\n{m}\nIf Rika is running: tray menu > Settings > Reload settings."))
        server.start()
        print(f"Open {server.url}  (Ctrl+C to quit)")
        if not args.no_browser:
            webbrowser.open(server.url)

    def progress(s):
        print(f"\r{s['message']}: {s['done']}/{s['total']} clips   ", end="", flush=True)

    try:
        lab.wait(progress)
        lab.save()
        print(f"\nDone! Static page: {lab.out_dir / 'index.html'}")
        if server:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        lab.save()
        if server:
            server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
