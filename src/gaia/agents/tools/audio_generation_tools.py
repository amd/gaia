# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lemonade-powered music and sound-effect generation for GAIA agents.

The audio-generation API is a Lemonade extension, not OpenAI /audio/speech.
It returns WAV bytes. Never pass the response to the JSON-only client helper.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from gaia.agents.base.file_snapshots import SnapshotError, before_change
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.logger import get_logger

logger = get_logger(__name__)

_SAVE_TO_HELP = (
    "Pass an absolute folder (the file gets a generated name) or a new .wav "
    "file path, e.g. the user's project folder."
)


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
                save_to: Absolute folder or new .wav path for a copy of the file.
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
                save_to: Absolute folder or new .wav path for a copy of the file.
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

        file_name = f"{kind}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:10]}.wav"
        copy_to = None
        if save_to is not None:
            copy_to, refusal = self._audio_save_target(save_to, file_name)
            if refusal is not None:
                return refusal

        client = self.audio_gen_client
        destination = self.audio_gen_output_dir / file_name
        staging = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.audio_gen_output_dir, suffix=".part", delete=False
            ) as temporary:
                staging = Path(temporary.name)
            client.generate_audio(payload, staging, timeout=1800)
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
            result = {
                "status": "success",
                "audio_path": str(destination.resolve()),
                "model": model,
                "type": kind,
                "format": "wav",
                "duration_requested": duration,
            }
            if copy_to is not None:
                not_saved = _copy_for_user(destination, copy_to, kind)
                if not_saved is None:
                    result["saved_to"] = str(copy_to)
                else:
                    # Still a success: the track exists and plays from audio_path.
                    result["copy_not_saved"] = not_saved
            return result
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


def _copy_for_user(source: Path, destination: Path, kind: str):
    """Copy the finished WAV to *destination*, which must not exist yet.

    Exclusive create closes the minutes-long window between the save_to check
    and the copy; a failed copy removes its partial file. Returns ``None`` on
    success, or why the copy is missing and where the original is.
    """
    try:
        before_change(destination, f"generate_{kind}")
        with source.open("rb") as src, destination.open("xb") as dst:
            try:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            except BaseException:
                dst.close()
                destination.unlink(missing_ok=True)
                raise
    except FileExistsError:
        reason = f"{destination} appeared while the audio was generating; it was kept"
    except (OSError, SnapshotError) as exc:
        reason = f"the copy to {destination} could not be saved: {exc}"
    else:
        return None
    return (
        f"The audio was made and is kept at {source}, but {reason}. Tell the "
        "user where it is and ask for a new file name or folder for the copy."
    )
