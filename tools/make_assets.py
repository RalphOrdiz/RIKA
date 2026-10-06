"""Generate rika/assets/chime.wav and rika/assets/rika.ico (run once; outputs are committed)."""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ASSETS = ROOT / "rika" / "assets"


def chime(path: Path, rate: int = 44100) -> None:
    """A short, bright two-note 'ding-ding' with a soft sparkle."""
    def note(freq: float, dur: float, start: float, total: float) -> np.ndarray:
        t = np.arange(int(total * rate)) / rate
        env = np.where(t >= start, np.exp(-(t - start) * 9.0), 0.0)
        tone = np.sin(2 * np.pi * freq * (t - start)) + 0.35 * np.sin(2 * np.pi * freq * 2 * (t - start))
        return env * tone * (t < start + dur)

    total = 0.45
    sig = note(1318.5, 0.35, 0.0, total) + note(1760.0, 0.35, 0.11, total)  # E6 -> A6
    attack = np.minimum(1.0, np.arange(len(sig)) / (0.004 * rate))
    sig = sig * attack
    sig = sig / np.max(np.abs(sig)) * 0.5
    pcm = (sig * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def icon(path: Path) -> None:
    from rika.icons import make_icon

    img = make_icon("idle", 256)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(path.with_suffix(".png"))


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    chime(ASSETS / "chime.wav")
    icon(ASSETS / "rika.ico")
    print("Wrote", *sorted(p.name for p in ASSETS.iterdir()))
