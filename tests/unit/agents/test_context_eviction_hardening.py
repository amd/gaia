# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The run archive and the marks at their edges: bad disks, tampering, odd text,
hostile handles, tiny and huge windows."""

# pylint: disable=protected-access

import hashlib
import threading

import pytest

from gaia.agents.base.artifacts import ArtifactStore
from gaia.agents.base.context_eviction import ContextEvictor


def _text(n: int, size: int = 6000) -> str:
    return (f"result {n} " + "lorem ipsum dolor sit amet " * (size // 27))[:size]


# ---------------------------------------------------------------------------
# A disk that refuses the write
# ---------------------------------------------------------------------------


def test_an_unwritable_archive_raises_a_value_error_naming_the_run(
    tmp_path, monkeypatch
):
    store = ArtifactStore(archive_dir=tmp_path / "runs" / "r1" / "results")

    def refuse(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("pathlib.Path.write_bytes", refuse)
    with pytest.raises(ValueError, match="Cannot archive tool output.*No space left"):
        store.put_result(_text(1))
    assert not store._items


# ---------------------------------------------------------------------------
# Tampered, corrupted or colliding files
# ---------------------------------------------------------------------------


def test_a_tampered_archive_file_is_refused(tmp_path):
    text = _text(1)
    handle = ArtifactStore(archive_dir=tmp_path).put_result(text)
    (tmp_path / f"{handle}.txt").write_text("you have been pwned", encoding="utf-8")

    reborn = ArtifactStore(archive_dir=tmp_path)
    with pytest.raises(ValueError, match="does not match its handle"):
        reborn.read(handle)


def test_a_corrupted_archive_file_is_refused(tmp_path):
    handle = ArtifactStore(archive_dir=tmp_path).put_result(_text(1))
    (tmp_path / f"{handle}.txt").write_bytes(b"\xff\xfe\x00garbage")

    with pytest.raises(ValueError, match="does not match its handle"):
        ArtifactStore(archive_dir=tmp_path).read(handle)


def test_a_stale_file_under_the_same_name_is_replaced(tmp_path):
    text = _text(1)
    handle = "output_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    (tmp_path / f"{handle}.txt").write_text("wrong", encoding="utf-8")

    assert ArtifactStore(archive_dir=tmp_path).put_result(text) == handle
    assert (tmp_path / f"{handle}.txt").read_text(encoding="utf-8") == text
    assert ArtifactStore(archive_dir=tmp_path).read(handle)["content"] == text[:8000]


# ---------------------------------------------------------------------------
# Unicode and binary-ish output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "日本語の結果 ✓ émoji 🚀 " * 300,
        "bytes decoded leniently: \udcff\udcfe and on " * 200,
        "\x00\x01\x02 control characters \x7f" * 300,
    ],
    ids=["cjk-emoji", "lone-surrogates", "control-chars"],
)
def test_odd_text_round_trips_through_the_archive(tmp_path, text):
    handle = ArtifactStore(archive_dir=tmp_path).put_result(text)
    reborn = ArtifactStore(archive_dir=tmp_path)
    assert reborn.text(handle) == text


# ---------------------------------------------------------------------------
# Hostile handles: never a path into the filesystem
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handle",
    [
        "output_../../secret",
        "output_..\\..\\Windows\\win.ini",
        "../results/output_" + "a" * 32,
        "C:\\Windows\\win.ini",
        "/etc/passwd",
        "output_" + "z" * 32,
        "output_" + "a" * 31,
        "output_" + "ａ" * 32,
        "output_" + "a" * 32 + ".txt",
    ],
)
def test_a_hostile_handle_reads_nothing(tmp_path, monkeypatch, handle):
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    store = ArtifactStore(archive_dir=tmp_path / "results")
    reads = []
    original = type(tmp_path).read_bytes
    monkeypatch.setattr(
        type(tmp_path), "read_bytes", lambda self: reads.append(self) or original(self)
    )

    with pytest.raises(ValueError, match="Unknown output handle"):
        store.read(handle)
    assert store.has(handle) is False
    assert reads == []


def test_another_runs_handle_is_not_reachable(tmp_path):
    handle = ArtifactStore(archive_dir=tmp_path / "run-a").put_result(_text(1))
    with pytest.raises(ValueError, match="Unknown output handle"):
        ArtifactStore(archive_dir=tmp_path / "run-b").read(handle)


# ---------------------------------------------------------------------------
# Memory stays bounded when the disk holds the originals
# ---------------------------------------------------------------------------


def test_memory_copies_are_capped_and_the_disk_serves_the_rest(tmp_path):
    store = ArtifactStore(max_bytes=50_000, archive_dir=tmp_path)
    handles = [store.put_result(_text(n, 9000)) for n in range(40)]

    assert sum(v[2] for v in store._items.values()) <= 50_000
    for n, handle in enumerate(handles):
        assert store.text(handle) == _text(n, 9000)
    assert sum(v[2] for v in store._items.values()) <= 50_000


def test_without_a_disk_the_cap_still_refuses(tmp_path):
    store = ArtifactStore(max_bytes=10_000)
    store.put_result(_text(1, 6000))
    with pytest.raises(ValueError, match="archive is full"):
        store.put_result(_text(2, 6000))


# ---------------------------------------------------------------------------
# Concurrency: chats evicting at the same moment
# ---------------------------------------------------------------------------


def test_concurrent_archiving_keeps_every_file_intact(tmp_path):
    stores = [ArtifactStore(archive_dir=tmp_path) for _ in range(4)]
    texts = [_text(n) for n in range(25)]
    errors = []

    def work(store):
        try:
            for text in texts:
                store.put_result(text)
        except Exception as e:  # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    files = sorted(p for p in tmp_path.iterdir() if p.suffix == ".txt")
    assert len(files) == len(texts)
    reborn = ArtifactStore(archive_dir=tmp_path)
    for text in texts:
        handle = "output_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        assert reborn.text(handle) == text
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


# ---------------------------------------------------------------------------
# Windows from 4K to 128K: the marks always leave room for input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("window", [4096, 8192, 16384, 32768, 65536, 131072])
def test_every_window_has_room_for_input(window):
    evictor = ContextEvictor()
    evictor.begin_turn(window, output_reserve=8192)
    assert 0 < evictor.usable() < window
    assert evictor.output_reserve <= max(window // 4, 1)
    assert evictor.target() < evictor.threshold() < evictor.usable()


@pytest.mark.parametrize("window", [32768, 65536, 131072])
def test_large_windows_keep_the_full_reply_reserve(window):
    evictor = ContextEvictor()
    evictor.begin_turn(window, output_reserve=8192)
    assert evictor.usable() == window - 8192 - 2048


# ---------------------------------------------------------------------------
# Second review: surrogates in counting, small-window headroom, batch floor,
# retention of old runs
# ---------------------------------------------------------------------------


def test_counting_a_result_with_lone_surrogates_does_not_crash():
    from gaia.agents.base.context_eviction import message_tokens

    message = {"role": "tool", "content": "api said \udcff\udcfe " * 500}
    assert message_tokens(message) > 0


def test_the_tokenizer_fallback_counts_lone_surrogates(monkeypatch):
    import tiktoken

    from gaia.agents.base import turn_metrics

    monkeypatch.setattr(
        tiktoken, "get_encoding", lambda _n: (_ for _ in ()).throw(OSError("x"))
    )
    monkeypatch.setattr(turn_metrics, "_TOKENIZER", None)
    try:
        assert turn_metrics.count_tokens("bad \udcff bytes " * 100) > 0
    finally:
        turn_metrics._TOKENIZER = None


@pytest.mark.parametrize("window", [4096, 8192, 16384])
def test_a_small_window_still_has_a_positive_trigger_and_a_real_gap(window):
    evictor = ContextEvictor()
    evictor.begin_turn(window, output_reserve=8192)
    assert 0 < evictor.target() < evictor.threshold() < evictor.usable()


def test_a_tiny_gain_waits_when_the_window_is_not_nearly_full():
    evictor = ContextEvictor(min_batch_tokens=10_000)
    evictor.begin_turn(65536, output_reserve=8192)
    store = ArtifactStore()
    messages = [{"role": "user", "content": ""}]
    for n in range(3):
        messages += [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{n}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "name": "read_file",
                "tool_call_id": f"c{n}",
                "content": [{"type": "text", "text": _text(n, 1500)}],
            },
        ]
    # Pad the user turn to just past the trigger, short of halfway into the headroom.
    need = evictor.threshold() + 300 - evictor.raw_tokens(messages, "s", [])
    messages[0] = {"role": "user", "content": "x" * (need * 3)}
    for _ in range(3):
        evictor.before_call(messages, store, system_prompt="s", tools=[])
    before = evictor.estimate(messages, "s", [])
    assert evictor.threshold() < before < evictor.usable() - evictor.headroom() // 2
    assert evictor.before_call(messages, store, system_prompt="s", tools=[]) is None
    assert not any("Not shown" in str(m.get("content")) for m in messages)


def test_old_runs_are_swept_and_recent_ones_kept(tmp_path):
    import os
    import time

    from gaia.agents.base.artifacts import sweep_old_runs

    runs = tmp_path / "runs"
    old, recent = runs / "old" / "results", runs / "recent" / "results"
    for d in (old, recent):
        d.mkdir(parents=True)
        (d / "output_x.txt").write_text("x", encoding="utf-8")
    ten_days = time.time() - 10 * 86400
    for p in (old, old / "output_x.txt", old.parent):
        os.utime(p, (ten_days, ten_days))

    assert sweep_old_runs(runs, max_age_days=7) == 1
    assert not old.parent.exists() and recent.exists()
