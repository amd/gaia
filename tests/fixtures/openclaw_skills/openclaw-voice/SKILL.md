# OpenClaw Voice Skill

Browser-based voice chat for OpenClaw agents.

## What It Does

Adds voice chat capability to your OpenClaw agent. Users can speak to your agent via a web browser and hear responses in real-time.

## Stack

- **STT**: faster-whisper (local, no API costs)
- **TTS**: ElevenLabs (cloud, high quality) or Chatterbox (self-hosted)
- **Transport**: WebSocket
- **Backend**: OpenClaw gateway (chatCompletions endpoint)

## Quick Setup

### 1. Enable Gateway Endpoint

Add to your `openclaw.json`:

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "chatCompletions": {
          "enabled": true
        }
      }
    }
  }
}
```

### 2. Add Voice Agent

```json
{
  "agents": {
    "list": [
      {
        "id": "voice",
        "workspace": "/path/to/your/workspace",
        "model": "anthropic/claude-sonnet-4-5"
      }
    ]
  }
}
```

### 3. Run the Server

```bash
# Clone and setup
git clone https://github.com/Purple-Horizons/openclaw-voice.git
cd openclaw-voice
uv sync  # or pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your OPENCLAW_GATEWAY_URL, OPENCLAW_GATEWAY_TOKEN, ELEVENLABS_API_KEY

# Run
PYTHONPATH=. python -m src.server.main
```

### 4. Access

Open `http://localhost:8765` in your browser.

For HTTPS (required for mobile mic), use Tailscale Funnel or your own SSL.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENCLAW_GATEWAY_URL` | Yes* | OpenClaw gateway URL (e.g., `http://localhost:18789`) |
| `OPENCLAW_GATEWAY_TOKEN` | Yes* | Gateway auth token |
| `ELEVENLABS_API_KEY` | Recommended | For high-quality TTS |
| `OPENAI_API_KEY` | Fallback | Used if gateway not configured |

*Required for full agent integration. Falls back to direct OpenAI if not set.

## Features

- **Push-to-talk**: Hold button to speak
- **Continuous mode**: Hands-free, auto-listens after responses
- **Keyboard shortcut**: Spacebar toggles recording
- **Mobile support**: Works on phones (requires HTTPS)

## API Key Auth (Optional)

For production, enable auth:

```bash
OPENCLAW_REQUIRE_AUTH=true
OPENCLAW_MASTER_KEY=your-secret-key
```

Then generate user keys via `POST /api/keys`.

## Files

- `src/server/main.py` - FastAPI server
- `src/server/stt.py` - Speech-to-text (Whisper)
- `src/server/tts.py` - Text-to-speech (ElevenLabs/Chatterbox)
- `src/server/backend.py` - AI backend (gateway or OpenAI)
- `src/client/index.html` - Browser UI

## License

MIT — Built by [Purple Horizons](https://purplehorizons.io)
