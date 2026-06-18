"""
generate_voices.py — Standalone voice generator for video editing.

Generates individual MP3 files from script segments, ready to drag
into CapCut, Premiere, DaVinci Resolve, or any editor.

USAGE:

  # From a pipeline script JSON:
  python generate_voices.py script_20260507_083733.json

  # From a custom text file (one segment per line, format: "Label | Text"):
  python generate_voices.py --text segments.txt

  # With a custom voice clone:
  python generate_voices.py script.json --voice-id YOUR_VOICE_ID

  # List available voices:
  python generate_voices.py --list-voices

CUSTOM TEXT FILE FORMAT (segments.txt):
  Hook | Alright degens, the scanner is hot today.
  Signal 1 Whale Candles | We're seeing massive whale candles on ALGO...
  Funding Rate | Funding rates just turned negative on ANKR...
  CTA | Subscribe for daily alpha and drop your coin below!

OUTPUT:
  Creates a folder with individual MP3 files:
    voices_20260507_1030/
    ├── 01_Hook.mp3
    ├── 02_Signal_1_Whale_Candles.mp3
    ├── 03_Funding_Rate.mp3
    └── 04_CTA.mp3

REQUIRES:
  - ELEVEN_API_KEY environment variable
  - requests package (pip install requests)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # ElevenLabs "Adam"
DEFAULT_MODEL    = "eleven_turbo_v2_5"

API_BASE = "https://api.elevenlabs.io/v1"


def get_api_key() -> str:
    key = os.environ.get("ELEVEN_API_KEY") or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        print("ERROR: ELEVEN_API_KEY not set.")
        print("  Get your key from https://elevenlabs.io/app/settings/api-keys")
        print("  Then run: setx ELEVEN_API_KEY \"your-key-here\"")
        sys.exit(1)
    return key


# ─────────────────────────────────────────────────────────────────────────────
# LIST VOICES
# ─────────────────────────────────────────────────────────────────────────────

def list_voices(api_key: str):
    """Print all available voices (including clones)."""
    import requests

    print("\n  Loading voices...\n")
    resp = requests.get(
        f"{API_BASE}/voices",
        headers={"xi-api-key": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    voices = resp.json().get("voices", [])

    # Show cloned voices first
    cloned = [v for v in voices if v.get("category") == "cloned"]
    stock  = [v for v in voices if v.get("category") != "cloned"]

    if cloned:
        print("  ── YOUR CLONED VOICES ──")
        for v in cloned:
            print(f"    {v['name']:<25} ID: {v['voice_id']}")
        print()

    print("  ── STOCK VOICES (top 15) ──")
    for v in stock[:15]:
        labels = v.get("labels", {})
        desc = ", ".join(f"{k}={v}" for k, v in labels.items()) if labels else ""
        print(f"    {v['name']:<25} ID: {v['voice_id']}  ({desc})")

    print(f"\n  Total: {len(voices)} voices ({len(cloned)} cloned)")
    print(f"\n  Usage: python generate_voices.py script.json --voice-id <ID>")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK QUOTA
# ─────────────────────────────────────────────────────────────────────────────

def check_quota(api_key: str) -> dict:
    import requests
    resp = requests.get(
        f"{API_BASE}/user/subscription",
        headers={"xi-api-key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    limit = data.get("character_limit", 0)
    used  = data.get("character_count", 0)
    return {
        "tier":      data.get("tier", "?"),
        "remaining": max(0, limit - used),
        "limit":     limit,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GENERATE VOICE FOR A SINGLE SEGMENT
# ─────────────────────────────────────────────────────────────────────────────

def generate_one(
    text:       str,
    output:     Path,
    api_key:    str,
    voice_id:   str = DEFAULT_VOICE_ID,
    model_id:   str = DEFAULT_MODEL,
) -> int:
    """
    Generate TTS for one segment, write MP3 to disk.
    Returns byte count.
    """
    import requests

    resp = requests.post(
        f"{API_BASE}/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    output.write_bytes(resp.content)
    return len(resp.content)


# ─────────────────────────────────────────────────────────────────────────────
# PARSE INPUTS
# ─────────────────────────────────────────────────────────────────────────────

def load_from_json(path: Path) -> list[dict]:
    """Load segments from a pipeline script JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = []

    # Add hook as first segment
    hook = data.get("hook", "")
    if hook:
        segments.append({"label": "Hook", "text": hook})

    # Add each segment
    for seg in data.get("segments", []):
        coin = seg.get("coin", "Segment")
        narration = seg.get("narration", "")
        if narration:
            # Clean label for filename
            label = coin.replace("/", "_").replace(" ", "_")
            segments.append({"label": label, "text": narration})

    # Add outro
    outro = data.get("outro", "")
    if outro:
        segments.append({"label": "CTA", "text": outro})

    return segments


