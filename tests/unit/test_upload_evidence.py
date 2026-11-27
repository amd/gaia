# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""scripts/testing/upload-evidence.sh, run against a stub rclone that records calls."""

import gzip
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
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

    def _run(*files, remotes="gaia:", with_rclone=True, fail_on="", ref="pr-1"):
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
            [BASH, str(SCRIPT), ref, *[str(f) for f in files]],
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
    shot.write_bytes(_png())

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


@pytest.fixture
def run_ref(run):
    return lambda ref, *files: run(*files, ref=ref)


# ── Scan classification, called in-process ─────────────────────────────────


def _load_scanner():
    spec = importlib.util.spec_from_file_location(
        "scan_evidence", SCRIPT.parent / "scan_evidence.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCANNER = _load_scanner()
LEAK = FAKE_SECRETS["github"]


def _chunk(kind, body):
    crc = struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return struct.pack(">I", len(body)) + kind + body + crc


def _png(*extra_chunks, trailer=b""):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + b"".join(extra_chunks)
        + _chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + _chunk(b"IEND", b"")
        + trailer
    )


def _scan(capsys, *paths, ref=None):
    args = (["--ref", ref] if ref is not None else []) + [str(p) for p in paths]
    code = SCANNER.main(args)
    return code, capsys.readouterr().err


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_real_png_is_cleared_for_review_by_eye(tmp_path, capsys):
    shot = _write(tmp_path, "shot.png", _png(_chunk(b"tEXt", b"Software\x00gaia")))
    code, err = _scan(capsys, shot)
    assert code == 0, err
    assert "review by eye" in err and "blocked" not in err


@pytest.mark.parametrize(
    "chunk",
    [
        _chunk(b"tEXt", b"Comment\x00" + LEAK.encode()),
        _chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(LEAK.encode())),
        _chunk(b"iTXt", b"XML\x00\x01\x00en\x00\x00" + zlib.compress(LEAK.encode())),
        _chunk(b"iTXt", b"XML\x00\x00\x00\x00\x00" + LEAK.encode()),
        _chunk(b"eXIf", b"MM\x00*" + LEAK.encode("utf-16-le")),
        _chunk(b"prVt", b"\x01" + LEAK.encode("utf-16-le")),
    ],
    ids=["tEXt", "zTXt", "iTXt-compressed", "iTXt-plain", "eXIf-utf16", "private"],
)
def test_a_key_in_png_metadata_is_refused(tmp_path, capsys, chunk):
    shot = _write(tmp_path, "shot.png", _png(chunk))
    code, err = _scan(capsys, shot)
    assert code == 1
    assert "shot.png has a provider key prefix" in err
    assert LEAK not in err and "review by eye" not in err


