# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA names a missing CA file instead of blaming the key or the download (#7226).

The bundled Linux lemond reads only /etc/ssl/certs/ca-certificates.crt; Fedora
44's original ca-certificates build has no such file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import responses

from gaia.llm import lemonade_tls
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.lemonade_tls import (
    LEMOND_CA_FILE,
    CaOverlayError,
    ca_store_problem,
    enter_ca_overlay,
    explain_tls_failure,
    find_system_ca_bundle,
    plan_ca_overlay,
)

FEDORA_BUNDLE = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
CURL_CA_ERROR = "CURL error: Problem with the SSL CA cert (path? access rights?)"

DEBIAN = {LEMOND_CA_FILE}
FEDORA_44 = {FEDORA_BUNDLE}
NONE: set = set()


def _fs(files):
    return lambda path: path in files


def test_debian_layout_has_no_problem():
    assert ca_store_problem(platform="linux", exists=_fs(DEBIAN)) is None


def test_fedora_44_layout_names_the_missing_file_and_the_update():
    problem = ca_store_problem(platform="linux", exists=_fs(FEDORA_44))
    assert problem is not None
    assert LEMOND_CA_FILE in problem
    assert FEDORA_BUNDLE in problem
    assert "ca-certificates" in problem
    assert "restart GAIA" in problem


def test_no_bundle_anywhere_names_the_package_to_install():
    problem = ca_store_problem(platform="linux", exists=_fs(NONE))
    assert problem is not None
    assert "no system certificate store" in problem
    assert "Install the ca-certificates package" in problem


def test_remedies_name_no_command_line():
    for files in (FEDORA_44, NONE):
        problem = ca_store_problem(platform="linux", exists=_fs(files))
        assert "`" not in problem and "sudo" not in problem


def test_the_text_fits_a_lemonade_the_user_installed_too():
    # Download errors get this text whichever Lemonade is in use.
    for files in (FEDORA_44, NONE):
        problem = ca_store_problem(platform="linux", exists=_fs(files))
        assert "GAIA's model server" not in problem


def test_other_platforms_are_not_judged():
    for platform in ("win32", "darwin"):
        assert ca_store_problem(platform=platform, exists=_fs(NONE)) is None


def test_probe_order_prefers_the_debian_path():
    assert find_system_ca_bundle(_fs({FEDORA_BUNDLE, LEMOND_CA_FILE})) == (
        LEMOND_CA_FILE
    )
    assert find_system_ca_bundle(_fs(FEDORA_44)) == FEDORA_BUNDLE
    assert find_system_ca_bundle(_fs(NONE)) is None


def test_only_a_ca_failure_is_explained():
    probe = {"platform": "linux", "exists": _fs(FEDORA_44)}
    other = "CURL error: Couldn't resolve host name"
    assert explain_tls_failure(other, **probe) == other
    explained = explain_tls_failure(CURL_CA_ERROR, **probe)
    assert explained.startswith("This PC's certificate store has no")
    assert explained.endswith(f"(Lemonade said: {CURL_CA_ERROR})")


def test_a_ca_failure_on_a_good_store_is_left_alone():
    probe = {"platform": "linux", "exists": _fs(DEBIAN)}
    assert explain_tls_failure(CURL_CA_ERROR, **probe) == CURL_CA_ERROR


