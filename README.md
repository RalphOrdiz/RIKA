# RIKA

Rika is a Windows voice assistant for RJO Printing Service. Say **"Rika"** (or press **Ctrl+Alt+R**), then ask her to open the shop Gmail, find a printable file in it, and print it. She always asks "Yes or no?" before printing.

- **Offline listening:** wake word (Vosk), speech to text (faster-whisper), and meaning-based command matching (MiniLM).
- **Voice:** ElevenLabs, Kokoro (offline) or Windows voices, with automatic fallback.
- **Gmail:** read-only access (`gmail.readonly`) to find PDF/Word attachments.
- **Printing:** SumatraPDF for PDFs, MS Word or LibreOffice for Word files.

See **[SETUP.md](SETUP.md)** for setup (Google Cloud, Chrome profile, printer), the Voice Lab, and building the `.exe`.

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python tools\download_models.py
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m rika --text --dry-run
build.bat
```

Not included in this repo: speech models, `credentials.json`, `token.json` and `.env` (API keys). See `.env.example`.