@pytest.mark.parametrize(
    "name, data",
    [
        ("shot.jpg", b"\xff\xd8\xff\xe1\x00\x40Exif\x00\x00" + LEAK.encode()),
        ("shot.jpeg", b"\xff\xd8\xff\xe1\x00\x40" + LEAK.encode("utf-16-le")),
        ("shot.jpg", b"\xff\xd8\xff\x01" + LEAK.encode("utf-16-le")),
        ("anim.gif", b"GIF89a" + b"\x00" * 7 + b"!\xfe" + LEAK.encode()),
        ("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8 + LEAK.encode()),
        ("clip.webm", b"\x1a\x45\xdf\xa3" + LEAK.encode()),
    ],
    ids=["jpg-exif", "jpg-utf16", "jpg-utf16-odd", "gif-comment", "mp4", "webm"],
)
def test_a_key_in_other_media_metadata_is_refused(tmp_path, capsys, name, data):
    path = _write(tmp_path, name, data)
    code, err = _scan(capsys, path)
    assert code == 1
    assert f"{name} has a provider key prefix" in err
    assert LEAK not in err


@pytest.mark.parametrize(
    "data, reason",
    [
        (
            _png(trailer=b"PK\x03\x04" + zlib.compress(LEAK.encode())),
            "data after its end",
        ),
        (_png()[:-6], "truncated"),
        (b"\x89PNG\r\n\x1a\n" + LEAK.encode(), "truncated"),
        (_png(_chunk(b"zTXt", b"c\x00\x00not-deflate")), "corrupt"),
    ],
    ids=["trailing-zip", "truncated", "polyglot-text", "bad-ztxt"],
)
def test_a_png_that_cannot_be_fully_read_is_refused(tmp_path, capsys, data, reason):
    shot = _write(tmp_path, "shot.png", data)
    code, err = _scan(capsys, shot)
    assert code == 1
    assert "shot.png can't be scanned" in err and reason in err
    assert "review by eye" not in err


def test_a_png_text_chunk_bomb_is_refused_not_truncated(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(SCANNER, "MAX_PNG_TEXT_BYTES", 1024)
    bomb = zlib.compress(b"a" * 4096 + LEAK.encode())
    shot = _write(tmp_path, "shot.png", _png(_chunk(b"zTXt", b"c\x00\x00" + bomb)))
    code, err = _scan(capsys, shot)
    assert code == 1 and "too large to scan" in err


@pytest.mark.parametrize(
    "name, data",
    [
        ("notes.png", LEAK.encode()),
        ("notes.jpg", b"\xff\xd8" + LEAK.encode()),
        ("notes.gif", b"GIF8 " + LEAK.encode()),
        ("notes.webp", b"RIFF\x00\x00\x00\x00WEBX" + LEAK.encode()),
        ("notes.mp4", b"\x00\x00\x00\x18ftyX" + LEAK.encode()),
    ],
)
def test_a_file_with_a_media_extension_but_not_media_content_is_not_waved_through(
    tmp_path, capsys, name, data
):
    path = _write(tmp_path, name, data)
    code, err = _scan(capsys, path)
    assert code == 1
    assert "review by eye" not in err
    if 0 not in data and data.isascii():
        assert f"{name} contains a provider key prefix" in err


@pytest.mark.parametrize(
    "data",
    [
        b"PK\x03\x04" + zlib.compress(LEAK.encode()),
        gzip.compress(LEAK.encode()),
        LEAK.encode("utf-16"),
        LEAK.encode("utf-16-le"),
        LEAK.encode("utf-32"),
        "clé ".encode("latin-1") + LEAK.encode(),
        b"%PDF-1.7\n" + zlib.compress(LEAK.encode()),
        b"text\x00" + LEAK.encode(),
    ],
    ids=["zip", "gzip", "utf16-bom", "utf16-le", "utf32", "latin1", "pdf", "nul"],
)
def test_non_media_files_that_are_not_utf8_text_are_refused(tmp_path, capsys, data):
    path = _write(tmp_path, "evidence.bin", data)
    code, err = _scan(capsys, path)
    assert code == 1 and "not UTF-8 text" in err


def test_a_key_in_a_file_name_or_ref_is_refused_without_echoing_it(tmp_path, capsys):
    path = _write(tmp_path, f"{LEAK}.log", b"clean\n")
    code, err = _scan(capsys, path, ref="pr-1")
    assert (
        code == 1
        and "file 1 of 1 has a provider key prefix" in err
        and "in its name" in err
    )
    assert LEAK not in err

    clean = _write(tmp_path, "run.log", b"clean\n")
    code, err = _scan(capsys, clean, ref=LEAK)
    assert code == 1 and "the ref contains a provider key prefix" in err
    assert LEAK not in err


def test_a_symlink_is_refused(tmp_path, capsys):
    target = _write(tmp_path, "credentials", b"clean\n")
    link = tmp_path / "shot.log"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this machine can't create symlinks")
    code, err = _scan(capsys, link)
    assert code == 1 and "is a symlink" in err


def test_an_empty_or_unreadable_list_never_clears(tmp_path, capsys):
    assert SCANNER.main([]) == 2
    assert SCANNER.main(["--ref"]) == 2
    assert SCANNER.main(["--ref", "pr-1"]) == 2
    assert SCANNER.main([str(tmp_path / "missing.log")]) == 2
    assert SCANNER.main([str(tmp_path)]) == 2
    capsys.readouterr()


def test_an_empty_text_file_is_clean(tmp_path, capsys):
    code, _ = _scan(capsys, _write(tmp_path, "empty.log", b""))
    assert code == 0


# ── The helper end to end ───────────────────────────────────────────────────


def test_a_key_in_image_metadata_blocks_the_upload(run, tmp_path):
    chunk = _chunk(b"tEXt", b"c\x00" + LEAK.encode())
    shot = _write(tmp_path, "shot.png", _png(chunk))
    proc, calls = run(shot)
    assert proc.returncode != 0
    assert "shot.png has a provider key prefix" in proc.stderr
    assert LEAK not in proc.stderr + proc.stdout
    assert _uploads(calls) == []


def test_the_scanned_copy_is_what_uploads_even_if_the_file_changes(tmp_path):
    """The stub rclone writes a key into the original at upload time (TOCTOU)."""
    original = _file(tmp_path, "run.log", "clean\n")
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    sent = tmp_path / "sent.log"
    stub = stub_dir / "rclone"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = listremotes ]; then echo gaia:; exit 0; fi\n'
        f'echo "{LEAK}" >> "$ORIGINAL"\n'
        'cat "$4" >> "$SENT"\n',
        newline="\n",
    )
    stub.chmod(0o755)
    path = [str(stub_dir), os.path.dirname(sys.executable), os.environ["PATH"]]
    env = dict(
        os.environ, PATH=os.pathsep.join(path), ORIGINAL=str(original), SENT=str(sent)
    )
    env.pop("R2_REMOTE", None)
    proc = subprocess.run(
        [BASH, str(SCRIPT), "pr-1", str(original)],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert LEAK in original.read_text()
    assert sent.read_text() == "clean\n"


def test_a_symlink_is_refused_before_anything_uploads(run, tmp_path):
    target = _file(tmp_path, "credentials", "clean\n")
    link = tmp_path / "run.log"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this machine can't create symlinks")
    proc, calls = run(link)
    assert proc.returncode != 0 and "is a symlink" in proc.stderr
    assert _uploads(calls) == []


@pytest.mark.parametrize(
    "ref", ["../etc", "pr-1/../../x", ".hidden", "pr..1", "--skip-scan", "", "pr 1"]
)
def test_a_ref_that_could_escape_the_prefix_or_pose_as_a_flag_is_refused(
    run_ref, tmp_path, ref
):
    proc, calls = run_ref(ref, _file(tmp_path, "run.log", "clean\n"))
    assert proc.returncode != 0 and "must be letters, digits" in proc.stderr
    assert _uploads(calls) == []


def test_a_parent_dir_path_is_refused(run, tmp_path):
    (tmp_path / "sub").mkdir()
    proc, calls = run(tmp_path / "sub" / "..")
    assert proc.returncode != 0 and "not a file" in proc.stderr
    assert _uploads(calls) == []


@pytest.mark.parametrize("name", [".env", "a b.log", "x;rm.log", "-rf.log"])
def test_a_name_that_needs_escaping_or_poses_as_a_flag_is_refused(run, tmp_path, name):
    path = tmp_path / name
    try:
        path.write_text("clean\n", encoding="utf-8")
    except OSError:
        pytest.skip(f"this filesystem can't hold the name {name!r}")
    proc, calls = run(path)
    assert proc.returncode != 0 and "rename" in proc.stderr
    assert _uploads(calls) == []


def test_a_key_in_the_ref_blocks_the_upload_without_echoing_it(run_ref, tmp_path):
    proc, calls = run_ref(LEAK, _file(tmp_path, "run.log", "clean\n"))
    assert proc.returncode != 0 and "the ref contains a" in proc.stderr
    assert LEAK not in proc.stderr + proc.stdout
    assert _uploads(calls) == []


def test_no_files_is_a_usage_error(run_ref):
    proc, calls = run_ref("pr-1")
    assert proc.returncode != 0 and "usage:" in proc.stderr
    assert _uploads(calls) == []