@responses.activate
def test_a_download_that_hits_the_ca_error_says_why(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    monkeypatch.setattr(
        lemonade_tls,
        "ca_store_problem",
        lambda **_: ca_store_problem(platform="linux", exists=_fs(FEDORA_44)),
    )
    client = LemonadeClient(host="127.0.0.1", port=1, verbose=False)
    monkeypatch.setattr(client, "ensure_npu_backend", lambda _model: None)
    responses.add(
        responses.POST,
        f"{client.base_url}/pull",
        body=f'event: error\ndata: {{"error":"{CURL_CA_ERROR}"}}\n\n',
        status=200,
        content_type="text/event-stream",
    )
    events = []
    try:
        for event in client.pull_model_stream("nomic-embed-text-v1-GGUF"):
            events.append(event)
    except LemonadeClientError as e:
        raised = str(e)
    else:
        raise AssertionError("the pull should have failed")
    assert LEMOND_CA_FILE in events[-1]["error"]
    assert LEMOND_CA_FILE in raised and CURL_CA_ERROR in raised


# -- the private CA view lemond is started in ------------------------------

BUNDLE_PEM = "-----BEGIN CERTIFICATE-----\nfedora\n-----END CERTIFICATE-----\n"


def _can_symlink(tmp_path: Path) -> bool:
    try:
        os.symlink("x", tmp_path / ".probe")
    except OSError:
        return False
    os.remove(tmp_path / ".probe")
    return True


def _empty(directory: Path) -> None:
    for entry in sorted(directory.rglob("*"), reverse=True):
        if entry.is_dir() and not entry.is_symlink():
            entry.rmdir()
        else:
            entry.unlink()
    directory.rmdir()


def _fedora_44(tmp_path: Path) -> dict:
    """A fake /etc as Fedora 44's original ca-certificates leaves it."""
    etc = tmp_path / "etc"
    certs = etc / "ssl" / "certs"
    certs.mkdir(parents=True)
    (certs / "README").write_text("readme", encoding="utf-8")
    (certs / "hashes").mkdir()
    (certs / "hashes" / "abcd1234.0").write_text("cert", encoding="utf-8")
    bundle = etc / "pki" / "ca-trust" / "extracted" / "pem" / "tls-ca-bundle.pem"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(BUNDLE_PEM, encoding="utf-8")
    return {
        "etc": etc,
        "certs": certs,
        "bundle": str(bundle),
        "ca_file": str(certs / "ca-certificates.crt"),
    }


def _plan(layout, root):
    return plan_ca_overlay(
        root,
        platform="linux",
        ca_file=layout["ca_file"],
        # The Debian path first, as in SYSTEM_CA_BUNDLES: it must never be
        # chosen as its own source.
        bundles=(layout["ca_file"], "/nonexistent/ca.pem", layout["bundle"]),
    )


def test_overlay_gives_lemond_the_fedora_bundle_at_the_debian_path(tmp_path):
    layout = _fedora_44(tmp_path)
    overlay = _plan(layout, tmp_path / "gaia")

    assert overlay.bundle == layout["bundle"]
    assert overlay.mount_point == os.path.realpath(layout["certs"])
    copied = Path(overlay.mirror) / "ca-certificates.crt"
    # The system bundle, byte for byte: nothing is trusted that the OS doesn't.
    assert copied.read_bytes() == Path(layout["bundle"]).read_bytes()
    # Every original entry stays visible through a bind onto its placeholder.
    assert {os.path.basename(src) for src, _ in overlay.binds} == {"README", "hashes"}
    for src, dst in overlay.binds:
        assert os.path.dirname(dst) == overlay.mirror
        assert os.path.isdir(src) == os.path.isdir(dst)
    # The host's store is left exactly as it was.
    assert not os.path.exists(layout["ca_file"])


def test_no_overlay_where_lemond_already_has_its_file(tmp_path):
    layout = _fedora_44(tmp_path)
    Path(layout["ca_file"]).write_text(BUNDLE_PEM, encoding="utf-8")
    assert _plan(layout, tmp_path / "gaia") is None
    assert not (tmp_path / "gaia").exists()


def test_no_overlay_without_any_system_bundle(tmp_path):
    layout = _fedora_44(tmp_path)
    os.remove(layout["bundle"])
    assert _plan(layout, tmp_path / "gaia") is None


def test_no_overlay_off_linux(tmp_path):
    layout = _fedora_44(tmp_path)
    for platform in ("win32", "darwin"):
        planned = plan_ca_overlay(
            tmp_path / "gaia",
            platform=platform,
            ca_file=layout["ca_file"],
            bundles=(layout["bundle"],),
        )
        assert planned is None


def test_overlay_replaces_a_stale_mirror(tmp_path):
    layout = _fedora_44(tmp_path)
    stale = tmp_path / "gaia" / "ca-overlay" / "gone-from-the-host"
    stale.parent.mkdir(parents=True)
    stale.write_text("old", encoding="utf-8")
    overlay = _plan(layout, tmp_path / "gaia")
    assert not stale.exists()
    assert {os.path.basename(s) for s, _ in overlay.binds} == {"README", "hashes"}


def test_missing_certs_directory_covers_its_parent(tmp_path):
    layout = _fedora_44(tmp_path)
    ssl_dir = layout["etc"] / "ssl"
    _empty(layout["certs"])
    (ssl_dir / "openssl.cnf").write_text("cnf", encoding="utf-8")

    overlay = _plan(layout, tmp_path / "gaia")

    assert overlay.mount_point == os.path.realpath(ssl_dir)
    created = Path(overlay.mirror) / "certs" / "ca-certificates.crt"
    assert created.read_text(encoding="utf-8") == BUNDLE_PEM
    assert {os.path.basename(s) for s, _ in overlay.binds} == {"openssl.cnf"}


def test_a_missing_ssl_directory_is_refused_rather_than_covering_etc(tmp_path):
    """Covering all of /etc would pin files like resolv.conf for lemond."""
    layout = _fedora_44(tmp_path)
    _empty(layout["certs"])
    (layout["etc"] / "ssl").rmdir()
    (layout["etc"] / "resolv.conf").write_text("nameserver 1", encoding="utf-8")
    with pytest.raises(CaOverlayError, match="nor its parent exists"):
        _plan(layout, tmp_path / "gaia")
    assert not (tmp_path / "gaia").exists()


def test_symlinked_certs_dir_is_resolved_and_symlinks_are_kept(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("this OS account cannot create symlinks")
    layout = _fedora_44(tmp_path)
    real = layout["etc"] / "pki" / "tls" / "certs"
    real.parent.mkdir(parents=True)
    layout["certs"].rename(real)
    os.symlink(real, layout["certs"])
    os.symlink("/etc/pki/tls/cert.pem", real / "ca-bundle.crt")
    # A dangling Debian-name link must be replaced, not kept dangling.
    os.symlink("/nonexistent/ca.pem", real / "ca-certificates.crt")

    overlay = _plan(layout, tmp_path / "gaia")

    assert overlay.mount_point == os.path.realpath(real)
    mirror = Path(overlay.mirror)
    assert os.readlink(mirror / "ca-bundle.crt") == "/etc/pki/tls/cert.pem"
    assert not (mirror / "ca-certificates.crt").is_symlink()
    assert (mirror / "ca-certificates.crt").read_text(encoding="utf-8") == BUNDLE_PEM
    binds = {os.path.basename(s) for s, _ in overlay.binds}
    assert binds == {"README", "hashes"}


def test_a_dangling_certs_link_is_replaced_by_a_directory(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("this OS account cannot create symlinks")
    layout = _fedora_44(tmp_path)
    _empty(layout["certs"])
    os.symlink(tmp_path / "nowhere", layout["certs"])

    overlay = _plan(layout, tmp_path / "gaia")

    mirror = Path(overlay.mirror)
    assert (mirror / "certs").is_dir() and not (mirror / "certs").is_symlink()
    assert (mirror / "certs" / "ca-certificates.crt").is_file()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux namespaces")
def test_the_child_sees_the_file_and_the_host_does_not(tmp_path):
    """The real system calls, on a fake /etc: no root, no change to the host."""
    layout = _fedora_44(tmp_path)
    overlay = _plan(layout, tmp_path / "gaia")
    probe = (
        "import os,sys;"
        "d=sys.argv[1];"
        "print(open(os.path.join(d,'ca-certificates.crt')).read(),end='');"
        "print(open(os.path.join(d,'README')).read());"
        "print(os.listdir(os.path.join(d,'hashes')));"
        "print(os.getuid(),os.getgid())"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", probe, str(layout["certs"])],
            preexec_fn=enter_ca_overlay(overlay),
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.SubprocessError as e:
        # Only a refused namespace skips; a probe that ran and failed fails.
        pytest.skip(f"this kernel refuses unprivileged user namespaces: {e}")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert "\n".join(lines[:3]) == BUNDLE_PEM.rstrip("\n")
    assert lines[3] == "readme"
    assert lines[4] == "['abcd1234.0']"
    # Same user and group, not root: the namespace adds no privilege.
    assert lines[5] == f"{os.getuid()} {os.getgid()}"
    assert not os.path.exists(layout["ca_file"])


def test_setup_https_paths_never_turn_verification_off():
    """The fix adds a CA file; nothing on setup's HTTPS paths skips the check."""
    import gaia.installer.init_command as init_command
    import gaia.llm.lemonade_client as lemonade_client
    import gaia.llm.lemonade_embedded as lemonade_embedded
    import gaia.tls as gaia_tls
    import gaia.ui.routers.providers as providers

    off = (
        "verify=False",
        "CERT_NONE",
        "check_hostname = False",
        "check_hostname=False",
        "SSL_VERIFYPEER",
        "--insecure",
        "_create_unverified_context",
        "PYTHONHTTPSVERIFY",
    )
    import gaia.hub.catalog as hub_catalog
    import gaia.hub.installer as hub_installer
    import gaia.installer.lemonade_installer as lemonade_installer
    import gaia.llm.providers.fireworks as fireworks

    for module in (
        lemonade_tls,
        lemonade_embedded,
        lemonade_client,
        init_command,
        lemonade_installer,
        providers,
        fireworks,
        hub_catalog,
        hub_installer,
        gaia_tls,
    ):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for marker in off:
            assert marker not in source, f"{marker} in {module.__name__}"


def test_a_c_library_without_unshare_is_a_ca_overlay_error(monkeypatch, tmp_path):
    """macOS or a libc without the call: an actionable error, not AttributeError."""

    class _NoCalls:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(lemonade_tls.ctypes, "CDLL", _NoCalls)
    overlay = lemonade_tls.CaOverlay("/etc/ssl/certs", str(tmp_path), (), "/b.pem")
    with pytest.raises(CaOverlayError, match="unshare"):
        enter_ca_overlay(overlay)


class _Context:
    """Records what truststore loads into a context."""

    def __init__(self):
        self.loaded = []
        self.defaults = False

    def load_verify_locations(self, cafile=None, **_):
        self.loaded.append(cafile)

    def set_default_verify_paths(self):
        self.defaults = True


def test_python_https_finds_fedora_44s_bundle(monkeypatch):
    """Hub installs, the cloud key check and the Lemonade download run in Python
    through truststore; on Fedora 44's layout it must load the system bundle."""
    import ssl
    from types import SimpleNamespace

    openssl = pytest.importorskip("truststore._openssl")
    monkeypatch.setattr(
        ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(cafile=None, capath=None),
    )
    monkeypatch.setattr(openssl.os.path, "isfile", lambda p: p == FEDORA_BUNDLE)
    ctx = _Context()
    with openssl._configure_context(ctx):
        pass
    assert ctx.loaded == [FEDORA_BUNDLE]
    assert not ctx.defaults
