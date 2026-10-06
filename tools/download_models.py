"""Download the offline speech models into ./models (run once before building).

    python tools\\download_models.py               # vosk small-en + whisper base.en
    python tools\\download_models.py --whisper small.en
"""
from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
VOSK_NAME = "vosk-model-small-en-us-0.15"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_NAME}.zip"


def download_vosk() -> None:
    target = MODELS / VOSK_NAME
    if (target / "am").exists():
        print(f"[ok] {target} already exists")
        return
    print(f"Downloading {VOSK_URL} ...")
    with urllib.request.urlopen(VOSK_URL) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(MODELS)
    print(f"[ok] Vosk model -> {target}")


def download_whisper(size: str) -> None:
    from huggingface_hub import snapshot_download

    target = MODELS / f"faster-whisper-{size}"
    if (target / "model.bin").exists():
        print(f"[ok] {target} already exists")
        return
    repo = f"Systran/faster-whisper-{size}"
    print(f"Downloading {repo} ...")
    snapshot_download(repo, local_dir=str(target), allow_patterns=["*.bin", "*.json", "*.txt"])
    print(f"[ok] Whisper model -> {target}")


def download_minilm() -> None:
    """Sentence-meaning model (all-MiniLM-L6-v2, ONNX int8, ~23 MB) for understanding paraphrases."""
    import shutil

    from huggingface_hub import hf_hub_download

    target = MODELS / "minilm-l6-v2"
    if (target / "model_quantized.onnx").exists() and (target / "tokenizer.json").exists():
        print(f"[ok] {target} already exists")
        return
    target.mkdir(parents=True, exist_ok=True)
    for remote, local in (("onnx/model_quantized.onnx", "model_quantized.onnx"), ("tokenizer.json", "tokenizer.json")):
        shutil.copy(hf_hub_download("Xenova/all-MiniLM-L6-v2", remote), target / local)
    print(f"[ok] Sentence model -> {target}")


SUMATRA_VERSION = "3.5.2"
SUMATRA_URL = f"https://www.sumatrapdfreader.org/dl/rel/{SUMATRA_VERSION}/SumatraPDF-{SUMATRA_VERSION}-64.zip"


def download_sumatra() -> None:
    """Portable SumatraPDF (GPLv3, free to redistribute) bundled for silent PDF printing."""
    target = ROOT / "vendor" / "SumatraPDF.exe"
    if target.exists():
        print(f"[ok] {target} already exists")
        return
    print(f"Downloading {SUMATRA_URL} ...")
    try:
        req = urllib.request.Request(SUMATRA_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            exe = next(n for n in zf.namelist() if n.lower().endswith(".exe"))
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(zf.read(exe))
        print(f"[ok] SumatraPDF -> {target}")
    except Exception as exc:
        print(f"[warn] Could not download SumatraPDF ({exc}). Install it from https://www.sumatrapdfreader.org "
              "or put SumatraPDF.exe in the vendor folder; Rika also finds an installed copy.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--whisper", default="base.en", help="base.en (fast) or small.en / small (more accurate)")
    ap.add_argument("--no-sumatra", action="store_true", help="don't download portable SumatraPDF")
    args = ap.parse_args()
    MODELS.mkdir(exist_ok=True)
    download_vosk()
    download_whisper(args.whisper)
    download_minilm()
    if not args.no_sumatra:
        download_sumatra()
    return 0


if __name__ == "__main__":
    sys.exit(main())
