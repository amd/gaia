# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regenerate the bulk fixtures under ``tiers_files_shell`` and assert their planted values.

The values asserted here are the contract in
``eval/scenarios/GAIA_FIXTURE_VALUES.md`` ("Files and shell"). Run it after any
change to the bulk files; it fails loudly if a planted value drifts.

    python tests/fixtures/gaia/tiers_files_shell/_gen_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

BIG_LINES = 5000
ERROR_LINE = 4812
WARN_EVERY = 135
MANY_FILES = 60
TODO_FILES = (4, 13, 21, 29, 37, 48, 59)
LOWERCASE_DECOYS = (10, 50)
OWNERS = ("Ines", "Marek", "Tomasz", "Yuki", "Farah", "Lior")


def _timestamp(line_no: int) -> str:
    seconds = line_no * 17
    hh, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    return f"2026-09-14T{hh:02d}:{mm:02d}:{ss:02d}Z"


def _big_log() -> str:
    lines = []
    for i in range(1, BIG_LINES + 1):
        if i == ERROR_LINE:
            lines.append(f"{_timestamp(i)} ERROR [ledger] checksum mismatch batch=7731")
        elif i % WARN_EVERY == 0:
            lines.append(f"{_timestamp(i)} WARN [svc-{i % 7}] slow response seq={i}")
        else:
            lines.append(f"{_timestamp(i)} INFO [svc-{i % 7}] heartbeat seq={i}")
    return "\n".join(lines) + "\n"


def _note(n: int) -> str:
    body = [f"Note {n:03d}", f"owner: {OWNERS[n % len(OWNERS)]}", "status: ok"]
    if n == 37:
        body.append("TODO(eval): P0 rotate the staging certificate")
    elif n in TODO_FILES:
        body.append(f"TODO(eval): P2 tidy section {n}")
    if n in LOWERCASE_DECOYS:
        body.append("todo list reviewed, nothing outstanding")
    return "\n".join(body) + "\n"


def main() -> None:
    big = HERE / "big" / "server_2026-09.txt"
    big.parent.mkdir(exist_ok=True)
    big.write_text(_big_log(), encoding="utf-8", newline="\n")

    many = HERE / "many"
    many.mkdir(exist_ok=True)
    for n in range(1, MANY_FILES + 1):
        (many / f"note_{n:03d}.txt").write_text(
            _note(n), encoding="utf-8", newline="\n"
        )

    blob = HERE / "binary" / "blob.bin"
    blob.parent.mkdir(exist_ok=True)
    blob.write_bytes(bytes(range(256)))

    log_lines = big.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 5000
    errors = [i for i, line in enumerate(log_lines, 1) if " ERROR " in line]
    assert errors == [4812], errors
    assert log_lines[4811] == (
        "2026-09-14T22:43:24Z ERROR [ledger] checksum mismatch batch=7731"
    ), log_lines[4811]
    assert sum(" WARN " in line for line in log_lines) == 37

    notes = sorted(many.glob("note_*.txt"))
    assert len(notes) == 60
    with_todo = [p.name for p in notes if "TODO(eval)" in p.read_text("utf-8")]
    assert with_todo == [f"note_{n:03d}.txt" for n in TODO_FILES], with_todo
    p0 = [p.name for p in notes if "TODO(eval): P0" in p.read_text("utf-8")]
    assert p0 == ["note_037.txt"], p0

    assert blob.stat().st_size == 256
    print("tiers_files_shell bulk fixtures regenerated; planted values hold.")


if __name__ == "__main__":
    main()
