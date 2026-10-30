# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade-powered music and sound-effect generation for GAIA agents.

The audio-generation API is a Lemonade extension, not OpenAI /audio/speech.
It returns WAV bytes. Never pass the response to the JSON-only client helper.
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from gaia.agents.base.file_snapshots import SnapshotError, before_change
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.lemonade_launcher import describe_client_hint
from gaia.logger import get_logger

logger = get_logger(__name__)

_SAVE_TO_HELP = (
    "Pass an absolute folder (the file gets a generated name) or a new .wav "
    "file path, e.g. the user's project folder."
)


class InvalidWavError(ValueError):
    """The bytes Lemonade returned are not a playable WAV file."""


def wav_duration_seconds(path: Path) -> Optional[float]:
    """Seconds of audio in a RIFF WAV, from its ``fmt `` and ``data`` chunks.

    Returns None for RF64, whose sizes live in a ``ds64`` chunk this reader
    does not parse. Raises InvalidWavError when a RIFF file has no audio.
    """
    size = path.stat().st_size
    with path.open("rb") as wav:
        header = wav.read(12)
        if len(header) < 12 or header[8:] != b"WAVE":
            raise InvalidWavError("missing RIFF/WAVE header")
        if header[:4] == b"RF64":
            return None
        if header[:4] != b"RIFF":
            raise InvalidWavError("missing RIFF/WAVE header")
        byte_rate = 0
        while True:
            chunk = wav.read(8)
            if len(chunk) < 8:
                break
            chunk_id = chunk[:4]
            chunk_size = struct.unpack("<I", chunk[4:])[0]
            if chunk_id == b"fmt ":
                fmt = wav.read(chunk_size)
                if len(fmt) < 12:
                    raise InvalidWavError("truncated fmt chunk")
                byte_rate = struct.unpack("<I", fmt[8:12])[0]
                if chunk_size % 2:
                    wav.seek(1, os.SEEK_CUR)
                continue
            if chunk_id == b"data":
                if not byte_rate:
                    raise InvalidWavError("data chunk before a valid fmt chunk")
                # A streamed WAV may carry a 0 or 0xFFFFFFFF placeholder size.
                available = size - wav.tell()
                if chunk_size in (0, 0xFFFFFFFF) or chunk_size > available:
                    chunk_size = available
                if chunk_size <= 0:
                    raise InvalidWavError("no audio samples")
                return chunk_size / byte_rate
            wav.seek(chunk_size + (chunk_size % 2), os.SEEK_CUR)
    raise InvalidWavError("no fmt/data chunks")


