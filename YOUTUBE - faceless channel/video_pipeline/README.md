# Video Pipeline — Scanner Data → YouTube (Automated)

Reads your scanner_v3 JSON outputs and produces a narrated, visualized
crypto recap video, then uploads it to YouTube. Fully automated, ~$0.04/video.

## Folder layout

```
crypto-scanner/
├── python-scanners/engine/scanner_v3/
│   ├── ignition_scanner.py      ← your existing scanners
│   ├── perp_scanner.py
│   ├── trend_scanner.py
│   ├── run_scan.py
│   └── video_pipeline/          ← THIS FOLDER (drop it here)
│       ├── main.py              — entry point
│       ├── ingest.py            — reads scanner JSONs
│       ├── scriptgen.py         — Claude API → narration script
│       ├── voiceover.py         — ElevenLabs TTS → audio
│       ├── visuals.py           — matplotlib → chart frames
│       ├── compose.py           — MoviePy → final MP4
│       ├── upload.py            — YouTube Data API v3
│       ├── requirements.txt
│       └── README.md
├── outputs/
│   ├── scanner-results/         ← scanner JSONs (input)
│   │   ├── master_radar_LATEST.json
│   │   ├── ignition_v3_LATEST.json
│   │   └── ...
│   ├── videos/                  ← generated videos (output)
│   └── logs/
```

## Setup (one-time)

### 1. Install Python dependencies

```bash
cd video_pipeline
pip install -r requirements.txt
```

### 2. Install ffmpeg

```bash
# Windows (with chocolatey)
choco install ffmpeg

# Mac
brew install ffmpeg

# Linux
sudo apt install ffmpeg
```

### 3. Set API keys

Create a `.env` file or set environment variables:

```bash
# Required for script generation
set ANTHROPIC_API_KEY=sk-ant-...

# Required for high-quality TTS (or skip for free gTTS fallback)
set ELEVEN_API_KEY=xi-...

# Required for auto-upload (skip for --no-upload)
set YOUTUBE_CLIENT_SECRET=C:\path\to\client_secret.json
```

### 4. YouTube API setup (for auto-upload)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → enable **YouTube Data API v3**
3. Create **OAuth 2.0 credentials** (Desktop application)
4. Download `client_secret.json`
5. Set `YOUTUBE_CLIENT_SECRET` to its path
6. First run opens browser for consent → token cached at `~/.youtube_token.json`

## Usage

### Quick test (no upload)

```bash
# Make sure you've run your scanners first
python run_scan.py

# Generate video without uploading
python video_pipeline/main.py --no-upload
```

### Preview script only (no video)

```bash
python video_pipeline/main.py --preview
```

### Full pipeline (scan → video → YouTube)

```bash
python run_scan.py && python video_pipeline/main.py
```

### Landscape format (regular YouTube video instead of Shorts)

```bash
python video_pipeline/main.py --landscape
```

### Custom voice

```bash
# Browse voices at https://elevenlabs.io/voice-library
python video_pipeline/main.py --voice-id <VOICE_ID>
```

## Automate daily with Task Scheduler (Windows)

1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily, 08:00 (after Asian session close)
3. Action: Start a Program
   - Program: `C:\Users\bruno\AppData\Local\Programs\Python\Python312\python.exe`
   - Arguments: `run_scan.py && python video_pipeline/main.py`
   - Start in: `C:\Users\bruno\...\crypto-scanner\python-scanners\engine\scanner_v3`

Or create a batch file `daily_video.bat`:

```bat
@echo off
cd /d "C:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\crypto scanner\crypto-scanner\python-scanners\engine\scanner_v3"

echo [%date% %time%] Running scanners...
python run_scan.py --account 96700

echo [%date% %time%] Generating video...
python video_pipeline\main.py

echo [%date% %time%] Done.
```

## Cost breakdown

| Component | Cost |
|---|---|
| Claude API (script gen) | ~$0.01/video |
| ElevenLabs (90s TTS) | ~$0.03/video |
| YouTube Data API | Free (10K quota/day) |
| matplotlib/ffmpeg | Free |
| **Total per video** | **~$0.04** |
| **Monthly (daily)** | **~$1.20** |

## Pipeline flow

```
Scanner JSONs → ingest.py → summary dict
                               ↓
                         scriptgen.py → Claude API → script JSON
                               ↓
                         voiceover.py → ElevenLabs → audio.mp3
                               ↓
                          visuals.py → matplotlib → frame PNGs
                               ↓
                          compose.py → MoviePy/ffmpeg → video.mp4
                               ↓
                           upload.py → YouTube API → published
```

## Upgrading visuals later

The current visuals.py generates charts from synthetic price data (random walk
based on the coin's 24h change). To use your actual OHLCV cache data:

1. Import your `data.py` module
2. In `_render_price_chart()`, replace the synthetic data block with:
   ```python
   df = data.get_ohlcv(coin_sym, "bybit", "1h", 48, use_cache=True)
   ```
3. Use mplfinance for proper candlestick rendering:
   ```python
   import mplfinance as mpf
   mpf.plot(df, type="candle", style="nightclouds", ...)
   ```

This keeps the pipeline working immediately without requiring your data
dependencies, while giving you a clear upgrade path.
