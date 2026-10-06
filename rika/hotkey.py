"""Global hotkey (default Ctrl+Alt+R) using Win32 RegisterHotKey - no extra packages."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}


def parse_hotkey(spec: str) -> tuple[int, int]:
    *mods, key = spec.lower().replace(" ", "").split("+")
    flags = MOD_NOREPEAT
    for m in mods:
        flags |= _MODS[m]
    return flags, ord(key.upper())


class HotkeyListener(threading.Thread):
    def __init__(self, spec: str, callback: Callable[[], None]):
        super().__init__(name="hotkey", daemon=True)
        self.spec = spec
        self.callback = callback
        self._tid: int | None = None
        self.registered = threading.Event()

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._tid = threading.get_native_id()
        mods, vk = parse_hotkey(self.spec)
        if not user32.RegisterHotKey(None, 1, mods, vk):
            log.error("Could not register hotkey %s (already used by another app?)", self.spec)
            return
        self.registered.set()
        log.info("Hotkey %s registered", self.spec)
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:
                        log.exception("hotkey callback failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