class AudioGenerationToolsMixin:
    """Register local WAV generation tools backed by downloaded Lemonade models."""

    def init_audio_generation(self, output_dir: Optional[str] = None) -> None:
        from gaia.agents.base.tools import tool
        from gaia.config import GAIA_CONFIG_DIR

        self.audio_gen_client = LemonadeClient(verbose=False)
        self.audio_gen_output_dir = (
            Path(output_dir or GAIA_CONFIG_DIR / "cache" / "audio" / "generations")
            .expanduser()
            .resolve()
        )
        self.audio_gen_output_dir.mkdir(parents=True, exist_ok=True)

        @tool(atomic=True)
        def list_audio_generation_models() -> Dict[str, Any]:
            """List downloaded Lemonade music and sound-effect generators."""
            try:
                return {
                    "status": "success",
                    "models": [
                        {
                            "name": m["id"],
                            "recipe": m.get("recipe"),
                            "labels": m.get("labels", []),
                        }
                        for m in self._audio_generation_models()
                    ],
                }
            except LemonadeClientError as exc:
                return {"status": "error", "error": str(exc)}

        @tool(atomic=True, timeout=3600)
        def generate_music(
            prompt: str,
            model: str = "ACE-Step-Music",
            lyrics: Optional[str] = None,
            vocal_language: Optional[str] = None,
            duration: Optional[float] = None,
            steps: Optional[int] = None,
            seed: Optional[int] = None,
            save_to: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Generate and save WAV music. Empty lyrics mean instrumental music.

            Args:
                prompt: Genre, instruments, mood, tempo and optional vocal style.
                model: Exact downloaded ACE-Step model ID.
                lyrics: Song lyrics, optionally with [verse]/[chorus] tags.
                vocal_language: Optional BCP-47 lyrics language, e.g. 'de'.
                duration: Desired seconds.
                steps: Optional generation steps; omit for backend defaults.
                seed: Optional reproducibility seed.
                save_to: Absolute folder or new .wav path to save into.
            """
            return self._generate_lemonade_audio(
                "music",
                prompt,
                model,
                lyrics=lyrics,
                vocal_language=vocal_language,
                duration=duration,
                steps=steps,
                seed=seed,
                save_to=save_to,
            )

        @tool(atomic=True, timeout=3600)
        def generate_sound_effect(
            prompt: str,
            model: str = "ThinkSound-SFX",
            duration: Optional[float] = None,
            steps: Optional[int] = None,
            cfg: Optional[float] = None,
            seed: Optional[int] = None,
            save_to: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Generate and save a WAV sound effect using ThinkSound or OpenMOSS.

            Args:
                prompt: Description of the sound and scene.
                model: Exact downloaded sound-effect model ID.
                duration: Desired seconds.
                steps: Optional generation steps, if supported by the backend.
                cfg: Optional guidance for ThinkSound; omit for other recipes.
                seed: Optional reproducibility seed.
                save_to: Absolute folder or new .wav path to save into.
            """
            return self._generate_lemonade_audio(
                "sfx",
                prompt,
                model,
                duration=duration,
                steps=steps,
                cfg=cfg,
                seed=seed,
                save_to=save_to,
            )

    def _audio_generation_models(self):
        data = self.audio_gen_client.list_models()  # Downloaded models only.
        return [
            m
            for m in data.get("data", [])
            if m.get("id") and "audio-generation" in (m.get("labels") or [])
        ]

    def _generate_lemonade_audio(
        self,
        kind: str,
        prompt: str,
        model: str,
        *,
        lyrics: Optional[str] = None,
        vocal_language: Optional[str] = None,
        duration: Optional[float] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        seed: Optional[int] = None,
        save_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not prompt or not prompt.strip():
            return {"status": "error", "error": "A nonempty prompt is required."}
        if duration is not None and duration <= 0:
            return {"status": "error", "error": "Duration must be positive."}
        if steps is not None and steps <= 0:
            return {"status": "error", "error": "Steps must be positive."}
        try:
            models = {m["id"]: m for m in self._audio_generation_models()}
        except LemonadeClientError as exc:
            return {"status": "error", "error": str(exc)}
        selected = models.get(model)
        if selected is None:
            available = ", ".join(sorted(models)) or "none"
            return {
                "status": "error",
                "error": (
                    f"'{model}' is not a downloaded audio-generation model "
                    f"(downloaded: {available}). Ask the user before adding it: "
                    f"{describe_client_hint('pull', model).instruction}"
                ),
            }
        recipe = selected.get("recipe")
        # The current Lemonade recipes support different operations. Do not
        # send vocal lyrics to the SFX engine or ThinkSound CFG to ACE-Step.
        if kind == "music" and recipe != "acestep":
            return {"status": "error", "error": "Music requires an ACE-Step model."}
        if kind == "sfx" and recipe not in {"thinksound", "openmoss"}:
            return {
                "status": "error",
                "error": "SFX requires a ThinkSound or OpenMOSS model.",
            }
        if cfg is not None and recipe != "thinksound":
            return {"status": "error", "error": "CFG is only supported by ThinkSound."}

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": "wav",
        }
        for key, value in (
            ("lyrics", lyrics),
            ("vocal_language", vocal_language),
            ("duration", duration),
            ("steps", steps),
            ("cfg", cfg),
            ("seed", seed),
        ):
            if value is not None:
                payload[key] = value

        file_name = f"{kind}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:10]}.wav"
        if save_to is None:
            destination = self.audio_gen_output_dir / file_name
        else:
            destination, refusal = self._audio_save_target(save_to, file_name)
            if refusal is not None:
                return refusal

        client = self.audio_gen_client
        staging = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.audio_gen_output_dir, suffix=".part", delete=False
            ) as temporary:
                staging = Path(temporary.name)
            client.generate_audio(payload, staging, timeout=1800)
            # Reject empty/truncated/JSON responses even when the HTTP status
            # was 200; an agent must never claim a broken file was generated.
            try:
                if staging.stat().st_size < 44:
                    raise InvalidWavError("response shorter than a WAV header")
                seconds = wav_duration_seconds(staging)
            except InvalidWavError as exc:
                return {
                    "status": "error",
                    "error": f"Lemonade returned invalid WAV data ({exc}).",
                }
            before_change(destination, f"generate_{kind}")
            if save_to is None:
                os.replace(staging, destination)
            else:
                _publish_new_file(staging, destination)
                staging.unlink()
            staging = None
            logger.info("Generated %s via %s: %s", kind, model, destination)
            return {
                "status": "success",
                "audio_path": str(destination.resolve()),
                "model": model,
                "type": kind,
                "format": "wav",
                "prompt": prompt,
                "duration_requested": duration,
                "duration_seconds": (
                    round(seconds, 2) if seconds is not None else None
                ),
            }
        except FileExistsError:
            return {
                "status": "error",
                "error": (
                    f"{destination} appeared while the audio was generating; "
                    "it was kept. Choose a new file name."
                ),
            }
        except (LemonadeClientError, OSError, SnapshotError) as exc:
            return {"status": "error", "error": f"Audio generation failed: {exc}"}
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)

    def _audio_save_target(self, save_to: str, file_name: str):
        """Resolve *save_to* to a new .wav path the host's write policy allows.

        The policy is checked before anything about the path is reported, so a
        refused location never learns what exists there.
        Returns ``(path, None)`` or ``(None, error_result)``.
        """
        if not isinstance(save_to, str) or not save_to.strip() or "\x00" in save_to:
            return None, {
                "status": "error",
                "error": f"Invalid save_to. {_SAVE_TO_HELP}",
            }
        target = Path(save_to.strip()).expanduser()
        if not target.is_absolute():
            return None, {
                "status": "error",
                "error": (
                    f"save_to must be an absolute path, got '{save_to}'. "
                    f"{_SAVE_TO_HELP}"
                ),
            }
        target = target.resolve()
        if target.suffix.lower() != ".wav":
            target = target / file_name
        validator = getattr(self, "path_validator", None)
        if validator is None:
            return None, {
                "status": "error",
                "error": (
                    "This agent has no write policy, so it cannot save outside "
                    "GAIA's audio folder. Omit save_to to keep the file in "
                    f"{self.audio_gen_output_dir}."
                ),
            }
        if not validator.is_path_allowed(str(target)):
            return None, {
                "status": "error",
                "error": validator.access_denied_message(str(target)),
            }
        if not target.parent.is_dir():
            return None, {
                "status": "error",
                "error": (
                    f"Folder {target.parent} does not exist. Create it first "
                    "or pick an existing folder."
                ),
            }
        if target.exists():
            return None, {
                "status": "error",
                "error": (
                    f"{target} already exists; choose a new file name so the "
                    "existing audio is not replaced."
                ),
            }
        # The write cap guards model-written text; audio size follows duration.
        allowed, reason = validator.validate_write(str(target), content_size=0)
        if not allowed:
            return None, {"status": "error", "error": reason}
        return target, None


def _publish_new_file(source: Path, destination: Path) -> None:
    """Copy *source* to *destination*, which must not exist yet.

    Exclusive create closes the minutes-long window between the save_to check
    and the copy; a failed copy removes its partial file.
    """
    with source.open("rb") as src, destination.open("xb") as dst:
        try:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        except BaseException:
            dst.close()
            destination.unlink(missing_ok=True)
            raise