def load_from_text(path: Path) -> list[dict]:
    """
    Load segments from a text file.
    Format: Label | Text to speak
    Or just: Text to speak (auto-numbered)
    """
    segments = []
    lines = path.read_text(encoding="utf-8").strip().splitlines()

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "|" in line:
            label, text = line.split("|", 1)
            label = label.strip()
            text = text.strip()
        else:
            label = f"Segment_{i}"
            text = line

        if text:
            segments.append({"label": label, "text": text})

    return segments


def _safe_filename(label: str) -> str:
    """Convert label to safe filename."""
    safe = re.sub(r'[^\w\s-]', '', label)
    safe = re.sub(r'\s+', '_', safe)
    return safe[:50]


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate individual voice MP3 files from script segments"
    )
    parser.add_argument("input", nargs="?",
                        help="Script JSON file or text file path")
    parser.add_argument("--text", action="store_true",
                        help="Treat input as text file (Label | Text format)")
    parser.add_argument("--voice-id", type=str, default=None,
                        help="ElevenLabs voice ID (default: Adam)")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help="ElevenLabs model ID")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: voices_TIMESTAMP/)")
    parser.add_argument("--list-voices", action="store_true",
                        help="List all available voices and exit")

    args = parser.parse_args()
    api_key = get_api_key()

    # List voices mode
    if args.list_voices:
        list_voices(api_key)
        return

    # Must have input file
    if not args.input:
        parser.print_help()
        print("\n  Examples:")
        print("    python generate_voices.py script_20260507.json")
        print("    python generate_voices.py segments.txt --text")
        print("    python generate_voices.py script.json --voice-id abc123")
        print("    python generate_voices.py --list-voices")
        return

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    # Load segments
    if args.text or input_path.suffix == ".txt":
        segments = load_from_text(input_path)
    else:
        segments = load_from_json(input_path)

    if not segments:
        print("ERROR: No segments found in input file")
        sys.exit(1)

    # Setup output dir
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        out_dir = Path(f"voices_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    voice_id = args.voice_id or DEFAULT_VOICE_ID

    # Check quota
    quota = check_quota(api_key)
    total_chars = sum(len(s["text"]) for s in segments)
    print(f"\n  ── VOICE GENERATOR ──")
    print(f"  Segments:  {len(segments)}")
    print(f"  Characters: {total_chars:,}")
    print(f"  Quota:     {quota['remaining']:,} remaining ({quota['tier']})")
    print(f"  Voice:     {voice_id}")
    print(f"  Output:    {out_dir}/")
    print()

    if total_chars > quota["remaining"]:
        print(f"  WARNING: Need {total_chars:,} chars but only {quota['remaining']:,} left!")
        resp = input("  Continue anyway? (y/n) ")
        if resp.lower() != "y":
            return

    # Generate each segment
    generated = []
    for i, seg in enumerate(segments, 1):
        label = _safe_filename(seg["label"])
        filename = f"{i:02d}_{label}.mp3"
        output = out_dir / filename

        print(f"  [{i}/{len(segments)}] {label} ({len(seg['text'])} chars)...", end=" ", flush=True)

        try:
            nbytes = generate_one(
                text=seg["text"],
                output=output,
                api_key=api_key,
                voice_id=voice_id,
                model_id=args.model,
            )
            print(f"✓ {nbytes:,} bytes → {filename}")
            generated.append(output)
        except Exception as e:
            print(f"✗ ERROR: {e}")

    # Summary
    print(f"\n  ── DONE ──")
    print(f"  Generated: {len(generated)}/{len(segments)} files")
    print(f"  Location:  {out_dir.resolve()}")
    print(f"\n  Drag these into CapCut to replace your voice recordings.")
    print()


if __name__ == "__main__":
    main()
