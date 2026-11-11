# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-level contract for ``dictate_document``.

The voice and the chat model are stubbed; everything between them is real: the
document is read from disk, the form is chosen, the recording lands in GAIA's
audio folder where the Agent UI can play it, and the user's copy and the
listening script land where they asked.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent")

import gaia.config
from gaia.audio.lemonade_tts import LemonadeTTSError
from gaia.security import PathValidator
from gaia.ui.generated_media import media_card, media_dir
from tests.unit.test_profilespec_characterization import chat_agent_build_context

STORY = "The Long Walk\n\nIt rained all morning. Nobody else was out.\n"
REPORT = (
    "Quarterly Review\n\nSales rose.[1] See https://example.com/q3 for more.\n\n"
    "| Product | Q2 | Q3 |\n| Loaf | 2,140 | 3,480 |\n"
)
SCRIPT = "This is the quarterly review.\n\n## Sales\n\nLoaves rose from 2,140 to 3,480."


class FakeChat:
    """Stands in for the agent's chat model during the listening rewrite."""

    effective_model = "test-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def send_messages(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, SimpleNamespace):
            return reply
        return SimpleNamespace(text=reply, finish_reason="stop", usage={}, stats={})


@pytest.fixture
def dictation(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "story.txt").write_text(STORY, encoding="utf-8")
    (allowed / "report.md").write_text(REPORT, encoding="utf-8")
    with chat_agent_build_context("full") as agent:
        agent.path_validator = PathValidator(allowed_paths=[str(allowed)])
        agent._register_tools()
        chat = FakeChat([SCRIPT])
        agent._make_extraction_chat = lambda: chat
        # The build context's agent is a skeleton with no live stop channels.
        agent._cancel_event = None
        agent._console_cancelled = lambda: False
        events = []
        agent.console.report_phase = lambda phase, message: events.append(
            (phase, message)
        )
        with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:

            def synthesize(pieces, destination, **kwargs):
                on_piece = kwargs.get("on_piece")
                for index, _piece in enumerate(pieces, 1):
                    if on_piece:
                        on_piece(index, len(pieces))
                Path(destination).write_bytes(" | ".join(pieces).encode())
                return 12.5

            client.return_value.synthesize_file.side_effect = synthesize
            yield SimpleNamespace(
                tool=agent._tools_registry["dictate_document"]["function"],
                agent=agent,
                allowed=allowed,
                chat=chat,
                client=client,
                synthesize=client.return_value.synthesize_file,
                events=events,
            )


def _spoken(result):
    return Path(result["audio_path"]).read_bytes().decode()


def test_plain_prose_is_recorded_as_written_without_the_model(dictation):
    result = dictation.tool(str(dictation.allowed / "story.txt"))

    assert result["status"] == "success"
    assert result["form"] == "as_written"
    assert "nothing in it needed rewording" in result["form_reason"]
    assert dictation.chat.calls == []
    assert (
        _spoken(result) == "The Long Walk. It rained all morning. Nobody else was out."
    )
    assert "script_path" not in result and "saved_to" not in result
    assert result["format"] == "mp3"
    assert result["duration_seconds"] == 12.5
    assert dictation.synthesize.call_args.kwargs["audio_format"] == "mp3"


def test_the_recording_plays_inline_in_the_agent_ui(dictation):
    result = dictation.tool(str(dictation.allowed / "story.txt"))

    audio = Path(result["audio_path"])
    assert audio.parent == media_dir("audio")
    card = media_card({"type": "tool_result", "success": True, "data": result})
    assert card["render"] == "audio"
    assert card["data"]["src"].startswith("/api/media/audio/dictation_story_")
    assert card["data"]["src"].endswith(".mp3")
    assert card["data"]["duration_seconds"] == 12.5


def test_a_report_is_rewritten_for_listening_and_the_script_is_saved(dictation):
    result = dictation.tool(str(dictation.allowed / "report.md"))

    assert result["form"] == "listening"
    assert result["form_reason"] == (
        "rewritten for listening because it has tables, footnotes and web or "
        "email addresses"
    )
    (call,) = dictation.chat.calls
    assert "text-to-speech voice" in call["system_prompt"]
    assert "2,140" in call["messages"][0]["content"]
    assert call["tools"] == []
    assert _spoken(result) == (
        "This is the quarterly review. | Sales. Loaves rose from 2,140 to 3,480."
    )
    script = Path(result["script_path"])
    assert script.parent == media_dir("audio")
    assert script.read_text(encoding="utf-8").strip() == SCRIPT


