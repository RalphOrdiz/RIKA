"""Open an account's Gmail in the right Chrome profile (no passwords involved)."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote

from rika.errors import ChromeNotFound

log = logging.getLogger(__name__)


def find_chrome(configured: str = "") -> Path:
    if configured and Path(os.path.expandvars(configured)).is_file():
        return Path(os.path.expandvars(configured))
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                val, _ = winreg.QueryValueEx(key, "")
                p = Path(val.strip('"'))
                if p.is_file():
                    return p
        except OSError:
            pass
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ):
        p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and p.is_file():
            return p
    found = shutil.which("chrome.exe")
    if found:
        return Path(found)
    raise ChromeNotFound("chrome.exe not found (registry App Paths, Program Files, LocalAppData)")


def chrome_user_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def list_chrome_profiles() -> list[dict]:
    """Profiles from Chrome's 'Local State' -> profile.info_cache."""
    local_state = chrome_user_data_dir() / "Local State"
    if not local_state.exists():
        return []
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read Chrome Local State: %s", exc)
        return []
    cache = data.get("profile", {}).get("info_cache", {})
    profiles = [
        {
            "dir": folder,
            "name": info.get("name", ""),
            "email": info.get("user_name", ""),
            "full_name": info.get("gaia_name", ""),
        }
        for folder, info in cache.items()
    ]
    profiles.sort(key=lambda p: (p["dir"] != "Default", p["dir"]))
    return profiles


def format_profiles(profiles: list[dict]) -> str:
    if not profiles:
        return "No Chrome profiles found (is Google Chrome installed and opened at least once?)"
    lines = [f"{'Folder (chrome_profile_dir)':30} {'Profile name':25} Signed-in Google account", "-" * 85]
    for p in profiles:
        lines.append(f"{p['dir']:30} {p['name'][:25]:25} {p['email'] or '(not signed in)'}")
    return "\n".join(lines)


def resolve_profile_dir(configured: str, email: str) -> str:
    """Use the configured folder if it exists; otherwise find the profile signed in as ``email``."""
    profiles = list_chrome_profiles()
    dirs = {p["dir"] for p in profiles}
    if configured and (configured in dirs or (chrome_user_data_dir() / configured).is_dir()):
        return configured
    for p in profiles:
        if p["email"].lower() == email.lower():
            if configured:
                log.warning("Chrome profile '%s' not found; using '%s' (signed in as %s)", configured, p["dir"], email)
            return p["dir"]
    if configured:
        log.warning("Chrome profile folder '%s' not found and no profile is signed in as %s", configured, email)
    return configured or "Default"


def gmail_url(email: str) -> str:
    return f"https://mail.google.com/mail/u/?authuser={quote(email)}"


def open_gmail(
    email: str, profile_dir: str, chrome_cfg: str = "", dry_run: bool = False, maximize: bool = True
) -> list[str]:
    chrome = find_chrome(chrome_cfg)
    profile = resolve_profile_dir(profile_dir, email)
    cmd = [str(chrome), f"--profile-directory={profile}"]
    if maximize:
        cmd.append("--start-maximized")  # only honoured if Chrome wasn't running; see _remember_new_windows
    cmd.append(gmail_url(email))
    log.info("Opening Gmail: %s", subprocess.list2cmdline(cmd))
    if not dry_run:
        before = {h for h, _ in chrome_windows()}
        subprocess.Popen(cmd, close_fds=True, creationflags=subprocess.DETACHED_PROCESS)
        threading.Thread(
            target=_remember_new_windows, args=(email, before), kwargs={"maximize": maximize},
            name="gmail-window", daemon=True,
        ).start()
    return cmd


SW_MAXIMIZE = 3


def is_maximized(hwnd: int) -> bool:
    import ctypes

    return bool(ctypes.windll.user32.IsZoomed(hwnd))


def maximize_window(hwnd: int) -> None:
    import ctypes

    if not is_maximized(hwnd):
        if ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE) or is_maximized(hwnd):
            log.info("Maximized Chrome window %s", hwnd)
        else:
            log.warning("Could not maximize Chrome window %s", hwnd)


# ---------------------------------------------------------------------------- closing
#
# All Chrome profiles share one chrome.exe, so Rika never kills Chrome. She only sends
# WM_CLOSE (like clicking X) to windows that belong to the account:
#   - windows that appeared right after she opened that account's Gmail, and
#   - windows whose title shows that Gmail (Gmail titles contain the address:
#     "Inbox (3) - rjoprintingservice@gmail.com - Gmail - Google Chrome").

WM_CLOSE = 0x0010
_opened_windows: dict[str, set[int]] = {}
_opened_lock = threading.Lock()


def _process_name(pid: int) -> str:
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def chrome_windows() -> list[tuple[int, str]]:
    """Visible top-level Chrome browser windows: [(hwnd, title)]."""
    import win32gui
    import win32process

    found: list[tuple[int, str]] = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if _process_name(pid) == "chrome.exe":
            found.append((hwnd, title))
        return True

    win32gui.EnumWindows(visit, None)
    return found


def _remember_new_windows(email: str, before: set[int], wait_s: float = 15.0, maximize: bool = False) -> None:
    """After opening Gmail, note which Chrome window(s) belong to this account (and maximize them once)."""
    end = time.monotonic() + wait_s
    handled: set[int] = set()
    while time.monotonic() < end:
        for hwnd, title in chrome_windows():
            t = title.lower()
            looks_like_gmail = any(k in t for k in ("gmail", "mail.google", "google accounts", "sign in"))
            if email.lower() in t or (hwnd not in before and looks_like_gmail):
                with _opened_lock:
                    if hwnd not in _opened_windows.setdefault(email.lower(), set()):
                        log.info("Remembering Chrome window %s for %s (%r)", hwnd, email, title)
                    _opened_windows[email.lower()].add(hwnd)
                if maximize and hwnd not in handled:
                    handled.add(hwnd)  # only once, so you can still un-maximize it yourself
                    maximize_window(hwnd)
        time.sleep(0.5)


def account_windows(email: str) -> list[tuple[int, str]]:
    current = chrome_windows()
    with _opened_lock:
        remembered = _opened_windows.get(email.lower(), set())
        remembered &= {h for h, _ in current}  # forget windows that were closed by hand
    return [(h, t) for h, t in current if h in remembered or email.lower() in t.lower()]


def close_gmail(email: str, dry_run: bool = False) -> int:
    """Close this account's Chrome window(s). Returns how many were closed."""
    import win32gui

    targets = account_windows(email)
    for hwnd, title in targets:
        log.info("Closing Chrome window %s for %s: %r%s", hwnd, email, title, " (dry run)" if dry_run else "")
        if not dry_run:
            win32gui.PostMessage(hwnd, WM_CLOSE, 0, 0)
    with _opened_lock:
        if not dry_run:
            _opened_windows.pop(email.lower(), None)
    return len(targets)
