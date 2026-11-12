# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""DictationToolsMixin — turn a document into an audio file the user can play.

``dictate_document`` reads the document, decides whether it can be read as
written or needs rewriting for the ear, records it with the local Kokoro voice
and keeps the recording in GAIA's audio folder, where the Agent UI plays it
inline. A copy goes to the folder the user named.

The helpers at the top are shared with the flagship's ``text_to_speech`` tool so
both save speech the same way.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

#: A two-hour document is about 35 minutes of recording on a laptop GPU; the
#: default tool timeout would cut it off.
DICTATION_TIMEOUT_SECONDS = 3600

_SLUG = re.compile(r"[^A-Za-z0-9]+")


def speech_folder() -> Path:
    """GAIA's audio folder: the only place the Agent UI plays audio from."""
    import gaia.config as gaia_config

    folder = Path(gaia_config.GAIA_CONFIG_DIR) / "cache" / "audio" / "generations"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def new_speech_file(prefix: str, audio_format: str, name_hint: str = "") -> Path:
    """A fresh path in :func:`speech_folder` for one recording."""
    from gaia.audio.lemonade_tts import SPEECH_FILE_FORMATS

    hint = _SLUG.sub("_", name_hint).strip("_")[:24].rstrip("_")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = "_".join(
        part for part in (prefix, hint, stamp, uuid.uuid4().hex[:6]) if part
    )
    return speech_folder() / f"{stem}{SPEECH_FILE_FORMATS[audio_format]}"


def speech_settings(voice: str, speed: Optional[float]) -> Tuple[str, float]:
    """The voice and speed to speak in: the caller's, else the saved read-aloud ones.

    Raises:
        ValueError: the voice is not a Kokoro voice, the speed is out of range,
            or the saved settings cannot be read.
    """
    from gaia.audio.lemonade_tts import DEFAULT_VOICE, KOKORO_VOICES
    from gaia.config import (
        READ_ALOUD_MAX_SPEED,
        READ_ALOUD_MIN_SPEED,
        GaiaConfig,
        GaiaConfigError,
    )

    try:
        saved = GaiaConfig.load()
    except GaiaConfigError as exc:
        raise ValueError(str(exc)) from exc
    voice = voice or saved.read_aloud_voice or DEFAULT_VOICE
    if speed is None:
        speed = saved.read_aloud_speed / 100
    if voice not in KOKORO_VOICES:
        raise ValueError(
            f"Unknown Kokoro voice '{voice}'. Choose one of: "
            f"{', '.join(KOKORO_VOICES)}."
        )
    low, high = READ_ALOUD_MIN_SPEED / 100, READ_ALOUD_MAX_SPEED / 100
    if not low <= speed <= high:
        raise ValueError(f"speed must be between {low} and {high}.")
    return voice, speed


def format_of(path: Path) -> Optional[str]:
    """``"wav"`` / ``"mp3"`` for a speech file name, else ``None``."""
    from gaia.audio.lemonade_tts import SPEECH_FILE_FORMATS

    suffix = path.suffix.lower()
    return next(
        (fmt for fmt, ext in SPEECH_FILE_FORMATS.items() if ext == suffix), None
    )


class CopyFailed(OSError):
    """The recording exists in GAIA's audio folder but a copy could not be saved."""


def copy_for_user(source: Path, destination: Path, tool_name: str) -> None:
    """Copy a finished recording or script to where the user asked for it.

    Written beside the destination and moved into place, so a failed copy never
    leaves half a file under the name the user asked for.

    Raises:
        CopyFailed: the copy could not be written; says where the original is.
    """
    from gaia.agents.base.file_snapshots import before_change

    partial = destination.with_name(destination.name + ".part")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        before_change(destination, tool_name)
        shutil.copyfile(source, partial)
        os.replace(partial, destination)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise CopyFailed(
            f"{source.name} was made, but the copy to {destination} could not "
            f"be saved: {exc}. The original is kept at {source}. Ask the user "
            "for another place to save it, then copy it there."
        ) from exc