def test_a_named_folder_gets_the_audio_and_script_and_the_player_still_works(
    dictation,
):
    out = dictation.allowed / "listen"
    out.mkdir()
    result = dictation.tool(str(dictation.allowed / "report.md"), output_path=str(out))

    assert result["saved_to"] == str(out / "report.mp3")
    assert result["script_path"] == str(out / "report.script.md")
    assert (out / "report.mp3").read_bytes() == Path(result["audio_path"]).read_bytes()
    assert (out / "report.script.md").read_text(encoding="utf-8").strip() == SCRIPT
    card = media_card({"type": "tool_result", "success": True, "data": result})
    assert card["render"] == "audio"
    assert card["data"]["saved_to"] == str(out / "report.mp3")
    assert card["data"]["script_path"] == str(out / "report.script.md")


def test_an_attachment_is_named_as_the_user_named_it(dictation):
    uploads = dictation.allowed / "chat" / "uploads"
    uploads.mkdir(parents=True)
    copy = uploads / "0123456789ab_story.txt"
    copy.write_text(STORY, encoding="utf-8")
    out = dictation.allowed / "listen"
    out.mkdir()
    result = dictation.tool(str(copy), output_path=str(out))

    assert result["document"] == "story.txt"
    assert Path(result["audio_path"]).name.startswith("dictation_story_")
    assert result["saved_to"] == str(out / "story.mp3")
    assert dictation.events[0] == ("dictation", "Reading story.txt")


def test_the_script_file_needs_permission_before_any_work_is_done(dictation):
    """Approval of the audio file is not approval of a second file beside it."""
    report = dictation.allowed / "report.md"
    target = dictation.allowed.parent / "elsewhere" / "report.mp3"
    dictation.agent.path_validator = PathValidator(
        allowed_paths=[str(report), str(target)]
    )
    result = dictation.tool(str(report), output_path=str(target))

    assert result["status"] == "error"
    assert dictation.chat.calls == []
    dictation.synthesize.assert_not_called()
    assert not target.parent.exists()


def test_a_new_folder_is_asked_for_file_by_file_and_created(dictation):
    """Each file is its own grant: a yes to the audio opens nothing else there."""
    asked = []
    out = dictation.allowed.parent / "Audiobooks" / "2026"
    validator = PathValidator(allowed_paths=[str(dictation.allowed)])

    def approve(path):
        # What a yes does: the path joins this chat's scope.
        asked.append(Path(path))
        validator.allowed_paths.add(Path(path))
        return True

    validator._prompt_user_for_access = approve
    dictation.agent.path_validator = validator
    result = dictation.tool(str(dictation.allowed / "report.md"), output_path=str(out))

    assert result["status"] == "success"
    assert asked == [out / "report.mp3", out / "report.script.md"]
    assert (out / "report.mp3").is_file() and (out / "report.script.md").is_file()
    assert out not in validator.allowed_paths


def test_a_copy_that_cannot_be_saved_says_where_the_recording_is(
    dictation, monkeypatch
):
    import shutil

    target = dictation.allowed / "walk.mp3"
    target.write_bytes(b"earlier recording")

    def disk_full(_source, destination):
        Path(destination).write_bytes(b"half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", disk_full)
    result = dictation.tool(
        str(dictation.allowed / "story.txt"), output_path=str(target)
    )

    assert result["status"] == "error"
    kept = Path(result["kept_at"])
    assert kept.is_file() and kept.parent == media_dir("audio")
    assert str(kept) in result["error"] and "No space left" in result["error"]
    # The user's earlier file is untouched and no half-copy sits beside it.
    assert target.read_bytes() == b"earlier recording"
    assert (
        sorted(p.name for p in dictation.allowed.iterdir() if p.suffix == ".part") == []
    )


def test_the_pages_reported_are_the_pages_read(dictation):
    pages = "\n\n".join(f"[Page {n}]\nPage {n} says hello." for n in (1, 2, 3))
    from gaia.audio import dictation as dictation_text

    with patch.object(
        dictation_text, "document_text", return_value=(pages, {"pages": 3}, False)
    ):
        whole = dictation.tool(str(dictation.allowed / "story.txt"))
        part = dictation.tool(str(dictation.allowed / "story.txt"), pages="2-99")

    assert whole["pages"] == "1-3"
    assert part["pages"] == "2-3"
    assert _spoken(part) == "Page 2 says hello. Page 3 says hello."


def test_a_named_file_sets_the_format(dictation):
    target = dictation.allowed / "walk.wav"
    result = dictation.tool(
        str(dictation.allowed / "story.txt"), output_path=str(target)
    )

    assert result["format"] == "wav"
    assert dictation.synthesize.call_args.kwargs["audio_format"] == "wav"
    assert Path(result["audio_path"]).suffix == ".wav"
    assert target.is_file()


def test_as_written_on_a_report_says_what_is_read_literally(dictation):
    result = dictation.tool(str(dictation.allowed / "report.md"), form="as_written")

    assert result["form"] == "as_written"
    assert "read out literally" in result["form_reason"]
    assert dictation.chat.calls == []
    assert "script_path" not in result


def test_progress_names_each_stage(dictation):
    dictation.tool(str(dictation.allowed / "report.md"))

    messages = [message for phase, message in dictation.events if phase == "dictation"]
    assert messages[0] == "Reading report.md"
    assert "Writing the listening script: part 1 of 1" in messages
    assert messages[-1] == "Recording about 1 min of audio: part 2 of 2"


