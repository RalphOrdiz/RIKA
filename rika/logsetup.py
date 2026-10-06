from __future__ import annotations

import logging
import logging.handlers
import sys

from rika import paths


def setup_logging(verbose: bool = False) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        paths.log_path(), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        console.setLevel(logging.DEBUG if verbose else logging.WARNING)
        root.addHandler(console)

    for noisy in ("googleapiclient.discovery_cache", "urllib3", "comtypes", "PIL", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
