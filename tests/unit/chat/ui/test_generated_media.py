# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Generated images and audio show inline in the chat (#540).

The endpoint must serve only files GAIA itself wrote into its generation
folders: traversal, absolute paths, symlinks, subfolders and other suffixes
all 404. A tool result becomes an ``image`` / ``audio`` card only for such a
file, in-process and relayed alike, and the card survives a reload.
"""

import asyncio
import json
import os
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import gaia.config as gaia_config
from gaia.ui import generated_media
from gaia.ui.generated_media import (
    MediaNotServable,
    media_card,
    media_dir,
    resolve_generated_media,
    wav_duration_seconds,
)
from gaia.ui.utils import DOCUMENT_ROOTS_ENV

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def gaia_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.delenv(DOCUMENT_ROOTS_ENV, raising=False)
    cfg = tmp_path / "gaia-config"
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", cfg)
    media_dir("image").mkdir(parents=True)
    media_dir("audio").mkdir(parents=True)
    return cfg


def _wav(path: Path, seconds: float = 2.0, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def _float_wav(path: Path, seconds: float, rate: int = 48000, channels: int = 2):
    """A 32-bit float WAV with a LIST chunk first, as music models write."""
    data = b"\x00" * int(seconds * rate * channels * 4)
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * channels * 4, 8, 32)
    lst = b"INFOISFT\x05\x00\x00\x00gaia\x00\x00"  # odd size, padded
    body = (
        b"WAVE"
        + b"LIST"
        + struct.pack("<I", 17)
        + lst[:17]
        + b"\x00"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _can_symlink(tmp_path: Path) -> bool:
    try:
        (tmp_path / "probe-link").symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        return False
    return True


# ── resolve_generated_media ──────────────────────────────────────────────────


def test_a_generated_file_resolves(gaia_dir):
    img = media_dir("image") / "sunset_SD-Turbo_1.png"
    img.write_bytes(PNG)
    assert resolve_generated_media("image", img.name) == img.resolve()
    wav = _wav(media_dir("audio") / "music_1.wav")
    assert resolve_generated_media("audio", wav.name) == wav.resolve()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../secret.png",
        "..\\secret.png",
        "sub/x.png",
        "sub\\x.png",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "C:x.png",
        "x.png:stream",
        "x.png\x00.txt",
        " x.png",
        "x.png ",
    ],
)
def test_names_that_are_not_plain_file_names_are_refused(gaia_dir, name):
    with pytest.raises(MediaNotServable):
        resolve_generated_media("image", name)


def test_an_absolute_path_to_a_real_file_is_refused(gaia_dir, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    with pytest.raises(MediaNotServable):
        resolve_generated_media("image", str(outside))


@pytest.mark.parametrize(
    "kind,name",
    [
        ("image", "x.svg"),
        ("image", "x.txt"),
        ("image", "x.wav"),
        ("audio", "x.png"),
        ("audio", "x.mp3"),
        ("video", "x.png"),
    ],
)
def test_other_suffixes_and_kinds_are_refused(gaia_dir, kind, name):
    for k in ("image", "audio"):
        (media_dir(k) / name).write_bytes(PNG)
    with pytest.raises(MediaNotServable):
        resolve_generated_media(kind, name)


def test_a_folder_or_a_file_in_a_subfolder_is_refused(gaia_dir):
    (media_dir("image") / "dir.png").mkdir()
    (media_dir("image") / "sub").mkdir()
    (media_dir("image") / "sub" / "x.png").write_bytes(PNG)
    for name in ("dir.png", "x.png"):
        with pytest.raises(MediaNotServable):
            resolve_generated_media("image", name)


def test_cold_state_no_folder_yet_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", tmp_path / "fresh")
    with pytest.raises(MediaNotServable, match="no image has been generated"):
        resolve_generated_media("image", "x.png")


def test_a_symlink_in_the_folder_is_refused(gaia_dir, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks need privileges on this machine")
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG)
    (media_dir("image") / "leak.png").symlink_to(secret)
    real = media_dir("image") / "real.png"
    real.write_bytes(PNG)
    # Even a link to another generated file is not served.
    (media_dir("image") / "alias.png").symlink_to(real)
    for name in ("leak.png", "alias.png"):
        with pytest.raises(MediaNotServable, match="symlink"):
            resolve_generated_media("image", name)


def test_a_symlinked_media_folder_still_serves_its_own_files(tmp_path, monkeypatch):
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks need privileges on this machine")
    real_cfg = tmp_path / "real-config"
    (real_cfg / "cache" / "sd" / "images").mkdir(parents=True)
    (real_cfg / "cache" / "sd" / "images" / "a.png").write_bytes(PNG)
    link = tmp_path / "linked-config"
    link.symlink_to(real_cfg, target_is_directory=True)
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", link)
    assert resolve_generated_media("image", "a.png").name == "a.png"


# ── The HTTP endpoint ────────────────────────────────────────────────────────


@pytest.fixture
def client(gaia_dir):
    from gaia.ui.server import create_app

    return TestClient(create_app(db_path=":memory:"))


def test_endpoint_serves_a_generated_image_and_audio_hardened(gaia_dir, client):
    (media_dir("image") / "a.png").write_bytes(PNG)
    wav = _wav(media_dir("audio") / "music_1.wav")

    img = client.get("/api/media/image/a.png")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert img.headers["content-security-policy"] == "sandbox"
    assert img.content == PNG

    snd = client.get("/api/media/audio/music_1.wav")
    assert snd.status_code == 200
    assert snd.headers["content-type"] == "audio/wav"
    assert snd.content == wav.read_bytes()


def test_endpoint_serves_an_encoded_unicode_name(gaia_dir, client):
    (media_dir("image") / "夕焼け_1.png").write_bytes(PNG)
    card = media_card(
        {
            "type": "tool_result",
            "success": True,
            "image_path": str(media_dir("image") / "夕焼け_1.png"),
        }
    )
    assert client.get(card["data"]["src"]).status_code == 200


@pytest.mark.parametrize(
    "url",
    [
        "/api/media/image/..%2Fsecret.png",
        "/api/media/image/..%5Csecret.png",
        "/api/media/image/%2e%2e",
        "/api/media/image/%2Fetc%2Fpasswd",
        "/api/media/image/C%3A%5Csecret.png",
        "/api/media/image/secret.png%00.png",
        "/api/media/image/sub/x.png",
        "/api/media/image/../../secret.png",
        "/api/media/audio/a.png",
        "/api/media/video/a.png",
        "/api/media/image/missing.png",
    ],
)
def test_endpoint_refuses_everything_else(gaia_dir, client, tmp_path, url):
    (tmp_path / "secret.png").write_bytes(PNG)
    (gaia_dir / "cache" / "sd" / "secret.png").write_bytes(PNG)
    (media_dir("image") / "sub").mkdir()
    (media_dir("image") / "sub" / "x.png").write_bytes(PNG)
    (media_dir("image") / "a.png").write_bytes(PNG)
    resp = client.get(url)
    assert resp.status_code == 404, (url, resp.status_code, resp.text)
    assert resp.content != PNG


def test_endpoint_refuses_a_symlink_out(gaia_dir, client, tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks need privileges on this machine")
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG)
    (media_dir("image") / "leak.png").symlink_to(secret)
    assert client.get("/api/media/image/leak.png").status_code == 404


# ── media_card ───────────────────────────────────────────────────────────────


def test_in_process_image_result_becomes_an_image_card(gaia_dir):
    img = media_dir("image") / "sunset_SD-Turbo_1.png"
    img.write_bytes(PNG)
    card = media_card(
        {"type": "tool_result", "success": True, "image_path": str(img)},
        {"prompt": "  a sunset over mountains "},
    )
    assert card == {
        "render": "image",
        "data": {
            "src": "/api/media/image/sunset_SD-Turbo_1.png",
            "path": str(img.resolve()),
            "caption": "a sunset over mountains",
            "alt": "a sunset over mountains",
        },
    }


def test_relayed_audio_result_becomes_an_audio_card_with_length(gaia_dir):
    wav = _float_wav(media_dir("audio") / "music_2.wav", seconds=1.5)
    card = media_card(
        {
            "type": "tool_result",
            "tool": "generate_music",
            "success": True,
            "data": {"summary": "saved", "audio_path": str(wav)},
        },
        {"prompt": "calm piano"},
    )
    assert card["render"] == "audio"
    assert card["data"]["src"] == "/api/media/audio/music_2.wav"
    assert card["data"]["path"] == str(wav.resolve())
    assert card["data"]["caption"] == "calm piano"
    assert card["data"]["duration_seconds"] == 1.5
    assert "alt" not in card["data"]


def test_audio_card_omits_a_length_it_cannot_read(gaia_dir):
    bad = media_dir("audio") / "sfx_1.wav"
    bad.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    card = media_card({"type": "tool_result", "success": True, "audio_path": str(bad)})
    assert card["render"] == "audio"
    assert "duration_seconds" not in card["data"]
    assert "caption" not in card["data"]


@pytest.mark.parametrize("seconds,expected", [(2.0, 2.0), (0.0, 0.0), (61.25, 61.25)])
def test_wav_duration(tmp_path, seconds, expected):
    assert wav_duration_seconds(_wav(tmp_path / "a.wav", seconds)) == expected


def test_wav_duration_of_a_non_wav_is_none(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"ID3\x00not a wav")
    (tmp_path / "b.wav").write_bytes(b"")
    assert wav_duration_seconds(tmp_path / "a.wav") is None
    assert wav_duration_seconds(tmp_path / "b.wav") is None


def test_no_card_for_failed_or_unrelated_results(gaia_dir):
    img = media_dir("image") / "a.png"
    img.write_bytes(PNG)
    assert (
        media_card({"type": "tool_result", "success": False, "image_path": str(img)})
        is None
    )
    assert media_card({"type": "tool_args", "image_path": str(img)}) is None
    assert media_card({"type": "tool_result", "success": True, "summary": "ok"}) is None
    assert media_card({"type": "tool_result", "success": True, "image_path": 7}) is None


def test_no_card_for_a_file_outside_the_media_folder(gaia_dir, tmp_path):
    # Same name as a generated file, but the tool saved somewhere else.
    (media_dir("image") / "a.png").write_bytes(PNG)
    elsewhere = tmp_path / "project"
    elsewhere.mkdir()
    (elsewhere / "a.png").write_bytes(PNG)
    assert (
        media_card(
            {
                "type": "tool_result",
                "success": True,
                "image_path": str(elsewhere / "a.png"),
            }
        )
        is None
    )
    # A path that climbs back into the folder still names the real file.
    climbing = str(media_dir("image") / ".." / "images" / "a.png")
    assert media_card({"type": "tool_result", "success": True, "image_path": climbing})


def test_no_card_for_a_deleted_file(gaia_dir):
    gone = media_dir("audio") / "music_x.wav"
    assert (
        media_card({"type": "tool_result", "success": True, "audio_path": str(gone)})
        is None
    )


def test_the_tools_default_folders_are_the_served_folders(gaia_dir, monkeypatch):
    """A drift guard: the tools save where the endpoint serves from."""
    from gaia.agents.tools import audio_generation_tools
    from gaia.sd import mixin as sd_mixin

    monkeypatch.setattr(audio_generation_tools, "LemonadeClient", lambda **_: None)
    monkeypatch.setattr(
        sd_mixin, "LemonadeClient", lambda **_: SimpleNamespace(base_url="")
    )
    monkeypatch.setattr("gaia.agents.base.tools.tool", lambda *a, **k: (lambda f: f))

    audio = audio_generation_tools.AudioGenerationToolsMixin()
    audio.init_audio_generation()
    assert audio.audio_gen_output_dir == media_dir("audio").resolve()

    sd = sd_mixin.SDToolsMixin()
    sd.init_sd()
    assert Path(sd.sd_output_dir).resolve() == media_dir("image").resolve()


# ── Result keys carry the audio path across the relay ────────────────────────


def test_handler_and_translation_carry_audio_path(gaia_dir):
    from gaia.ui.sse_handler import SSEOutputHandler
    from gaia.ui.sse_translation import CanonicalTranslator

    wav = str(_wav(media_dir("audio") / "music_3.wav"))
    handler = SSEOutputHandler()
    handler._last_tool_name = "generate_music"
    handler.pretty_print_json(
        {"status": "success", "audio_path": wav, "type": "music"}, "Result"
    )
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get())
    result = next(e for e in events if e.get("type") == "tool_result")
    assert result["audio_path"] == wav

    translator = CanonicalTranslator(run_id="r", agent_id="gaia")
    translator.translate({"type": "tool_start", "tool": "generate_music"})
    canonical = translator.translate(result)
    relayed = next(e for e in canonical if e["type"] == "tool_result")
    assert relayed["data"]["audio_path"] == wav


# ── The chat stream: the card is streamed, persisted and reloaded ────────────


def test_relayed_turn_streams_and_persists_the_audio_card(gaia_dir, monkeypatch):
    import gaia.ui._chat_helpers as chat_helpers_module
    import gaia.ui.email_sidecar.daemon_client as daemon_client_module
    import gaia.ui.email_sidecar.relay as relay_module
    from gaia.ui._chat_helpers import _stream_chat_impl
    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import ChatRequest
    from gaia.ui.utils import message_to_response

    wav = _wav(media_dir("audio") / "music_4.wav", seconds=3.0)
    outside = gaia_dir.parent / "elsewhere.wav"
    _wav(outside)

    def _fake_relay_query(handler, proxy, **kw):
        handler._emit({"type": "tool_start", "tool": "generate_music"})
        handler._emit(
            {"type": "tool_args", "tool": "generate_music", "args": {"prompt": "rain"}}
        )
        handler._emit(
            {
                "type": "tool_result",
                "tool": "generate_music",
                "summary": "saved",
                "success": True,
                "data": {"audio_path": str(wav)},
            }
        )
        # A second track saved outside the folder gets no card.
        handler._emit({"type": "tool_start", "tool": "generate_sound_effect"})
        handler._emit(
            {
                "type": "tool_args",
                "tool": "generate_sound_effect",
                "args": {"prompt": "door"},
            }
        )
        handler._emit(
            {
                "type": "tool_result",
                "tool": "generate_sound_effect",
                "summary": "saved",
                "success": True,
                "data": {"audio_path": str(outside)},
            }
        )
        handler._emit({"type": "answer", "content": "Here is your track."})

    class _Handle:
        api_version = "2.4"

        def proxy(self):
            return SimpleNamespace(init=lambda: (200, {}))

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(
        daemon_client_module, "acquire_handle", lambda agent_id="email": _Handle()
    )
    monkeypatch.setattr(relay_module, "relay_query", _fake_relay_query)
    monkeypatch.setattr(chat_helpers_module, "_maybe_update_session_title", _noop)

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="email")
    request = ChatRequest(
        session_id=session["id"], message="make music", stream=True, agent_type="email"
    )
    streamed = []

    async def _drive():
        async for chunk in _stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    streamed.append(json.loads(line[len("data: ") :]))

    asyncio.run(_drive())

    results = [e for e in streamed if e.get("type") == "tool_result"]
    assert [r.get("render") for r in results] == ["audio", None]
    assert results[0]["data"]["src"] == "/api/media/audio/music_4.wav"
    assert results[0]["data"]["caption"] == "rain"
    assert results[0]["data"]["duration_seconds"] == 3.0

    persisted = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"][
        0
    ]
    steps = message_to_response(persisted).agent_steps
    cards = [(s.render, s.data) for s in steps if s.render]
    assert cards == [("audio", results[0]["data"])]
    sfx = [
        s for s in persisted["agent_steps"] if s.get("tool") == "generate_sound_effect"
    ]
    assert "render" not in sfx[0] and "data" not in sfx[0]


def test_module_reads_the_config_dir_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", tmp_path / "a")
    first = generated_media.media_dir("audio")
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", tmp_path / "b")
    assert generated_media.media_dir("audio") != first
    assert os.path.basename(first) == "generations"
