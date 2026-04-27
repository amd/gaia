# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/verify_wheel_dist.py."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

# Load the script as a module without making util/ a package.
_VERIFY_PATH = Path(__file__).resolve().parents[2] / "util" / "verify_wheel_dist.py"
_spec = importlib.util.spec_from_file_location("verify_wheel_dist", _VERIFY_PATH)
verify = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["verify_wheel_dist"] = verify
_spec.loader.exec_module(verify)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_clean_dist(root: Path) -> Path:
    """Create a minimal, valid dist/ tree under root."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div>'
        '<script src="/assets/main-abc123.js"></script></body></html>'
    )
    (dist / "assets" / "main-abc123.js").write_text("const x = 1; console.log(x);")
    (dist / "assets" / "style-abc123.css").write_text("body { margin: 0; }")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    return dist


def _build_wheel(
    dest_dir: Path,
    *,
    bundle_files: dict[str, bytes] | None = None,
    pad_to_compressed_bytes: int | None = None,
    pad_to_uncompressed_bytes: int | None = None,
) -> Path:
    """Build a synthetic wheel containing only the webui dist/ entries."""
    wheel_path = dest_dir / "amd_gaia-0.0.0-py3-none-any.whl"
    if bundle_files is None:
        bundle_files = {
            "gaia/apps/webui/dist/index.html": (
                b'<!doctype html><div id="root"></div>'
            ),
            "gaia/apps/webui/dist/assets/main.js": b"const a = 1;",
            "gaia/apps/webui/dist/assets/style.css": b"body{}",
        }
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in bundle_files.items():
            zf.writestr(name, data)
        if pad_to_uncompressed_bytes is not None:
            # Add highly compressible padding so the uncompressed size is
            # large but the compressed size stays small.
            current = sum(zi.file_size for zi in zf.infolist())
            need = pad_to_uncompressed_bytes - current
            if need > 0:
                zf.writestr("gaia/apps/webui/dist/assets/pad.txt", b"a" * need)
    if pad_to_compressed_bytes is not None:
        # Append incompressible random bytes inside a stored entry until the
        # wheel's on-disk size exceeds the threshold.
        import os as _os

        with zipfile.ZipFile(wheel_path, "a", zipfile.ZIP_STORED) as zf:
            current = wheel_path.stat().st_size
            need = pad_to_compressed_bytes - current
            if need > 0:
                zf.writestr("gaia/apps/webui/dist/assets/pad.bin", _os.urandom(need))
    return wheel_path


# ---------------------------------------------------------------------------
# Directory mode
# ---------------------------------------------------------------------------


def test_directory_mode_happy_path(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    assert verify.verify_directory(dist) == []


def test_directory_mode_missing_dir(tmp_path):
    errors = verify.verify_directory(tmp_path / "does-not-exist")
    assert errors and "does not exist" in errors[0]


def test_directory_mode_empty_dir(tmp_path):
    empty = tmp_path / "dist"
    empty.mkdir()
    errors = verify.verify_directory(empty)
    assert errors and any("empty" in e for e in errors)


def test_directory_mode_rejects_sourcemap_files(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    (dist / "assets" / "main-abc123.js.map").write_text("{}")
    errors = verify.verify_directory(dist)
    assert any("sourcemap" in e and "main-abc123.js.map" in e for e in errors)


def test_directory_mode_rejects_inline_sourcemap_data_uri(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    (dist / "assets" / "main-abc123.js").write_text(
        "const x = 1;\n//# sourceMappingURL=data:application/json;base64,e30="
    )
    errors = verify.verify_directory(dist)
    assert any("inline sourcemap" in e for e in errors)


def test_directory_mode_rejects_dotfiles(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    (dist / ".env").write_text("SECRET=x")
    errors = verify.verify_directory(dist)
    assert any("dotfile" in e and ".env" in e for e in errors)


def test_directory_mode_rejects_node_modules(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    leak = dist / "node_modules" / "foo"
    leak.mkdir(parents=True)
    (leak / "package.json").write_text("{}")
    errors = verify.verify_directory(dist)
    assert any("node_modules" in e for e in errors)


def test_directory_mode_rejects_leaked_vite_value_in_html(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    (dist / "index.html").write_text(
        '<html><head><script>VITE_API_KEY:"sk-real-secret-value"</script>'
        '</head><body><div id="root"></div></body></html>'
    )
    errors = verify.verify_directory(dist)
    assert any("VITE_" in e for e in errors)


def test_directory_mode_rejects_leaked_vite_env_var_set(tmp_path, monkeypatch):
    dist = _make_clean_dist(tmp_path)
    monkeypatch.setenv("VITE_API_KEY", "sk-real-secret")
    errors = verify.verify_directory(dist)
    assert any("VITE_API_KEY" in e and "non-empty value" in e for e in errors)


def test_directory_mode_allows_vite_placeholder_env(tmp_path, monkeypatch):
    dist = _make_clean_dist(tmp_path)
    monkeypatch.setenv("VITE_FOO", "__VITE_FOO__")
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    assert verify.verify_directory(dist) == []


def test_directory_mode_missing_index_html(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    (dist / "index.html").unlink()
    errors = verify.verify_directory(dist)
    assert any("index.html" in e for e in errors)


def test_directory_mode_rejects_unexpected_extension(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    (dist / "assets" / "weird.exe").write_bytes(b"MZ\x00")
    errors = verify.verify_directory(dist)
    assert any("unexpected file extension" in e for e in errors)


# ---------------------------------------------------------------------------
# Wheel mode
# ---------------------------------------------------------------------------


def test_wheel_mode_happy_path(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    wheel = _build_wheel(tmp_path)
    assert verify.verify_wheel(wheel) == []


def test_wheel_mode_missing_assets_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    wheel = _build_wheel(
        tmp_path,
        bundle_files={
            "gaia/apps/webui/dist/index.html": b"<html></html>",
        },
    )
    errors = verify.verify_wheel(wheel)
    assert any("assets/" in e for e in errors)


def test_wheel_mode_missing_index_html(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    wheel = _build_wheel(
        tmp_path,
        bundle_files={"gaia/apps/webui/dist/assets/main.js": b"x = 1;"},
    )
    errors = verify.verify_wheel(wheel)
    assert any("index.html" in e for e in errors)


def test_wheel_mode_no_webui_files(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    wheel = _build_wheel(
        tmp_path,
        bundle_files={"gaia/__init__.py": b""},
    )
    errors = verify.verify_wheel(wheel)
    assert any("frontend bundle missing" in e for e in errors)


def test_wheel_mode_rejects_sourcemap(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    wheel = _build_wheel(
        tmp_path,
        bundle_files={
            "gaia/apps/webui/dist/index.html": b'<div id="root"></div>',
            "gaia/apps/webui/dist/assets/main.js": b"const a = 1;",
            "gaia/apps/webui/dist/assets/main.js.map": b"{}",
        },
    )
    errors = verify.verify_wheel(wheel)
    assert any("sourcemap" in e for e in errors)


def test_wheel_mode_size_hard_fail(tmp_path, monkeypatch):
    """Hard-fail when wheel exceeds 95 MB compressed."""
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    # Build minimal wheel and pad with incompressible random bytes.
    threshold = verify.WHEEL_SIZE_HARD_FAIL_BYTES + 1024 * 1024  # 1 MB over
    wheel = _build_wheel(tmp_path, pad_to_compressed_bytes=threshold)
    errors = verify.verify_wheel(wheel)
    assert any("hard limit" in e for e in errors)


def test_wheel_mode_size_warn_threshold(tmp_path, monkeypatch, capsys):
    """Warn (not fail) when wheel between 50 MB and 95 MB compressed."""
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    threshold = verify.WHEEL_SIZE_WARN_BYTES + 5 * 1024 * 1024  # ~55 MB
    wheel = _build_wheel(tmp_path, pad_to_compressed_bytes=threshold)
    errors = verify.verify_wheel(wheel)
    captured = capsys.readouterr()
    # No size hard-fail expected.
    assert not any("hard limit" in e for e in errors)
    # Warning emitted.
    assert "::warning::" in captured.out


def test_wheel_mode_uncompressed_hard_fail(tmp_path, monkeypatch):
    """Hard-fail when uncompressed size exceeds 250 MB even if compressed is small."""
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    target = verify.WHEEL_UNCOMPRESSED_HARD_FAIL_BYTES + 1024 * 1024
    wheel = _build_wheel(tmp_path, pad_to_uncompressed_bytes=target)
    errors = verify.verify_wheel(wheel)
    assert any("uncompressed" in e for e in errors)


def test_wheel_mode_not_a_zip(tmp_path):
    fake = tmp_path / "fake.whl"
    fake.write_text("not a zip")
    errors = verify.verify_wheel(fake)
    assert any("not a valid zip" in e for e in errors)


def test_wheel_mode_does_not_exist(tmp_path):
    errors = verify.verify_wheel(tmp_path / "missing.whl")
    assert any("does not exist" in e for e in errors)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_directory_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    dist = _make_clean_dist(tmp_path)
    assert verify.main([str(dist)]) == 0


def test_cli_wheel_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    wheel = _build_wheel(tmp_path)
    assert verify.main(["--wheel", str(wheel)]) == 0


def test_cli_rejects_both_args(tmp_path):
    with pytest.raises(SystemExit):
        verify.main([str(tmp_path), "--wheel", str(tmp_path / "x.whl")])


def test_cli_rejects_no_args():
    with pytest.raises(SystemExit):
        verify.main([])
