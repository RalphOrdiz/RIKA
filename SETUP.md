# Rika – Setup Guide

Rika is a voice assistant for **RJO Printing Service**. Say **"Rika"** (or press **Ctrl+Alt+R**), then say a command:

| Say this | Rika does this |
|---|---|
| "Rika, open RJO Gmail" | Opens that Gmail account in the right Chrome profile, maximized (set `"maximize_gmail": false` to keep Chrome's normal size) |
| "Rika, print document one dot PDF" | Finds `document1.pdf` in Gmail, tells you the sender and the page count, asks "Yes or no?", then prints |
| "Rika, could you print report dot docx?" | Same thing, said a different way |
| "Rika, close RJO Gmail" / "Close Chrome" / "Close it" | Closes only the RJO Gmail Chrome window(s); your other Chrome windows stay open |
| "Rika, do you see a printable file in my Gmail?" / "Any files to print?" | Reads out the newest PDF/Word attachments and asks which one to print |
| "Rika, do you see report dot PDF?" | Looks for that file and offers to print it |
| "Rika, is the printer ready?" | Checks the printer |
| "Rika, what are you capable of?" | Tells you what she can do |
| "Rika, stop" / "That's all" / "Bye" | Stops and goes back to sleep |

**You don't need exact words.** Rika understands the *meaning*, so "Is there documents I can print?", "Scan for documents and PDFs", "Did a customer send something?" and "Any files to print?" all work. A small offline language model compares what you said with example phrasings of each command. If she keeps missing something you say often, add your phrasing to `rika/intent_examples.py` under the right command (developers: then rebuild).

You can also chat a little ("Hello", "Who are you?", "How are you?", "Thank you"). If you ask about something outside her job, like the weather, she tells you she only handles Gmail and printing.

**Conversations:** after she answers, Rika keeps listening for about 8 seconds, so you can carry on without saying "Rika" again. For example: *"Any files to print?"*, then *"The second one."*, then *"Yes."* When you stop talking, she says something like ***"Just call my name, I'll be sleeping for a while!"***. That means she's asleep, and you need to say "Rika" again to wake her.

Rika understands **English** commands.

Setup takes about 20 minutes and you only do it once. Go through the steps in order.

---

## 1. What you need

- A Windows 10 or 11 PC with a **microphone** and **speakers**
- **Google Chrome**, already signed in to the shop Gmail (`rjoprintingservice@gmail.com`)
- The **printer** installed in Windows (for example *EPSON L120 Series*)
- **Microsoft Word** for printing `.doc`/`.docx` files (if you don't have Word, install the free **LibreOffice**)
- An internet connection for Gmail, and once for downloading Rika's voice (about 350 MB). After that her voice works offline.

## 2. Install Rika

1. Copy the whole **`Rika`** folder (the one with `Rika.exe` in it) to `C:\Rika`.
   Copy the **whole folder**. `Rika.exe` alone won't run.
2. Double-click **`Rika.exe`**. After a few seconds a **pink "R" icon** appears in the system tray (bottom-right, near the clock; it may be hidden under the **^** arrow), and Rika says *"Hi hi! I'm ready to help!"*.
   **The very first time**, Rika downloads her voice (about 350 MB). Hover over the tray icon to see the progress, for example *"Downloading voice model 42%"*. Until it finishes she talks with a backup voice. Then she prepares all her lines for about 2 minutes (*"Preparing voice lines 12/70"*); after that she answers instantly, even after a restart.
3. On this first run Rika creates her settings file:
   `C:\Users\<you>\AppData\Roaming\Rika\config.json`
   To open it: right-click the tray icon → **Settings → Edit settings (config.json)**.

> **Tip:** to open that folder quickly, press **Win+R**, type `%APPDATA%\Rika` and press Enter.

## 3. Let Rika read Gmail (Google Cloud setup)

To read Gmail, Rika needs a small "app ID" from Google. It's free. Rika only asks for **read-only** access: she can't send, delete or change emails, and she never sees your password.

### 3a. Create a project
1. Go to <https://console.cloud.google.com/> and sign in with **rjoprintingservice@gmail.com**.
2. Accept the terms if Google asks.
3. At the top, click the **project picker** → **New project**. Name it `Rika` → **Create**.
4. Make sure the `Rika` project is selected at the top of the page.

### 3b. Turn on the Gmail API
1. Open <https://console.cloud.google.com/apis/library/gmail.googleapis.com>
2. Click **Enable**.

### 3c. Set up the consent screen ("Testing" mode)
1. Open <https://console.cloud.google.com/auth/overview> (in the menu this is called **Google Auth Platform**, or **OAuth consent screen** on older screens) and click **Get started**.
2. **App name:** `Rika`. **User support email:** your Gmail. Click **Next**.
3. **Audience:** choose **External**. Click **Next**.
4. **Contact information:** your Gmail. Click **Next**, tick the agreement, then **Create**.
5. Go to **Audience** in the left menu. Under **Test users**, click **Add users**, type `rjoprintingservice@gmail.com` and click **Save**.
   *(The app stays in "Testing" mode. That's fine: only the test users you add can use it.)*

### 3d. Download credentials.json
1. In the left menu, go to **Clients** (on older screens: **Credentials → Create credentials → OAuth client ID**).
2. Click **Create client**. For **Application type**, choose **Desktop app**. Name it `Rika desktop`, then click **Create**.
3. Click **Download JSON**.
4. Rename the downloaded file to exactly **`credentials.json`**.
5. Move it into **`%APPDATA%\Rika\`** (the same folder as `config.json`).

### 3e. Connect Gmail
1. Right-click the tray icon → **Settings → Connect Gmail...**
2. Your browser opens Google's sign-in page. Choose **rjoprintingservice@gmail.com**.
3. Google warns **"Google hasn't verified this app"**. That's expected, because it's your own app. Click **Continue**.
4. Allow **"View your email messages and settings"** (this is the read-only access).
5. The page says *"Rika is connected to Gmail!"*. You can close the tab.

> ⚠️ **Testing mode logins expire every 7 days.** When that happens Rika says *"Oops, my Gmail login expired!"* and asks *"Should I open the Google sign-in page now?"*. Say **yes** and sign in again (it takes 10 seconds).
> If you'd rather not do that every week, open **Audience** and click **Publish app** ("In production"). You'll see the same "unverified app" warning when you sign in, but the login stops expiring every week. Either way, Rika only reads mail.

## 4. Tell Rika which Chrome profile to use

Each Gmail account usually has its own Chrome profile, and Rika needs that profile's **folder name**.

1. Right-click the tray icon → **Settings → Show Chrome profiles, printers, mics**.
2. Notepad opens with a table like this:
   ```
   Folder (chrome_profile_dir)    Profile name              Signed-in Google account
   Default                        Person 1                  me@gmail.com
   Profile 2                      RJO Shop                  rjoprintingservice@gmail.com
   ```
3. Find the line with **rjoprintingservice@gmail.com** and note its folder (here `Profile 2`).

*Another way:* in that Chrome window, open `chrome://version`. **Profile Path** ends with the folder name, for example `...\User Data\Profile 2`.

If the folder name in config.json is wrong, Rika still tries to find the profile that is signed in with the account's email.

## 5. Find your printer's exact name

The same Notepad list (step 4) shows your printers. The one with `*` is the Windows default. Copy the name **exactly**, for example `EPSON L120 Series`.
*(You can also find it in Windows **Settings → Bluetooth & devices → Printers & scanners**.)*

## 6. Edit config.json

Right-click the tray icon → **Settings → Edit settings (config.json)**. Change the values marked ✏️, then save (Ctrl+S).

```json
{
  "accounts": {
    "rjoprintingservice": {
      "name": "RJO Printing Service",                 ✏️ what Rika calls it out loud
      "email": "rjoprintingservice@gmail.com",        ✏️
      "chrome_profile_dir": "Profile 2",              ✏️ from step 4
      "aliases": ["rjo", "rjo printing", "shop"]      ✏️ other names you might say
    }
  },
  "default_print_account": "rjoprintingservice",      the Gmail account Rika prints from
  "printer": "EPSON L120 Series",                     ✏️ from step 5 ("" = Windows default)
  "wake_word_sensitivity": 0.5,                       higher = hears "Rika" more easily (and by mistake more often)
  "tts": { "voices": [["af_bella", 0.7], ["af_nicole", 0.3]], "pitch_semitones": 2.0, "speed": 1.1 },   use the Voice Lab (section 8a)
  "search_days": 14                                   how far back to look for similar file names
}
```

Do **not** type the ✏️ notes into the file.

**More accounts:** copy the whole `"rjoprintingservice": {...}` block, put a comma between the blocks and give the new one its own name. For example, add `"personal": {"name": "Personal", "email": "you@gmail.com", "chrome_profile_dir": "Default", "aliases": []}` to say "open personal Gmail".

When you're done: right-click the tray icon → **Settings → Reload settings**. A few settings (`hotkey`, `mic_device`, `wake_word`, `stt`) need you to **Quit** Rika and start it again.

If the file has a mistake, Rika says *"Some of my settings looked wrong, so I used the defaults!"* and **Open logs → Rika log** explains which line.

## 7. Printing tools (SumatraPDF, Word)

- **PDF files** print with **SumatraPDF**, which comes with Rika (it's inside the `Rika\_internal\vendor` folder). If it's missing, install it from <https://www.sumatrapdfreader.org/download-free-pdf-viewer> and Rika finds it automatically.
- **DOC/DOCX files** print with **Microsoft Word**, which runs invisibly in the background. Without Word, Rika uses **LibreOffice** (<https://www.libreoffice.org>).
- Rika **always asks before printing**: *"Print document1.pdf from Juan, 3 pages, on EPSON L120? Yes or no?"*
  For yes, say **yes / yeah / yep / sure / go / print it**. For no, say **no / nope / cancel / stop**.

## 8. Using Rika every day

- Say **"Rika"** and wait for the chime and *"I'm listening!"*, then say your command. Rika stops listening about 1.5 seconds after you stop talking.
- After she answers, just keep talking; there's no need to say her name again. When she says *"Just call my name, I'll be sleeping for a while!"*, she's asleep again. To change how long she waits for a follow-up, set `"follow_up_seconds"` in config.json (default 8; 0 = one command per "Rika").
- Or say it all in one breath, for example **"Rika, print document one dot pdf"**. Rika hears the whole sentence and gets straight to work.
- The **hotkey Ctrl+Alt+R** does the same as saying "Rika". It's useful in a noisy shop.
- If Rika finds several matching files, she reads the top 3 (name, sender, time). Answer **"first"**, **"second"**, **"third"**, or **"the one from Juan"**. Say **"cancel"** to stop.
- If she can't find the file, she reads the 3 closest names, so you can try again with the right one.
- Spoken file names work: say "document **one dot pdf**", "my **underscore** resume **dot docx**", "id **dash** front dot pdf".

**Tray menu** (right-click the pink R):

| Item | What it does |
|---|---|
| Listen now | Same as saying "Rika" (double-clicking the icon does this too) |
| Voice Lab | Opens the Voice Lab in your browser so you can choose Rika's voice (see 8a) |
| Pause listening | Rika stops listening for her name (the hotkey still works) |
| Open logs | Rika's log, the print log (`print_log.csv`, opens in Excel), and the downloads folder |
| Settings | Edit and reload settings, connect Gmail, show profiles, printers and mics, **Start with Windows** |
| Quit | Closes Rika |

The small dot on the icon shows what Rika is doing: green = listening to you, orange = thinking or working, blue = talking, grey = paused, red = microphone problem.

## 8a. Choose Rika's voice (Voice Lab)

Rika's voice is made on this PC by **Kokoro**, a free offline voice engine. She uses Kokoro's built-in voices only: no voice cloning, and no real person's or existing character's voice. You can mix voices and make her higher or faster.

**Open the Voice Lab:** right-click the tray icon → **Voice Lab**. Your browser opens a page.
*(Or run `Rika.exe --voice-lab` from a Command Prompt in the Rika folder.)*

The first time, the lab makes about 540 short clips: every female English voice, each at 4 pitches × 3 speeds × 3 test lines. That takes a few minutes, and the squares fill in as clips finish. Next time it opens instantly.

On the page:

1. Pick a **test line** at the top: *"Hi hi! I'm Rika, your printing assistant!"*, *"Printing now~!"* or *"Uh oh, I can't find that file..."*.
2. Each voice has a little table: **rows = pitch** (+0, +2, +3, +4 semitones higher), **columns = speed** (×1.00, ×1.15, ×1.25). Click a square ▶ to hear it.
3. **Compare all voices** plays the same line in every voice, one after another, at the pitch and speed you picked. It's the easiest way to compare them.
4. To **mix voices**, type a blend in *Try a blend*, for example `af_bella:0.6,af_nicole:0.4`, and click **Render blend**. It appears at the top of the list.
5. When you like a square, click **★ Use this voice**. Rika saves it to config.json, reloads and says *"Do you like my new voice?"*. Then she prepares her lines in the new voice for about 2 minutes.

The green outline marks the voice Rika uses now. Voices starting with `af_` are American and `bf_` are British.
**Tips for a bubbly, energetic voice:** try `af_bella`, `af_nicole`, `af_sky`, `af_nova` and `af_heart`, at pitch **+2 to +3** and speed **×1.15**. Pitch +4 can start to sound cartoonish.

The clips stay in the `voice_lab_output` folder next to Rika.exe. You can open `voice_lab_output\index.html` any time to listen again. To save a choice from that page, the lab has to be running.

**Change the voice without the lab:** run, for example,
```
Rika.exe --voice-lab --use "af_bella:0.6,af_nicole:0.4" --pitch 2 --speed 1.15
```
or edit config.json (then **Settings → Reload settings**):

```json
"tts": {
  "engine": "kokoro",                                  kokoro (default) / edge / sapi
  "voices": [["af_bella", 0.7], ["af_nicole", 0.3]],   one or more built-in voices with weights
  "pitch_semitones": 2.0,                              0 to 4 (higher = brighter)
  "speed": 1.1,                                        0.9 to 1.3
  "fallback": ["edge", "sapi"]                         used, in this order, if Kokoro can't speak
}
```

If Kokoro can't run (for example, the voice download failed), Rika uses the Microsoft Edge online voice. If that also fails (no internet), she uses the built-in Windows voice. She keeps working either way.

## 9. Test without speaking (text mode)

Open a Command Prompt in the Rika folder and run:

```
Rika.exe --text --dry-run
```

Type commands such as `print document1.pdf`. With `--dry-run` nothing is really printed. Leave out `--dry-run` to really print, and add `--speak` to hear her answers too.

Other helpers: `Rika.exe --list-printers`, `Rika.exe --list-chrome-profiles`, `Rika.exe --list-mics`, `Rika.exe --auth` (connect Gmail), `Rika.exe --test-voice` (hear the current voice), `Rika.exe --voice-lab`, `Rika.exe --console` (voice mode with a log window).

## 10. Troubleshooting

| Rika says / you see | What to do |
|---|---|
| "I can't hear anything~ Is the microphone plugged in?" | Plug in the mic. Check **Windows Settings → Privacy & security → Microphone** ("Let desktop apps access your microphone" must be **On**). Pick another mic with `"mic_device"` (see `--list-mics`). |
| She doesn't react to "Rika" | Speak a little louder and closer, or raise `"wake_word_sensitivity"` to 0.6–0.7. Ctrl+Alt+R always works. |
| She wakes up by mistake | Lower `"wake_word_sensitivity"` to 0.3–0.4. |
| "Gmail isn't connected yet~" | Do step 3, and make sure `credentials.json` is in `%APPDATA%\Rika`. |
| "Oops, my Gmail login expired!" | Say **yes** when she offers to reconnect, or use **Settings → Connect Gmail...** |
| "I can't reach the internet~" | Check the Wi-Fi or cable. |
| "I can't find the printer..." | The `"printer"` name doesn't match Windows exactly. See step 5. |
| "The printer looks offline~" | Turn on the printer and check the USB cable. In Windows **Printers & scanners**, untick "Use printer offline". |
| "I can't find Google Chrome" | Install Chrome, or put the full path of `chrome.exe` in `"chrome_path"`. |
| She opens the wrong Chrome profile | Fix `"chrome_profile_dir"` (step 4). |
| Rika sounds different (robotic or another voice) | Her own voice isn't ready yet. Hover over the tray icon: if it says *Downloading voice*, wait. If the download failed, check the internet and restart Rika. Details are in the log. |
| Rika's voice is too high or too fast | Open the **Voice Lab** and pick a lower pitch or speed. |
| The pink icon doesn't appear | Rika may already be running (only one copy can run). Look under the **^** arrow near the clock. |

**Log file:** `%APPDATA%\Rika\rika.log` (tray → Open logs → Rika log). It has the full error details.

## 11. Privacy and housekeeping

- Gmail access is **read-only** (`gmail.readonly`). Rika never sees or stores your Google password; Google's own page handles sign-in.
- The login token is stored in `%APPDATA%\Rika\token.json`. Delete it to disconnect, or remove access at <https://myaccount.google.com/permissions>.
- Downloaded attachments go to `%APPDATA%\Rika\downloads` and are **deleted after 7 days** (`"download_retention_days"`).
- Only `.pdf`, `.doc` and `.docx` attachments are ever downloaded.
- Rika's voice (Kokoro) runs entirely on this PC. Only if Kokoro fails and Rika falls back to the Edge voice is the sentence she says (which can include a file name or sender name) sent to Microsoft's speech service.

---

## For developers

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python tools\download_models.py      & REM Vosk + Whisper models, SumatraPDF
.venv\Scripts\python -m pytest -q                  & REM unit tests (intents, normalizer, print flow)
.venv\Scripts\python tools\voice_selftest.py       & REM wake word + Whisper on synthesized speech
.venv\Scripts\python tools\controller_smoketest.py & REM full voice loop with a fake mic
.venv\Scripts\python voice_lab.py --help           & REM Voice Lab from source
.venv\Scripts\python -m rika --text --dry-run      & REM run from source
build.bat                                          & REM -> dist\Rika\Rika.exe
```

- **Better accuracy:** `python tools\download_models.py --whisper small.en`, then set `"stt": {"model": "small.en"}` and rebuild. It's slower but more accurate.
- **Custom wake word:** train a "Rika" model with openWakeWord (<https://github.com/dscripka/openWakeWord>, see the custom model notebook), run `pip install openwakeword`, then set `"wake_word": {"engine": "openwakeword", "openwakeword_model": "C:\\path\\rika.onnx"}`.
- **Other voice engines:** `"engine": "piper"` with `"piper": {"exe": "...", "model": "....onnx"}`, or add a new engine class in `rika/tts.py` (its docstring explains how). `tts.elevenlabs` (`voice_id`, `api_key`) is reserved for a future ElevenLabs engine and is off. Only use voices you're licensed to use. Don't clone a real voice actor or a copyrighted character.
- **Pitch shifting** uses Rubber Band through `pedalboard` with formant preservation (`"preserve_formants": true`), so a higher voice still sounds natural instead of chipmunk-like.
- **Licenses:** Kokoro-82M and its voices are Apache-2.0. pedalboard and SumatraPDF are GPLv3, which is fine for using Rika in your shop. If you ever redistribute Rika publicly, include their license texts.
