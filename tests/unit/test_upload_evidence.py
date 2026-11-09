# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""scripts/testing/upload-evidence.sh, run against a stub rclone that records calls."""

import gzip
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "testing" / "upload-evidence.sh"
)
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

# Assembled at runtime so no token-shaped literal sits in the repo.
FAKE_SECRETS = {
    "github": "ghp_" + "NOTAREALTOKEN" + "0" * 8,
    "openai": "sk-" + "FAKE" * 6,
    "assignment": "ANTHROPIC_API_KEY=" + "not-a-real-key",
    "pem": "-----BEGIN "
    + "PRIVATE KEY-----\n"
    + "NOTAREALKEY" * 3
    + "\n-----END PRIVATE KEY-----",
}
URL_RE = re.compile(
    r"^https://assets\.amd-gaia\.ai/testing/pr-1/\d{8}T\d{6}Z-[0-9a-f]{4}/(\S+)$"
)


@pytest.fixture
def run(tmp_path):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    log = tmp_path / "rclone.log"
    stub = stub_dir / "rclone"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$RCLONE_LOG"\n'
        'if [ "$1" = listremotes ] && [ -n "$STUB_REMOTES" ]; then '
        'echo "$STUB_REMOTES"; fi\n'
        'if [ -n "$STUB_FAIL" ]; then '
        'case "$*" in *"$STUB_FAIL"*) exit 1 ;; esac; fi\n',
        newline="\n",
    )
    stub.chmod(0o755)

    def _run(*files, remotes="gaia:", with_rclone=True, fail_on=""):
        path = [os.path.dirname(sys.executable), os.environ["PATH"]]
        if with_rclone:
            path.insert(0, str(stub_dir))
        env = dict(
            os.environ,
            PATH=os.pathsep.join(path),
            RCLONE_LOG=str(log),
            STUB_REMOTES=remotes,
            STUB_FAIL=fail_on,
        )
        env.pop("R2_REMOTE", None)
        proc = subprocess.run(
            [BASH, str(SCRIPT), "pr-1", *[str(f) for f in files]],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            check=False,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return proc, calls

    return _run


def _file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _uploads(calls):
    return [c for c in calls if c.startswith(("copy", "copyto", "sync", "move"))]


def _blocked(proc):
    return [line for line in proc.stderr.splitlines() if line.startswith("blocked")]


def test_a_file_with_a_fake_key_blocks_the_batch_and_nothing_uploads(run, tmp_path):
    clean = _file(tmp_path, "clean.txt", "agent answered in 3.2s\n")
    leaky = [
        _file(tmp_path, f"{kind}.log", f"env dump\n{secret}\n")
        for kind, secret in FAKE_SECRETS.items()
    ]

    proc, calls = run(clean, *leaky)

    assert proc.returncode != 0
    blocked = _blocked(proc)
    for kind in FAKE_SECRETS:
        assert any(f"{kind}.log contains a " in b for b in blocked), kind
    assert "clean.txt" not in proc.stderr
    assert "nothing was uploaded" in proc.stderr
    for secret in FAKE_SECRETS.values():
        for line in secret.splitlines():
            assert line not in proc.stderr
    assert _uploads(calls) == []
    assert "assets.amd-gaia.ai" not in proc.stdout


def test_files_that_cannot_be_read_as_text_are_refused(run, tmp_path):
    leak = f"env dump\n{FAKE_SECRETS['github']}\n"
    clean = _file(tmp_path, "clean.md", "café answered ✓\n")
    zipped = tmp_path / "trace.zip"
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("trace.log", leak)
    gzipped = tmp_path / "run.log.gz"
    gzipped.write_bytes(gzip.compress(leak.encode()))
    utf16 = tmp_path / "ps51.log"
    utf16.write_bytes(leak.encode("utf-16"))
    utf16le = tmp_path / "nobom.log"
    utf16le.write_bytes(leak.encode("utf-16-le"))
    renamed = _file(tmp_path, "notes.png", leak)

    proc, calls = run(clean, zipped, gzipped, utf16, utf16le, renamed)

    assert proc.returncode != 0
    blocked = _blocked(proc)
    for name in ("trace.zip", "run.log.gz", "ps51.log", "nobom.log"):
        assert any(name in b and "not UTF-8 text" in b for b in blocked), name
    assert any("notes.png contains a " in b for b in blocked)
    assert "clean.md" not in proc.stderr
    assert "review by eye" not in proc.stderr
    assert _uploads(calls) == []


def test_clean_files_upload_and_print_one_public_url_each(run, tmp_path):
    log = _file(tmp_path, "run.log", "loaded model, answered ✓, exited 0\n")
    shot = tmp_path / "chat.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    proc, calls = run(log, shot)

    assert proc.returncode == 0, proc.stderr
    urls = proc.stdout.split()
    assert [URL_RE.match(u).group(1) for u in urls] == ["run.log", "chat.png"]
    prefix = urls[0].rsplit("/", 1)[0].replace("https://assets.amd-gaia.ai/", "")
    assert urls[1].startswith(urls[0].rsplit("/", 1)[0])
    uploads = _uploads(calls)
    assert len(uploads) == 2
    for upload, name in zip(uploads, ["run.log", "chat.png"]):
        assert upload.startswith("copyto --s3-no-check-bucket -- ")
        assert upload.endswith(f"{name} gaia:amd-gaia/{prefix}/{name}")
    assert "review by eye before sharing" in proc.stderr and "chat.png" in proc.stderr


def test_a_failed_upload_still_shows_the_urls_already_uploaded(run, tmp_path):
    first = _file(tmp_path, "first.log", "ok\n")
    second = _file(tmp_path, "second.log", "ok\n")

    proc, calls = run(first, second, fail_on="second.log")

    assert proc.returncode != 0
    assert [URL_RE.match(u).group(1) for u in proc.stdout.split()] == ["first.log"]
    assert len(_uploads(calls)) == 2


def test_missing_remote_is_an_actionable_error_and_nothing_uploads(run, tmp_path):
    proc, calls = run(_file(tmp_path, "run.log", "ok\n"), remotes="other:")

    assert proc.returncode != 0
    assert "remote 'gaia:' is not configured" in proc.stderr
    assert "scripts/video-demo/R2-SETUP.md" in proc.stderr
    assert _uploads(calls) == []


def test_missing_rclone_is_an_actionable_error(run, tmp_path):
    if shutil.which("rclone"):
        pytest.skip("a real rclone is on PATH")
    proc, _ = run(_file(tmp_path, "run.log", "ok\n"), with_rclone=False)

    assert proc.returncode != 0
    assert "rclone not found" in proc.stderr
    assert "scripts/video-demo/R2-SETUP.md" in proc.stderr


def test_duplicate_names_are_refused_before_anything_uploads(run, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    proc, calls = run(_file(a, "run.log", "ok\n"), _file(b, "run.log", "ok\n"))

    assert proc.returncode != 0
    assert "two files are named 'run.log'" in proc.stderr
    assert _uploads(calls) == []