def test_voice_and_speed_follow_read_aloud_settings_unless_named(dictation):
    from gaia.config import GaiaConfig

    GaiaConfig.save_keys({"read_aloud_voice": "bm_george", "read_aloud_speed": 125})
    story = str(dictation.allowed / "story.txt")
    saved = dictation.tool(story)
    named = dictation.tool(story, voice="af_sky", speed=0.75)

    first, second = dictation.synthesize.call_args_list
    assert (saved["voice"], first.kwargs["speed"]) == ("bm_george", 1.25)
    assert (named["voice"], second.kwargs["speed"]) == ("af_sky", 0.75)


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"form": "summary"}, "form must be one of"),
        ({"audio_format": "ogg"}, "audio_format must be one of wav, mp3"),
        ({"voice": "zz_nobody"}, "Unknown Kokoro voice"),
        ({"speed": 3.0}, "speed must be between"),
        ({"pages": "2-3"}, "no page numbers"),
        ({"output_path": "out.flac"}, "folder or a file ending in .mp3 or .wav"),
    ],
)
def test_a_bad_request_is_refused_before_anything_is_recorded(
    dictation, kwargs, message
):
    if "output_path" in kwargs:
        kwargs = {"output_path": str(dictation.allowed / kwargs["output_path"])}
    result = dictation.tool(str(dictation.allowed / "story.txt"), **kwargs)

    assert result["status"] == "error"
    assert message in result["error"]
    dictation.synthesize.assert_not_called()


def test_a_document_or_destination_outside_the_allowed_folders_is_refused(
    dictation, tmp_path
):
    outside = tmp_path / "private.txt"
    outside.write_text("Secret.", encoding="utf-8")
    read = dictation.tool(str(outside))
    write = dictation.tool(
        str(dictation.allowed / "story.txt"), output_path=str(tmp_path / "out.mp3")
    )

    assert read["status"] == "error" and write["status"] == "error"
    dictation.synthesize.assert_not_called()
    assert not (tmp_path / "out.mp3").exists()


def test_a_missing_or_unsupported_document_says_so(dictation):
    missing = dictation.tool(str(dictation.allowed / "nope.pdf"))
    assert "is not a file" in missing["error"]
    deck = dictation.allowed / "deck.pptx"
    deck.write_bytes(b"PK")
    assert "not a document GAIA can dictate" in dictation.tool(str(deck))["error"]


def test_a_missing_voice_model_is_an_error_not_a_download(dictation):
    dictation.client.return_value.check_model_available.side_effect = LemonadeTTSError(
        "TTS model 'kokoro-v1' is not downloaded."
    )
    result = dictation.tool(str(dictation.allowed / "story.txt"))

    assert result == {
        "status": "error",
        "error": "TTS model 'kokoro-v1' is not downloaded.",
    }
    dictation.synthesize.assert_not_called()


def test_a_script_cut_off_by_the_reply_limit_fails_instead_of_recording_half(
    dictation,
):
    dictation.chat.replies = [
        SimpleNamespace(text="Half a scr", finish_reason="length", usage={}, stats={})
    ]
    result = dictation.tool(str(dictation.allowed / "report.md"))

    assert result["status"] == "error"
    assert "cut short" in result["error"]
    dictation.synthesize.assert_not_called()


def test_a_script_with_a_figure_the_document_lacks_is_never_recorded(dictation):
    wrong = SCRIPT.replace("3,480", "3,840")
    dictation.chat.replies = [wrong, wrong]
    result = dictation.tool(str(dictation.allowed / "report.md"))

    assert result["status"] == "error"
    assert "states 3,840, which the document does not" in result["error"]
    assert len(dictation.chat.calls) == 2
    dictation.synthesize.assert_not_called()


def test_a_figure_the_script_dropped_is_reported_with_the_recording(dictation):
    short = "This is the quarterly review.\n\n## Sales\n\nLoaves rose to 3,480."
    dictation.chat.replies = [short, short]
    result = dictation.tool(str(dictation.allowed / "report.md"))

    assert result["status"] == "success"
    assert "2,140" in result["figures_left_out"]


def test_stop_ends_the_run_and_saves_nothing(dictation):
    dictation.agent._console_cancelled = lambda: True
    result = dictation.tool(str(dictation.allowed / "story.txt"))

    assert result["status"] == "cancelled"
    assert not media_dir("audio").exists() or not list(media_dir("audio").iterdir())


def test_a_document_too_long_for_one_run_is_refused_with_how_to_split(dictation):
    book = dictation.allowed / "book.txt"
    book.write_text("word " * 60_000, encoding="utf-8")
    result = dictation.tool(str(book))

    assert result["status"] == "error"
    assert "hours of audio" in result["error"]
    dictation.synthesize.assert_not_called()
