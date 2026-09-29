# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regenerate the gaia_media fixtures: a spoken clip and a sign image.

The planted facts below are the ground truth the gaia_media scenarios judge
against (eval/scenarios/GAIA_FIXTURE_VALUES.md). Change them together.

    python tests/fixtures/gaia/media/_gen_media.py [--image-only]

The image is Pillow-only and portable. The audio uses the Windows SAPI voice
(System.Speech), so regenerating it needs Windows; the committed WAV is what
the eval runs against everywhere.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
AUDIO_PATH = HERE / "facilities_update.wav"
IMAGE_PATH = HERE / "bakery_sign.png"

AUDIO_SCRIPT = (
    "Hi team, this is Priya from facilities with a quick update before Friday. "
    "The quarterly fire drill for the Lindenfield building has moved to "
    "Thursday, October ninth, at two fifteen in the afternoon. "
    "When the alarm sounds, everyone should gather in the north parking lot, "
    "next to the blue bike racks. "
    "Thanks, and please pass this along to anyone who is out today."
)

SIGN_LINES = [
    ("HALVORSEN BAKERY", 64),
    ("Today's special:", 40),
    ("Cardamom Plum Tart  $6.75", 48),
    ("Closed Mondays", 36),
]


def make_image() -> None:
    width, height = 900, 520
    img = Image.new("RGB", (width, height), (250, 245, 232))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, width - 20, height - 20], outline=(90, 50, 30), width=8)
    y = 70
    for text, size in SIGN_LINES:
        font = ImageFont.load_default(size=size)
        box = draw.textbbox((0, 0), text, font=font)
        x = (width - (box[2] - box[0])) // 2
        draw.text((x, y), text, fill=(60, 30, 15), font=font)
        y += (box[3] - box[1]) + 50
    img.save(IMAGE_PATH, optimize=True)
    print(f"wrote {IMAGE_PATH} ({IMAGE_PATH.stat().st_size} bytes)")


def make_audio() -> None:
    if sys.platform != "win32":
        raise SystemExit(
            "Audio regeneration uses the Windows SAPI voice and must run on "
            "Windows. Rerun with --image-only, or regenerate on Windows."
        )
    # 16 kHz mono 16-bit matches what transcribe_media decodes to anyway.
    script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Zira Desktop')
$s.Rate = 0
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('{AUDIO_PATH}', $fmt)
$s.Speak('{AUDIO_SCRIPT.replace("'", "''")}')
$s.Dispose()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
    )
    print(f"wrote {AUDIO_PATH} ({AUDIO_PATH.stat().st_size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image-only", action="store_true")
    args = parser.parse_args()
    make_image()
    if not args.image_only:
        make_audio()


if __name__ == "__main__":
    main()
