"""Make Rika say a sentence with the configured voice and report which engine spoke.

    python tools\say.py "Hello! I opened your email and found the PDF. Sending it to the printer now."
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rika import tts  # noqa: E402
from rika.config import load_config  # noqa: E402
from rika.logsetup import setup_logging  # noqa: E402

setup_logging()
text = " ".join(sys.argv[1:]) or "Hello! I opened your email and found the PDF. Sending it to the printer now."
cfg = load_config()
sp = tts.Speaker(cfg.tts)
ok = sp.load_primary()
print(f"main voice '{sp.primary.name}': {'ready' if ok else 'NOT available - ' + sp.last_error}")
played = []
real_play = tts.play_file


def play(path, should_stop=None):
    played.append(Path(path).name)
    t = time.monotonic()
    real_play(path, should_stop)
    print(f"  played {Path(path).name.split('_')[0]} clip ({time.monotonic() - t:.1f}s)")


tts.play_file = play
t0 = time.monotonic()
sp.say(text)
engines = sorted({p.split("_")[0] for p in played})
print(f"Rika said: {text!r}\nvoice used: {', '.join(engines) or 'none'} | total {time.monotonic() - t0:.1f}s")
sys.exit(0 if played else 1)