class DictationToolsMixin:
    """
    Mixin providing document dictation.

    Tools provided:
    - dictate_document: Read a document aloud into a WAV or MP3 file

    Requires an ``Agent`` host: the listening rewrite runs on the host's chat
    model and progress goes to its console.
    """

    def register_dictation_tools(self) -> None:
        """Register the dictation tool into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        @tool(timeout=DICTATION_TIMEOUT_SECONDS, display_label="Dictating")
        def dictate_document(
            file_path: str,
            form: str = "auto",
            output_path: str = "",
            pages: str = "",
            voice: str = "",
            speed: Optional[float] = None,
            audio_format: str = "mp3",
        ) -> Dict[str, Any]:
            """Read a document aloud into one audio file the user can play.

            Use for "dictate this" or "turn this PDF into audio". Reads the
            whole PDF, Word, Excel, text or Markdown file itself: do not
            read_file or index it first.

            Args:
                file_path: The document; for an attachment, the path in its link.
                form: "auto" rewrites tables, charts, footnotes and links for
                    listening; "as_written" keeps exact words; "listening"
                    always rewrites.
                output_path: A folder or .mp3/.wav file the user asked for.
                    Empty keeps it in GAIA's audio folder.
                pages: PDF pages to read, such as "3-10". Empty reads all.
                voice: Kokoro voice. Omit to use the user's read-aloud voice.
                speed: Rate from 0.5 to 2.0. Omit to use the user's speed.
                audio_format: "mp3" (smaller) or "wav".

            Returns:
                status, form, form_reason, audio_path (the app's copy),
                saved_to, script_path, duration_seconds, format, pages.
            """
            return self._dictate_document(
                file_path, form, output_path, pages, voice, speed, audio_format
            )

    # ------------------------------------------------------------------ steps

    def _dictation_progress(self, message: str) -> None:
        logger.info(message)
        console = getattr(self, "console", None)
        if console is not None and hasattr(console, "report_phase"):
            console.report_phase("dictation", message)

    def _dictation_stopped(self) -> bool:
        from gaia.tool_cancellation import tool_cancelled

        event = getattr(self, "_cancel_event", None)
        console_cancelled = getattr(self, "_console_cancelled", lambda: False)
        return (
            tool_cancelled()
            or (event is not None and event.is_set())
            or bool(console_cancelled())
        )

    def _listening_reply(self, chat: Any, system: str, prompt: str) -> str:
        """One part of the listening script from the host's chat model."""
        from gaia.audio.dictation import REWRITE_MAX_TOKENS, DictationError
        from gaia.chat.sdk import LLMCallCancelled
        from gaia.llm.lemonade_client import no_thinking_kwargs
        from gaia.tool_cancellation import ToolCancelled, current_cancel_event

        # Stop ends the reply in progress, not just the wait for the next part.
        stop = getattr(self, "_cancel_event", None) or current_cancel_event()
        options = {"cancel": stop} if stop is not None else {}
        try:
            response = chat.send_messages(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                tools=[],
                max_tokens=REWRITE_MAX_TOKENS,
                **options,
                **no_thinking_kwargs(getattr(chat, "effective_model", None)),
            )
        except LLMCallCancelled as exc:
            raise ToolCancelled("dictation was stopped") from exc
        usage = getattr(response, "usage", None)
        sink = getattr(self, "_tool_reported_usage", None)
        if sink is not None:
            sink.append(usage if isinstance(usage, dict) else (response.stats or {}))
        if getattr(response, "finish_reason", None) == "length":
            raise DictationError(
                "The model ran out of room part-way through the listening "
                "script, so it would be cut short. Dictate the document as "
                "written, or in smaller page ranges."
            )
        return response.text

    def _dictation_destinations(
        self, stem: str, output_path: str, audio_format: str
    ) -> Tuple[Optional[Path], str, Optional[Dict[str, Any]]]:
        """``(user's audio path or None, format, refusal or None)``."""
        from gaia.agents.tools.path_access import write_access_error
        from gaia.agents.tools.workspace_paths import _workspace_target
        from gaia.audio.lemonade_tts import SPEECH_FILE_FORMATS

        if audio_format not in SPEECH_FILE_FORMATS:
            return (
                None,
                audio_format,
                {
                    "status": "error",
                    "error": (
                        f"audio_format must be one of "
                        f"{', '.join(SPEECH_FILE_FORMATS)}; got '{audio_format}'."
                    ),
                },
            )
        if not output_path:
            return None, audio_format, None
        destination = _workspace_target(self, output_path)
        if destination.is_dir() or not destination.suffix:
            destination = destination / f"{stem}{SPEECH_FILE_FORMATS[audio_format]}"
        else:
            named = format_of(destination)
            if named is None:
                return (
                    None,
                    audio_format,
                    {
                        "status": "error",
                        "error": (
                            "output_path must be a folder or a file ending in .mp3 "
                            f"or .wav; got '{destination.name}'."
                        ),
                    },
                )
            audio_format = named
        denied = write_access_error(self, destination)
        return destination, audio_format, denied

    def _dictate_document(
        self,
        file_path: str,
        form: str,
        output_path: str,
        pages: str,
        voice: str,
        speed: Optional[float],
        audio_format: str,
    ) -> Dict[str, Any]:
        from gaia.agents.tools.path_access import read_access_error, write_access_error
        from gaia.agents.tools.workspace_paths import _workspace_target
        from gaia.audio import dictation
        from gaia.audio.lemonade_tts import (
            DEFAULT_TTS_MODEL,
            LemonadeTTSClient,
            LemonadeTTSError,
        )
        from gaia.llm.lemonade_client import (
            LemonadeClientError,
            resolve_lemonade_base_url,
        )
        from gaia.tool_cancellation import ToolCancelled
        from gaia.upload_names import display_file_name

        if not isinstance(file_path, str) or not file_path.strip():
            return {"status": "error", "error": "file_path names no document."}
        source = _workspace_target(self, file_path.strip())
        denied = read_access_error(self, source)
        if denied:
            return denied
        if not source.is_file():
            return {
                "status": "error",
                "error": f"{source} is not a file. Check the path, or ask the "
                "user where the document is.",
            }
        try:
            voice, speed = speech_settings(voice, speed)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        # An attachment's stored copy carries an id; speak of it by its own name.
        name = display_file_name(str(source))
        stem = Path(name).stem
        user_audio, audio_format, refusal = self._dictation_destinations(
            stem, output_path, audio_format
        )
        if refusal:
            return refusal

        def check() -> None:
            if self._dictation_stopped():
                raise ToolCancelled("dictation was stopped")

        started = time.monotonic()
        audio: Optional[Path] = None
        try:
            self._dictation_progress(f"Reading {name}")
            text, _meta, is_markdown = dictation.document_text(str(source))
            if pages:
                text = dictation.select_pages(text, pages, name)
            if len(text) > dictation.MAX_DICTATION_CHARS:
                raise dictation.too_long_error(text, name)
            spoken_pages = [n for n, _body in dictation.split_pages(text) if n]
            chosen, reasons = dictation.choose_form(text, form)

            script: Optional[str] = None
            user_script: Optional[Path] = None
            left_out: list = []
            if chosen == dictation.LISTENING:
                if user_audio is not None:
                    # Asked for before the work, not after it: the script is a
                    # second file in the user's folder.
                    user_script = user_audio.with_suffix(".script.md")
                    denied = write_access_error(self, user_script)
                    if denied:
                        return denied
                chat = self._make_extraction_chat()

                def on_part(index: int, total: int) -> None:
                    check()
                    self._dictation_progress(
                        f"Writing the listening script: part {index} of {total}"
                    )

                script = dictation.rewrite_for_listening(
                    text,
                    stem,
                    lambda system, prompt: self._listening_reply(chat, system, prompt),
                    on_part,
                    left_out=left_out,
                )
                blocks = dictation.markdown_blocks(script)
            elif is_markdown:
                blocks = dictation.markdown_blocks(text)
            else:
                blocks = dictation.plain_blocks(text)
            pieces = dictation.speech_pieces(blocks)
            if not pieces:
                raise dictation.DictationError(
                    f"{name} left nothing to say once page marks were removed."
                )
            check()

            client = LemonadeTTSClient(
                base_url=resolve_lemonade_base_url(getattr(self, "_base_url", None)),
                model=DEFAULT_TTS_MODEL,
            )
            client.check_model_available()
            minutes = max(1, round(dictation.spoken_minutes(pieces)))
            audio = new_speech_file("dictation", audio_format, stem)

            def on_piece(index: int, total: int) -> None:
                check()
                self._dictation_progress(
                    f"Recording about {minutes} min of audio: part {index} of {total}"
                )

            duration = client.synthesize_file(
                pieces,
                audio,
                voice=voice,
                speed=speed,
                audio_format=audio_format,
                on_piece=on_piece,
            )
            script_file: Optional[Path] = None
            if script is not None:
                script_file = audio.with_suffix(".script.md")
                script_file.write_text(script, encoding="utf-8")
            script_not_copied = ""
            if user_audio is not None:
                copy_for_user(audio, user_audio, "dictate_document")
                if script_file is not None and user_script is not None:
                    try:
                        copy_for_user(script_file, user_script, "dictate_document")
                    except CopyFailed as exc:
                        # The audio is where the user asked; say only what is missing.
                        script_not_copied = str(exc)
                        user_script = None
        except ToolCancelled:
            if audio is not None:
                audio.unlink(missing_ok=True)
            return {
                "status": "cancelled",
                "message": f"Stopped before {name} was fully recorded; "
                "no audio was saved.",
            }
        except CopyFailed as exc:
            return {"status": "error", "error": str(exc), "kept_at": str(audio)}
        except (
            dictation.DictationError,
            LemonadeTTSError,
            LemonadeClientError,
            ConnectionError,
            ImportError,
            OSError,
            ValueError,
        ) as exc:
            return {"status": "error", "error": str(exc)}

        if chosen == dictation.LISTENING:
            why = (
                f"rewritten for listening because it has {_join(reasons)}"
                if reasons and form == "auto"
                else "rewritten for listening, as asked"
            )
        else:
            why = (
                "read as written; nothing in it needed rewording for the ear"
                if form == "auto"
                else "read as written, as asked"
                + (f"; its {_join(reasons)} are read out literally" if reasons else "")
            )
        result: Dict[str, Any] = {
            "status": "success",
            "document": name,
            "form": chosen,
            "form_reason": why,
            "audio_path": str(audio),
            "file_path": str(user_audio or audio),
            "duration_seconds": round(duration, 2),
            "format": audio_format,
            "size_bytes": audio.stat().st_size,
            "voice": voice,
            "model": DEFAULT_TTS_MODEL,
            "words": sum(len(piece.split()) for piece in pieces),
            "chunks": len(pieces),
            "generation_time_s": round(time.monotonic() - started, 1),
        }
        if spoken_pages:
            result["pages"] = f"{spoken_pages[0]}-{spoken_pages[-1]}"
        if user_audio is not None:
            result["saved_to"] = str(user_audio)
        if script_file is not None:
            result["script_path"] = str(user_script or script_file)
        if script_not_copied:
            result["script_not_copied"] = script_not_copied
        if left_out:
            listed = ", ".join(f"{n:,}" for n in sorted(set(left_out))[:12])
            result["figures_left_out"] = (
                f"The listening script does not say these figures from the "
                f"document: {listed}. They may be page or reference numbers "
                "dropped on purpose; tell the user so they can check the script."
            )
        return result


def _join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
