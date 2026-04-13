# Scythe-Transcribe

Cloud speech-to-text for **Windows** and **macOS**: a **React** web UI in your browser talks to a **FastAPI** server on your machine. Record from the microphone in the browser, transcribe with **Groq** (Whisper) or **OpenRouter** (audio-capable chat models), optionally run a follow-up **LLM** step, and apply a personal **keyword dictionary**. API keys and preferences are stored in JSON files under your user config directory (optional `.env` for keys).

## Requirements

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (recommended)
- **Node.js 20+** (only if you build or develop the frontend)
- Microphone access in the browser when prompted
- API keys from [Groq](https://console.groq.com/) and/or [OpenRouter](https://openrouter.ai/) as needed

## Quick start (tray + bundled UI)

From the project root:

```bash
uv sync
uv run scythe-transcribe
```

A **system tray** icon (Windows) or **menu bar** icon (macOS) appears. Use **Open Scythe** to open the web UI (served at `http://127.0.0.1:8765/` when the server is enabled), **Disable server** / **Enable server** to stop or restart the API, or **Shutdown** to quit.

Set `SCYTHE_TRAY=0` to run only the HTTP server in the foreground (no tray):

```powershell
# Windows PowerShell
$env:SCYTHE_TRAY = "0"
uv run scythe-transcribe
```

```bash
# macOS / Linux
export SCYTHE_TRAY=0
uv run scythe-transcribe
```

For API-only mode (e.g. automation), set `SCYTHE_SERVER_ONLY=1` so `python -m scythe_transcribe` runs Uvicorn without opening the tray.

## Frontend development (Vite)

1. Start the API (foreground):

   ```bash
   $env:SCYTHE_SERVER_ONLY = "1"   # PowerShell
   uv run python -m scythe_transcribe
   ```

2. In another terminal, start the Vite dev server (proxies `/api` to `http://127.0.0.1:8765`):

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

3. Open the URL Vite prints (port **5173**). Hot reload applies to the SPA; the Python API must be running separately.

## Rebuilding the SPA for packaged static files

After changing the frontend:

```bash
cd frontend
npm run build
```

Copy the build into the Python package’s `web_dist` folder so FastAPI can serve it next to the API (from the repo root):

```powershell
# PowerShell
Remove-Item -Recurse -Force src\scythe_transcribe\web_dist\* -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force frontend\dist\* src\scythe_transcribe\web_dist\
```

## API keys and preferences

- **In the app:** Enter keys and use **Save keys**. Keys are written to `api_keys.json` under the app config directory (see `platformdirs` / `user_config_dir` for `"Scythe-Transcribe"`).
- **Environment variables (optional):** `GROQ_API_KEY` and/or `OPENROUTER_API_KEY` (e.g. in a `.env` file in the working directory). File-stored keys take precedence when present.

Refresh the OpenRouter model list after saving an OpenRouter key (**Refresh OpenRouter models**).

## What you can configure

| Area | Description |
|------|-------------|
| **Transcription** | **Groq** (Whisper models) or **OpenRouter** (models that accept audio). Pick a model from the list or enter a custom model ID. |
| **Groq ASR prompt** | Optional Whisper “prompt” for names, jargon, or spelling hints (Groq only). |
| **OpenRouter instruction** | Text sent with the audio to the chat model (OpenRouter transcription only). |
| **Keyword dictionary** | One line per rule: `typo -> correction`. Applied to the transcript after ASR and before any LLM post-processing. |
| **LLM post-processing** | Optional: toggle on, set a **system-style prompt**, choose **Groq** or **OpenRouter** text model, then run processing after each capture. |

Recording is **start → stop & transcribe** (no live streaming ASR in this version).

## Development

Install with dev dependencies (tests, Ruff):

```bash
uv sync --group dev
```

Run tests:

```bash
uv run pytest
```

Lint:

```bash
uv run ruff check src tests
```

## Packaging note (macOS)

If you distribute a standalone app that uses the microphone, include a microphone usage description (e.g. `NSMicrophoneUsageDescription` in `Info.plist`) so macOS can prompt for mic access in the **browser**.

## Docs

- [Groq speech-to-text](https://console.groq.com/docs/speech-to-text)
- [OpenRouter audio](https://openrouter.ai/docs/guides/overview/multimodal/audio)
