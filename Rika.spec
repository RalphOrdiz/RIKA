# PyInstaller spec for Rika (one-folder build) -> dist\Rika\Rika.exe
# Build with build.bat (which also downloads models and runs the tests).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)
MODELS = ROOT / "models"

datas = [(str(ROOT / "rika" / "assets"), "rika/assets")]

vosk_model = MODELS / "vosk-model-small-en-us-0.15"
if not vosk_model.exists():
    raise SystemExit(f"Missing {vosk_model} - run: python tools\\download_models.py")
datas.append((str(vosk_model), f"models/{vosk_model.name}"))

whisper_models = [p for p in MODELS.glob("faster-whisper-*") if (p / "model.bin").exists()]
if not whisper_models:
    raise SystemExit("Missing models\\faster-whisper-* - run: python tools\\download_models.py")
for wm in whisper_models:
    for f in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt", "preprocessor_config.json"):
        if (wm / f).exists():
            datas.append((str(wm / f), f"models/{wm.name}"))

minilm = MODELS / "minilm-l6-v2"
if not (minilm / "model_quantized.onnx").exists():
    raise SystemExit("Missing models\\minilm-l6-v2 - run: python tools\\download_models.py")
for f in ("model_quantized.onnx", "tokenizer.json"):
    datas.append((str(minilm / f), "models/minilm-l6-v2"))

if (ROOT / "vendor" / "SumatraPDF.exe").exists():
    datas.append((str(ROOT / "vendor" / "SumatraPDF.exe"), "vendor"))

datas += collect_data_files("faster_whisper")
datas += collect_data_files("vosk")
datas += collect_data_files("certifi")
# Kokoro TTS: vocab config, espeak-ng phonemizer DLL + data (the voice model itself downloads on first run)
datas += collect_data_files("kokoro_onnx")
datas += collect_data_files("espeakng_loader")
datas += collect_data_files("phonemizer")
binaries = (
    collect_dynamic_libs("vosk")
    + collect_dynamic_libs("ctranslate2")
    + collect_dynamic_libs("espeakng_loader")
    + collect_dynamic_libs("onnxruntime")
)

hiddenimports = [
    "pystray._win32",
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "comtypes.stream",
    "win32timezone",
    "kokoro_onnx",
    "pedalboard",
    "phonemizer.backend.espeak.espeak",
]

a = Analysis(
    [str(ROOT / "Rika.pyw")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "torch", "pytest", "IPython", "pandas", "scipy"],
    noarchive=False,
)

# googleapiclient ships ~500 discovery documents; Rika only needs Gmail.
a.datas = [
    d for d in a.datas
    if "discovery_cache" not in d[0].replace("\\", "/") or d[0].replace("\\", "/").endswith("gmail.v1.json")
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rika",
    icon=str(ROOT / "rika" / "assets" / "rika.ico"),
    console=False,
    upx=False,
    version=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Rika")
