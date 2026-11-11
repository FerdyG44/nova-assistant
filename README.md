# N.O.V.A. — a personal AI assistant that lives on your Mac

Say **"Nova"** and a full-screen pixel face assembles itself on your screen, listens, answers out loud with
lip-synced speech, and does things on your computer for you. Away from home, talk to it from your phone.

> Türkçe anlatım: [README.tr.md](README.tr.md)

Nova was built for one person's daily use and is shared as-is so you can build your own. It speaks **Turkish by
default** (prompts, wake flow and speech recognition are tuned for Turkish; it can answer in other languages when asked).

## What it can do

- **Talk:** wake word, speech recognition (Whisper), natural neural voices, interruptible ("Nova, stop")
- **A face:** 3D pixel head rendered in the browser, with expressions (happy, sad, surprised, curious, wink),
  lip sync from the actual audio, and an assemble/scatter animation on open and close
- **Your Mac:** open/quit apps, find/read/write files, run terminal commands, read the screen (OCR), click, type,
  clipboard, volume, music, calendar, reminders, timers, email drafts
- **The world:** weather, time, web search, reading web pages, news
- **Your home:** turn the TV on/off, launch apps and control playback through an Apple TV
- **Your work:** tasks, notes, git status of your projects, and facts about your projects from simple Markdown notes
- **Memory:** "remember this" and "from now on do X" are stored permanently
- **Phone:** a small Flutter client (text + voice messages) over Tailscale

## How it works

```
"Nova…" ─▶ Mac microphone ─▶ wake.py (wake word, Whisper)
                               │  open the face (Chrome kiosk, full screen)
                               ▼
Phone ──Tailscale──▶ FastAPI (nova.main) ─▶ brain (Claude API or local Ollama) + tools ─▶ reply
                               │                                                      │
                               └──── SSE ─▶ face.html ◀──────── audio ────────────────┘
```

The brain is either the **Claude API** (recommended: far more reliable at using tools) or a **local model via
Ollama** (free, runs offline, weaker). If Claude is unreachable, Nova falls back to the local model.

## Requirements

- A Mac with Apple Silicon (speech recognition uses MLX; the Mac tools use AppleScript and macOS frameworks)
- macOS with Google Chrome (or Brave/Edge), Python 3.12, Homebrew
- Either an Anthropic API key **or** Ollama with enough RAM for a local model (8 GB minimum, 16 GB better)
- Optional: an Apple TV, Tailscale and Flutter for the phone app

## Setup

```bash
git clone <this repo> nova && cd nova
cp .env.example .env
sed -i '' "s/buraya-uzun-rastgele-bir-token/$(openssl rand -hex 24)/" .env
open -e .env          # set NOVA_OWNER, NOVA_HOME_CITY and (optionally) ANTHROPIC_API_KEY
./start.sh            # first run creates the Python environment and installs packages
```

On first run macOS asks for **microphone** access. Then say "Nova". Speech models download once (~500 MB).

Grant these once in System Settings → Privacy & Security, to the app you start Nova from (Terminal):

| Permission | Needed for |
|---|---|
| Microphone | the wake word and voice commands |
| Accessibility | clicking, typing, keyboard shortcuts |
| Screen Recording | reading the screen |
| Automation | Music, Calendar, Reminders, Finder, Mail (macOS asks on first use) |

### Preview the face without installing anything

Open `server/nova/static/face.html` in Chrome and add `?demo` to the address. Keys 1–7 switch expressions.

### Tell Nova about your projects

Drop a Markdown file per project into `knowledge/` (see `knowledge/README.md`). Your notes stay out of git.

### Apple TV (optional)

```bash
server/.venv/bin/atvremote wizard     # pick your Apple TV, enter the PINs shown on the TV
```

Then: "Nova, turn on the TV", "open YouTube on the TV", "play music", "turn the TV off". The TV itself is
switched through HDMI-CEC when the Apple TV wakes or sleeps.

### Phone access (optional)

```bash
brew install --cask tailscale && tailscale serve --bg 8800
cd app && flutter create --platforms=ios,android . && flutter pub get && flutter run --release
```

Enter the `https://…ts.net` address and the token from `.env` in the app.

### Keep it running

```bash
sed "s|NOVA_DIR|$PWD|g" deploy/com.nova.assistant.plist > ~/Library/LaunchAgents/com.nova.assistant.plist
launchctl load ~/Library/LaunchAgents/com.nova.assistant.plist
```

## Safety — read this before you run it

Nova can run shell commands, click on your screen and edit files. That is the point, and it is also a risk.

- Every request needs the token in `.env`. **Never share or commit your `.env`.** The server listens on
  127.0.0.1 only; remote access goes through your own Tailscale network.
- Read-only actions run immediately. Risky ones (deleting, overwriting, `sudo`, `git push`, quitting apps,
  clicking "Send/Delete/Buy", custom AppleScript) wait for your approval.
- File and command access is limited to your home folder; key and password locations (`.ssh`, Keychains, `.env`…)
  are blocked.
- If you use the Claude API, what you say and the tool results are sent to Anthropic. The local model keeps
  everything on your Mac. Edge voices send the spoken text to Microsoft; Piper is fully local.
- The risk classification is a best-effort filter, not a sandbox. You run this at your own risk.

## Project layout

| Path | What |
|---|---|
| `server/nova/agent.py` | personality and the tool-using chat loop |
| `server/nova/claude_brain.py` | Claude API brain |
| `server/nova/tools.py` | tasks, notes, memory, files, terminal, weather, search, project notes |
| `server/nova/mac.py` | apps, screen reading/clicking, keyboard, music, calendar, reminders |
| `server/nova/home.py` | Apple TV / television |
| `server/nova/wake.py` | wake word, conversation flow, interruption |
| `server/nova/voice.py` | speech recognition and text-to-speech |
| `server/nova/face.py`, `static/face.html` | the face and its event bridge |
| `app/` | Flutter phone client |

Tests: `cd server && .venv/bin/python -m pytest -q`

## License

MIT — see [LICENSE](LICENSE).
