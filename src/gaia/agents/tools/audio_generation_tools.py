# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade-powered music and sound-effect generation for GAIA agents.

The audio-generation API is a Lemonade extension, not OpenAI /audio/speech.
It returns WAV bytes. Never pass the response to the JSON-only client helper.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import requests

from gaia.agents.base.file_snapshots import SnapshotError, before_change
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.logger import get_logger

logger = get_logger(__name__)


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
            )

        @tool(atomic=True, timeout=3600)
        def generate_sound_effect(
            prompt: str,
            model: str = "ThinkSound-SFX",
            duration: Optional[float] = None,
            steps: Optional[int] = None,
            cfg: Optional[float] = None,
            seed: Optional[int] = None,
        ) -> Dict[str, Any]:
            """Generate and save a WAV sound effect using ThinkSound or OpenMOSS.

            Args:
                prompt: Description of the sound and scene.
                model: Exact downloaded sound-effect model ID.
                duration: Desired seconds.
                steps: Optional generation steps, if supported by the backend.
                cfg: Optional guidance for ThinkSound; omit for other recipes.
                seed: Optional reproducibility seed.
            """
            return self._generate_lemonade_audio(
                "sfx",
                prompt,
                model,
                duration=duration,
                steps=steps,
                cfg=cfg,
                seed=seed,
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
            return {
                "status": "error",
                "error": (
                    f"'{model}' is not a downloaded audio-generation model. "
                    "Call list_audio_generation_models() to see available models."
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

        client = self.audio_gen_client
        destination = self.audio_gen_output_dir / (
            f"{kind}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:10]}.wav"
        )
        staging = None
        try:
            with requests.post(
                f"{client.base_url}/audio/generations",
                json=payload,
                headers={"Content-Type": "application/json", **client._auth_headers()},
                timeout=(10, 1800),
                stream=True,
            ) as response:
                if response.status_code == 401:
                    return {
                        "status": "error",
                        "error": "Lemonade rejected the API key (401).",
                    }
                if response.status_code >= 400:
                    return {
                        "status": "error",
                        "error": (
                            f"Lemonade audio generation failed ({response.status_code}): "
                            f"{response.text[:600]}"
                        ),
                    }
                content_type = response.headers.get("Content-Type", "").lower()
                if "json" in content_type:
                    return {
                        "status": "error",
                        "error": (
                            "Expected WAV audio but received a JSON response from Lemonade."
                        ),
                    }
                with tempfile.NamedTemporaryFile(
                    dir=self.audio_gen_output_dir, suffix=".part", delete=False
                ) as temporary:
                    staging = Path(temporary.name)
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            temporary.write(chunk)
            # Reject empty/truncated/JSON responses even when the HTTP status
            # was 200; an agent must never claim a broken file was generated.
            with staging.open("rb") as wav:
                header = wav.read(12)
            if staging.stat().st_size < 44 or not (
                header[:4] in (b"RIFF", b"RF64") and header[8:] == b"WAVE"
            ):
                return {
                    "status": "error",
                    "error": "Lemonade returned invalid WAV data.",
                }
            before_change(destination, f"generate_{kind}")
            os.replace(staging, destination)
            staging = None
            logger.info("Generated %s via %s: %s", kind, model, destination)
            return {
                "status": "success",
                "audio_path": str(destination.resolve()),
                "model": model,
                "type": kind,
                "format": "wav",
                "duration_requested": duration,
            }
        except (requests.RequestException, OSError, SnapshotError) as exc:
            return {"status": "error", "error": f"Audio generation failed: {exc}"}
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
