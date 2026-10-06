"""Rika entry point.

    Rika.exe                       tray app (wake word + hotkey)
    Rika.exe --text                type commands instead of speaking
    Rika.exe --text --speak        ...and hear Rika's replies
    Rika.exe --console             voice mode without the tray (debug)
    Rika.exe --auth                connect Gmail (opens Google sign-in)
    Rika.exe --list-chrome-profiles | --list-printers | --test-voice
    add --dry-run to anything to skip real printing / Chrome launching
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from rika import __version__, paths
from rika.logsetup import setup_logging

log = logging.getLogger("rika")


def _ensure_console() -> None:
    """A windowed (no-console) exe has no stdin/stdout; give it a console for CLI modes."""
    if sys.stdout is not None and sys.stdin is not None:
        if not sys.stdin.isatty():
            try:  # piped input (e.g. from PowerShell) is UTF-8, often with a BOM
                sys.stdin.reconfigure(encoding="utf-8-sig")
            except (AttributeError, ValueError):
                pass
        return
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p

    def inherited(std_id: int, mode: str):
        """Use redirected pipes/files if the parent gave us some (e.g. Rika.exe --text < cmds.txt)."""
        handle = kernel32.GetStdHandle(std_id)
        if not handle or handle == ctypes.c_void_p(-1).value or kernel32.GetFileType(ctypes.c_void_p(handle)) == 0:
            return None
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if mode == "r" else os.O_WRONLY)
            return open(fd, mode, encoding="utf-8-sig" if mode == "r" else "utf-8", buffering=1 if mode == "w" else -1)
        except OSError:
            return None

    sys.stdin = sys.stdin or inherited(-10, "r")
    sys.stdout = sys.stdout or inherited(-11, "w")
    sys.stderr = sys.stderr or inherited(-12, "w") or sys.stdout
    if sys.stdin is None or sys.stdout is None:
        if not kernel32.AttachConsole(-1):  # parent console (cmd/PowerShell) ...
            kernel32.AllocConsole()  # ... or a fresh window
        sys.stdout = sys.stdout or open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stderr or sys.stdout
        sys.stdin = sys.stdin or open("CONIN$", "r", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="Rika", description="Rika - voice assistant for RJO Printing Service")
    p.add_argument("--text", action="store_true", help="type commands instead of speaking")
    p.add_argument("--speak", action="store_true", help="with --text: also speak replies out loud")
    p.add_argument("--console", action="store_true", help="voice mode in a console window (no tray)")
    p.add_argument("--dry-run", action="store_true", help="don't actually print or open Chrome")
    p.add_argument("--auth", action="store_true", help="connect the Gmail account (Google sign-in)")
    p.add_argument("--list-chrome-profiles", action="store_true", help="show Chrome profile folders")
    p.add_argument("--list-printers", action="store_true", help="show installed printers")
    p.add_argument("--list-mics", action="store_true", help="show microphones")
    p.add_argument("--test-voice", action="store_true", help="say a few lines with the configured voice")
    p.add_argument("--transcribe", type=Path, metavar="WAV", help="transcribe a 16-bit WAV file and show the intent")
    p.add_argument("--voice-lab", action="store_true", help="open the Voice Lab (see: Rika.exe --voice-lab --help)")
    p.add_argument("--config", type=Path, help="use this config.json instead of %%APPDATA%%\\Rika\\config.json")
    p.add_argument("-v", "--verbose", action="store_true", help="show debug logs in the console")
    p.add_argument("--version", action="version", version=f"Rika {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--voice-lab" in argv:  # Rika.exe --voice-lab [voice lab options]
        _ensure_console()
        from rika.voice_lab import main as lab_main

        argv.remove("--voice-lab")
        return lab_main(argv)
    args = build_parser().parse_args(argv)
    gui = not any(
        (args.text, args.console, args.auth, args.list_chrome_profiles, args.list_printers, args.list_mics,
         args.test_voice, args.transcribe)
    )
    if not gui:
        _ensure_console()
    else:
        # windowed exe: some libraries write progress bars to stderr - give them somewhere to go
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
    setup_logging(verbose=args.verbose)
    log.info("Rika %s starting (frozen=%s, args=%s)", __version__, paths.is_frozen(), argv or sys.argv[1:])

    from rika.config import load_config
    from rika.errors import ConfigError

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        if gui:
            _fatal_box(f"Rika can't start: {exc}\n\nFix config.json and try again:\n{args.config or paths.config_path()}")
        else:
            print(f"ERROR: {exc}")
        return 2

    if args.list_chrome_profiles:
        from rika.actions.gmail_open import format_profiles, list_chrome_profiles

        print(format_profiles(list_chrome_profiles()))
        print('\nPut the folder name in config.json -> accounts -> <name> -> "chrome_profile_dir".')
        return 0
    if args.list_printers:
        from rika.actions.printer import default_printer, list_printers

        default = default_printer()
        for name in list_printers():
            print(f"{'*' if name == default else ' '} {name}")
        print('\n* = Windows default. Put the exact name in config.json -> "printer".')
        return 0
    if args.list_mics:
        from rika.audio import list_input_devices

        print(list_input_devices())
        return 0
    if args.auth:
        return _run_auth(cfg)
    if args.test_voice:
        return _test_voice(cfg)
    if args.transcribe:
        return _transcribe_file(cfg, args.transcribe)

    from rika.actions.printer import cleanup_downloads

    cleanup_downloads(cfg["download_retention_days"])

    if args.text:
        return run_text_mode(cfg, speak=args.speak, dry_run=args.dry_run)
    if args.console:
        from rika.app import run_console

        return run_console(cfg, dry_run=args.dry_run)

    return run_tray(cfg, dry_run=args.dry_run, config_override=args.config)


def _fatal_box(message: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, "Rika", 0x10)


def _run_auth(cfg) -> int:
    from rika import gmail_auth

    acct = cfg.print_account
    print(f"Connecting Gmail for {acct.email} (read-only access)...")
    print(f"credentials.json: {paths.credentials_path()}")
    try:
        gmail_auth.run_auth_flow(login_hint=acct.email)
    except Exception as exc:
        print(f"ERROR: {exc}")
        log.exception("Gmail auth failed")
        return 1
    print(f"Done! Token saved to {paths.token_path()}")
    return 0


def _transcribe_file(cfg, path: Path) -> int:
    import wave

    import numpy as np

    from rika.intents import account_terms, parse
    from rika.stt import Transcriber, build_prompt

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            print("ERROR: need a 16-bit PCM WAV file")
            return 1
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, channels)[:, 0]
    audio = pcm.astype(np.float32) / 32768.0
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    tr = Transcriber(cfg.stt, build_prompt(cfg.accounts))
    tr.load()
    text = tr.transcribe(audio)
    intent = parse(text, account_terms=account_terms(cfg.accounts))
    print(f"Heard:  {text!r}\nIntent: {intent.name} {intent.slots}")
    return 0


def _load_voice(speaker) -> None:
    """Load the main voice before talking (shows download progress on first run)."""
    last = [""]

    def status(msg):
        if msg and msg != last[0]:
            last[0] = msg
            print(f"\r[voice] {msg}   ", end="", flush=True)

    ok = speaker.load_primary(status)
    if last[0]:
        print()
    if not ok:
        print(f"[voice] '{speaker.primary.name}' unavailable - using {[e.name for e in speaker.chain[1:]]} (see rika.log)")


def _test_voice(cfg) -> int:
    from rika.lines import line
    from rika.tts import Speaker

    speaker = Speaker(cfg.tts)
    _load_voice(speaker)
    for key in ("ready", "wake", "printing", "done", "not_understood"):
        text = line(key)
        print(f"Rika: {text}")
        speaker.say(text)
    speaker.close()
    return 0


# ---------------------------------------------------------------------------- tray

def _single_instance() -> bool:
    import ctypes

    ERROR_ALREADY_EXISTS = 183
    _single_instance.handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\RikaAssistantSingleton")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _open_in_notepad(path: Path) -> None:
    import subprocess

    subprocess.Popen(["notepad.exe", str(path)])


class TrayApp:
    """System-tray UI. The tray runs on the main thread; all real work happens on worker threads."""

    def __init__(self, cfg, dry_run: bool = False, config_override: Path | None = None):
        import pystray

        from rika.app import Controller
        from rika.icons import make_icon

        self.cfg = cfg
        self.config_override = config_override
        self.make_icon = make_icon
        self.icon = pystray.Icon("Rika", make_icon("starting"), "Rika - starting...", menu=self._menu())
        self.ctl = Controller(cfg, dry_run=dry_run, notify=self.notify)
        self.ctl.state_listeners.append(self._on_state)
        self.ctl.status_listeners.append(lambda s: self._on_state(self.ctl.state))
        self.lab_server = None

    # -------------------------------------------------------------- menu
    def _menu(self):
        from pystray import Menu, MenuItem as Item

        from rika import autostart

        return Menu(
            Item("Listen now", lambda: self.ctl.trigger("menu"), default=True),
            Item("Pause listening", self._toggle_pause, checked=lambda item: self.ctl.paused.is_set()),
            Item("Voice Lab", self._open_voice_lab),
            Menu.SEPARATOR,
            Item("Open logs", Menu(
                Item("Rika log", lambda: _open_in_notepad(paths.log_path())),
                Item("Print log (CSV)", self._open_print_log),
                Item("Downloads folder", lambda: os.startfile(paths.downloads_dir())),
            )),
            Item("Settings", Menu(
                Item("Edit settings (config.json)", lambda: _open_in_notepad(self.cfg.path or paths.config_path())),
                Item("Reload settings", self._reload),
                Menu.SEPARATOR,
                Item("Connect Gmail...", self._connect_gmail),
                Item("Show Chrome profiles, printers, mics", self._show_setup_info),
                Item("Open Rika folder", lambda: os.startfile(paths.data_dir())),
                Menu.SEPARATOR,
                Item(
                    "Start with Windows",
                    lambda: autostart.set_enabled(not autostart.is_enabled()),
                    checked=lambda item: autostart.is_enabled(),
                ),
            )),
            Menu.SEPARATOR,
            Item("Quit", self._quit),
        )

    def _toggle_pause(self) -> None:
        from rika.lines import line

        paused = not self.ctl.paused.is_set()
        self.ctl.set_paused(paused)
        self.notify(line("paused") if paused else line("resumed"))

    def _open_print_log(self) -> None:
        p = paths.print_log_path()
        if not p.exists():
            self.notify("Nothing has been printed yet~")
            return
        os.startfile(p)

    def _reload(self) -> None:
        from rika.config import load_config
        from rika.errors import ConfigError

        try:
            new = load_config(self.config_override)
        except ConfigError as exc:
            self.notify(f"Settings not reloaded: {exc}")
            return
        self.ctl.reload(new)  # updates self.cfg in place (shared object)
        msg = "Settings reloaded~"
        if new.problems:
            msg += f" ({len(new.problems)} problem(s) - see the log)"
        self.notify(msg)

    def _connect_gmail(self) -> None:
        import threading

        from rika import gmail_auth

        def work() -> None:
            acct = self.cfg.print_account
            try:
                self.notify("Opening Google sign-in in your browser~")
                gmail_auth.run_auth_flow(login_hint=acct.email if acct else None)
                self.ctl.assistant._mailbox = None
                self.notify("Gmail connected! Yay~")
            except Exception as exc:
                log.exception("Connect Gmail failed")
                self.notify(f"Gmail connection failed: {exc}")

        threading.Thread(target=work, name="gmail-auth", daemon=True).start()

    def _show_setup_info(self) -> None:
        from rika.actions.gmail_open import format_profiles, list_chrome_profiles
        from rika.actions.printer import default_printer, list_printers
        from rika.audio import list_input_devices

        default = default_printer()
        text = [
            "RIKA SETUP INFO", "=" * 60, "",
            "CHROME PROFILES  ->  config.json: accounts.<name>.chrome_profile_dir", "",
            format_profiles(list_chrome_profiles()), "",
            'PRINTERS  ->  config.json: "printer"   (* = Windows default)', "",
            *[f"{'*' if p == default else ' '} {p}" for p in list_printers()], "",
            'MICROPHONES  ->  config.json: "mic_device"', "",
            list_input_devices(), "",
            f"Config file: {self.cfg.path or paths.config_path()}",
            f"Gmail credentials.json: {paths.credentials_path()}",
        ]
        out = paths.data_dir() / "setup_info.txt"
        out.write_text("\n".join(text), encoding="utf-8")
        _open_in_notepad(out)

    def _open_voice_lab(self) -> None:
        """Start the voice lab inside Rika (first time renders the clips in the background) and open it."""
        import threading
        import webbrowser

        if self.lab_server is not None:
            webbrowser.open(self.lab_server.url)
            return

        def work() -> None:
            from rika.config import load_config
            from rika.voice_lab import LabServer, VoiceLab

            try:
                lab = VoiceLab(load_config(self.config_override))  # own copy, so Reload sees the change
                lab.prepare()
                lab.queue_all()  # skips clips that already exist
                self.lab_server = LabServer(lab, on_use=lambda msg: (self._reload(), self.notify(msg)))
                self.lab_server.start()
                webbrowser.open(self.lab_server.url)
                self.notify("Voice Lab is open in your browser~ Clips keep appearing while I render them.")
            except Exception as exc:
                log.exception("Voice lab failed")
                self.notify(f"Voice Lab couldn't start: {exc}")

        self.notify("Opening the Voice Lab...")
        threading.Thread(target=work, name="voice-lab-start", daemon=True).start()

    def _quit(self) -> None:
        if self.lab_server is not None:
            self.lab_server.stop()
        self.ctl.shutdown()
        self.icon.stop()

    # -------------------------------------------------------------- state / notify
    def _on_state(self, state: str) -> None:
        labels = {
            "idle": "listening for \"Rika\"", "paused": "paused (hotkey still works)", "listening": "listening...",
            "thinking": "thinking...", "working": "working...", "speaking": "talking", "error": "microphone problem",
        }
        title = f"Rika - {labels.get(state, state)}"
        if self.ctl.voice_status:  # e.g. "Downloading voice model 42%" on first run
            title += f" | {self.ctl.voice_status}"
        try:
            self.icon.icon = self.make_icon(state)
            self.icon.title = title[:127]
        except Exception:
            log.debug("tray update failed", exc_info=True)

    def notify(self, message: str) -> None:
        log.info("Notify: %s", message)
        try:
            if self.icon.HAS_NOTIFICATION:
                self.icon.notify(message, "Rika")
        except Exception:
            log.debug("notify failed", exc_info=True)

    # -------------------------------------------------------------- run
    def run(self) -> int:
        def setup(icon) -> None:
            icon.visible = True
            self.ctl.start()
            if self.cfg.problems:
                self.notify("Some settings looked wrong, so I used defaults. See Open logs > Rika log.")

        self.icon.run(setup=setup)
        return 0


def run_tray(cfg, dry_run: bool = False, config_override: Path | None = None) -> int:
    if not _single_instance():
        log.info("Rika is already running; exiting")
        return 0
    return TrayApp(cfg, dry_run=dry_run, config_override=config_override).run()


# ---------------------------------------------------------------------------- text mode

def run_text_mode(cfg, speak: bool = False, dry_run: bool = False) -> int:
    from rika.assistant import Assistant
    from rika.interaction import TextIO

    speaker = None
    if speak:
        from rika.tts import Speaker

        speaker = Speaker(cfg.tts)
        _load_voice(speaker)
    io = TextIO(speaker=speaker)
    from rika.semantic import SemanticMatcher

    semantic = SemanticMatcher()
    semantic.load()
    asst = Assistant(cfg, io, dry_run=dry_run, semantic=semantic)

    print(f"Rika {__version__} - text mode{' (dry run: nothing will really print)' if dry_run else ''}")
    print('Try: "print document1.pdf", "open rjo gmail", "what can you do?". Type "quit" to exit.\n')
    if cfg.problems:
        print("Config warnings:\n  " + "\n  ".join(cfg.problems) + "\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit", "bye"):
            break
        io.cancel_event.clear()
        try:
            asst.handle(text)
        except KeyboardInterrupt:
            print("\n(cancelled)")
    if speaker:
        speaker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
