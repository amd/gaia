# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for GAIA's private (embeddable) Lemonade Server."""

import hashlib
import io
import json
import platform
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from gaia.llm.lemonade_embedded import (
    _ASSET_TEMPLATES,
    EMBEDDABLE_SHA256,
    EmbeddedLemonade,
    EmbeddedLemonadeError,
    UnsupportedPlatformError,
    _extract,
    _flatten_single_root,
    asset_name,
    asset_url,
    gaia_home,
)
from gaia.version import LEMONADE_VERSION


@pytest.fixture
def manager(tmp_path):
    """An EmbeddedLemonade rooted in an empty temp directory."""
    return EmbeddedLemonade(home=tmp_path)


def _fake_platform(monkeypatch, system, machine):
    """Pin platform.system()/machine() for one test."""
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)


class TestAssetResolution:
    """Platform -> release asset mapping."""

    @pytest.mark.parametrize(
        "system,machine,expected_suffix",
        [
            ("Windows", "AMD64", "windows-x64.zip"),
            ("Windows", "x86_64", "windows-x64.zip"),
            ("Linux", "x86_64", "ubuntu-x64.tar.gz"),
            ("Linux", "aarch64", "ubuntu-arm64.tar.gz"),
            ("Darwin", "arm64", "macos-arm64.tar.gz"),
        ],
    )
    def test_resolves_each_supported_platform(
        self, monkeypatch, system, machine, expected_suffix
    ):
        _fake_platform(monkeypatch, system, machine)
        assert asset_name("11.8.0") == f"lemonade-embeddable-11.8.0-{expected_suffix}"

    def test_url_points_at_the_versioned_release_tag(self, monkeypatch):
        _fake_platform(monkeypatch, "Windows", "AMD64")
        url = asset_url("11.8.0")
        assert url.endswith("/v11.8.0/lemonade-embeddable-11.8.0-windows-x64.zip")
        assert url.startswith("https://github.com/lemonade-sdk/lemonade/releases/")

    def test_unmapped_architecture_names_the_remedy(self, monkeypatch):
        _fake_platform(monkeypatch, "Linux", "riscv64")
        with pytest.raises(UnsupportedPlatformError) as exc:
            asset_name()
        assert "riscv64" in str(exc.value)
        assert "gaia init" in str(exc.value)

    def test_unpublished_os_arch_pair_names_the_remedy(self, monkeypatch):
        # macOS x86_64: the architecture is known, but no asset is published.
        _fake_platform(monkeypatch, "Darwin", "x86_64")
        with pytest.raises(UnsupportedPlatformError) as exc:
            asset_name()
        assert "gaia init" in str(exc.value)


class TestChecksumPinning:
    """A version bump must not silently drop checksum verification."""

    def test_every_platform_has_a_pinned_digest_for_the_current_version(self):
        missing = [
            template.format(version=LEMONADE_VERSION)
            for template in _ASSET_TEMPLATES.values()
            if template.format(version=LEMONADE_VERSION) not in EMBEDDABLE_SHA256
        ]
        assert not missing, (
            f"LEMONADE_VERSION is {LEMONADE_VERSION} but these assets have no "
            f"pinned SHA-256 in EMBEDDABLE_SHA256: {missing}. Add them from the "
            f"release page before bumping."
        )

    def test_pins_are_well_formed_sha256(self):
        for name, digest in EMBEDDABLE_SHA256.items():
            assert len(digest) == 64, f"{name}: not a sha256 hex digest"
            assert not digest.startswith("sha256:"), f"{name}: strip the prefix"
            int(digest, 16)  # raises if non-hex

    def test_install_refuses_a_version_with_no_pin(self, manager, monkeypatch):
        _fake_platform(monkeypatch, "Windows", "AMD64")
        manager.version = "99.0.0"
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.install()
        assert "No pinned SHA-256" in str(exc.value)

    def test_verify_rejects_a_mismatched_download(self, manager, tmp_path):
        archive = tmp_path / "asset.zip"
        archive.write_bytes(b"not the real asset")
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager._verify(archive, "0" * 64)
        assert "Checksum mismatch" in str(exc.value)

    def test_verify_accepts_a_matching_download(self, manager, tmp_path):
        payload = b"the real asset"
        archive = tmp_path / "asset.zip"
        archive.write_bytes(payload)
        manager._verify(archive, hashlib.sha256(payload).hexdigest())


class TestArchiveSafety:
    """Downloaded archives must not be able to write outside the target."""

    @pytest.mark.parametrize(
        "evil", ["../escape.txt", "a/../../escape.txt", "/tmp/escape.txt"]
    )
    def test_rejects_members_escaping_the_destination(self, tmp_path, evil):
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.addfile(tarfile.TarInfo(evil), io.BytesIO(b""))

        with pytest.raises(EmbeddedLemonadeError) as exc:
            _extract(archive, tmp_path / "dest")
        assert "escapes" in str(exc.value)
        assert not (tmp_path / "escape.txt").exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="creating a symlink needs a privilege CI does not hold; Windows "
        "installs from the .zip asset, never the tarball",
    )
    def test_a_symlink_cannot_redirect_a_later_member_out_of_the_tree(self, tmp_path):
        # Every member name here resolves inside dest while dest is empty, so
        # a name-only check waves them through. Once 'hop' exists as a symlink,
        # the kernel resolves 'hop/../..' through it and the third member lands
        # a directory above dest.
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            holder = tarfile.TarInfo("d")
            holder.type = tarfile.DIRTYPE
            tf.addfile(holder)

            hop = tarfile.TarInfo("d/hop")
            hop.type = tarfile.SYMTYPE
            hop.linkname = "."
            tf.addfile(hop)

            payload = b"owned"
            through = tarfile.TarInfo("d/hop/../../pwned.txt")
            through.size = len(payload)
            tf.addfile(through, io.BytesIO(payload))

        dest = tmp_path / "dest"
        _extract(archive, dest)
        assert not (tmp_path / "pwned.txt").exists()
        assert (dest / "pwned.txt").read_text(encoding="utf-8") == "owned"

    def test_rejects_a_hard_link_pointing_out_of_the_tree(self, tmp_path):
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            link = tarfile.TarInfo("stolen")
            link.type = tarfile.LNKTYPE
            link.linkname = "../../etc/passwd"
            tf.addfile(link)

        with pytest.raises(EmbeddedLemonadeError) as exc:
            _extract(archive, tmp_path / "dest")
        assert "outside" in str(exc.value)

    def test_drops_setuid_and_setgid_bits(self, tmp_path):
        if platform.system() == "Windows":
            pytest.skip("POSIX permission bits are not modelled on Windows")

        archive = tmp_path / "suid.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            member = tarfile.TarInfo("rooted")
            member.mode = 0o4755
            member.size = 0
            tf.addfile(member, io.BytesIO(b""))

        dest = tmp_path / "dest"
        _extract(archive, dest)
        assert not (dest / "rooted").stat().st_mode & (stat.S_ISUID | stat.S_ISGID)

    def test_rejects_a_symlink_pointing_out_of_the_tree(self, tmp_path):
        # Member names alone look innocent here; the link target is the escape.
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            link = tarfile.TarInfo("escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../outside"
            tf.addfile(link)

        with pytest.raises(EmbeddedLemonadeError) as exc:
            _extract(archive, tmp_path / "dest")
        assert "outside" in str(exc.value)

    def test_rejects_device_and_special_files(self, tmp_path):
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            node = tarfile.TarInfo("dev/null")
            node.type = tarfile.CHRTYPE
            tf.addfile(node)

        with pytest.raises(EmbeddedLemonadeError) as exc:
            _extract(archive, tmp_path / "dest")
        assert "device or special file" in str(exc.value)

    def test_accepts_ordinary_members(self, tmp_path):
        archive = tmp_path / "ok.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            for name in ("lemond.exe", "resources/defaults.json"):
                tf.addfile(tarfile.TarInfo(name), io.BytesIO(b""))

        dest = tmp_path / "dest"
        _extract(archive, dest)
        assert (dest / "resources" / "defaults.json").is_file()

    def test_unknown_archive_suffix_is_rejected(self, tmp_path):
        bogus = tmp_path / "asset.7z"
        bogus.write_bytes(b"x")
        with pytest.raises(EmbeddedLemonadeError) as exc:
            _extract(bogus, tmp_path / "dest")
        assert ".zip or .tar.gz" in str(exc.value)

    def test_zip_and_tar_both_unpack(self, tmp_path):
        payload = tmp_path / "payload.txt"
        payload.write_text("hello", encoding="utf-8")

        zip_path = tmp_path / "a.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.write(payload, "payload.txt")
        _extract(zip_path, tmp_path / "outz")
        assert (tmp_path / "outz" / "payload.txt").read_text() == "hello"

        tar_path = tmp_path / "a.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(payload, "payload.txt")
        _extract(tar_path, tmp_path / "outt")
        assert (tmp_path / "outt" / "payload.txt").read_text() == "hello"


class TestPayloadLayout:
    """The published archives wrap everything in one directory."""

    def test_single_wrapper_directory_is_stripped(self, tmp_path):
        inner = tmp_path / "lemonade-embeddable-11.8.0-windows-x64"
        inner.mkdir()
        (inner / "lemond.exe").write_bytes(b"")
        assert _flatten_single_root(tmp_path) == inner

    def test_flat_archive_is_left_alone(self, tmp_path):
        (tmp_path / "lemond.exe").write_bytes(b"")
        (tmp_path / "LICENSE").write_bytes(b"")
        assert _flatten_single_root(tmp_path) == tmp_path


class TestPaths:
    """Layout under GAIA_HOME."""

    def test_cache_and_config_are_shared_across_versions(self, tmp_path):
        old = EmbeddedLemonade(version="11.7.0", home=tmp_path)
        new = EmbeddedLemonade(version="11.8.0", home=tmp_path)
        # Backends cost 141 MB to 4.3 GB; a version bump must not re-download them.
        assert old.cache_dir == new.cache_dir
        assert old.config_dir == new.config_dir
        assert old.dist_dir != new.dist_dir

    def test_gaia_home_honours_the_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GAIA_HOME", str(tmp_path))
        assert gaia_home() == tmp_path.resolve()

    def test_gaia_home_defaults_under_the_user_profile(self, monkeypatch):
        monkeypatch.delenv("GAIA_HOME", raising=False)
        assert gaia_home() == Path.home() / ".gaia"

    def test_daemon_filename_is_platform_correct(self, monkeypatch, tmp_path):
        _fake_platform(monkeypatch, "Windows", "AMD64")
        assert EmbeddedLemonade(home=tmp_path).daemon_path.name == "lemond.exe"
        _fake_platform(monkeypatch, "Linux", "x86_64")
        assert EmbeddedLemonade(home=tmp_path).daemon_path.name == "lemond"

    def test_base_url_carries_the_api_v1_prefix(self):
        # LemonadeClient builds on a base_url that already includes /api/v1.
        assert EmbeddedLemonade.base_url_for(1234) == "http://localhost:1234/api/v1"


class TestConfig:
    """The config lemond is handed."""

    def test_written_config_keeps_the_instance_private(self, manager):
        config = json.loads(manager.write_config().read_text(encoding="utf-8"))
        assert config["broadcast"] is False
        assert config["config_version"] == 2

    def test_does_not_set_no_fetch_executables(self, manager):
        # Setting it true collapses the advertised catalogue from 204 models to
        # the 4 runnable by built-in backends, breaking `gaia download`.
        config = json.loads(manager.write_config().read_text(encoding="utf-8"))
        assert "no_fetch_executables" not in config


class TestStatus:
    """Status reporting and stale-state recovery."""

    def test_cold_state_reports_not_installed_and_not_running(self, manager):
        status = manager.status()
        assert status.installed is False
        assert status.running is False
        assert status.base_url is None

    def test_unreadable_state_file_is_discarded(self, manager):
        manager.root.mkdir(parents=True, exist_ok=True)
        manager.state_path.write_text("{ not json", encoding="utf-8")
        assert manager.status().running is False

    def test_state_for_a_dead_instance_is_cleared(self, manager, monkeypatch):
        manager._write_state(
            {"pid": 1, "port": 65535, "api_key": "k", "version": manager.version}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
        monkeypatch.setattr(
            EmbeddedLemonade, "_daemon_alive", staticmethod(lambda p: False)
        )
        assert manager.status().running is False
        assert not manager.state_path.exists(), "dead instance left a stale state file"

    def test_a_slow_daemon_keeps_its_state(self, manager, monkeypatch):
        # A daemon loading a multi-GB backend can miss the health probe. Losing
        # its pid/port/key here would orphan it with no way to stop it.
        manager._write_state(
            {"pid": 4321, "port": 65535, "api_key": "k", "version": manager.version}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
        monkeypatch.setattr(
            EmbeddedLemonade, "_daemon_alive", staticmethod(lambda p: True)
        )

        status = manager.status()
        assert status.running is False
        assert status.unresponsive_pid == 4321
        assert manager.state_path.exists(), "live daemon lost its state file"

    def test_start_refuses_to_spawn_a_second_daemon(self, manager, monkeypatch):
        manager._write_state(
            {"pid": 4321, "port": 65535, "api_key": "k", "version": manager.version}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
        monkeypatch.setattr(
            EmbeddedLemonade, "_daemon_alive", staticmethod(lambda p: True)
        )

        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.start()
        assert "not answering" in str(exc.value)
        assert "gaia lemonade embedded stop" in str(exc.value)

    def test_stop_reaches_a_daemon_that_stopped_answering(self, manager, monkeypatch):
        manager._write_state(
            {"pid": 4321, "port": 65535, "api_key": "k", "version": manager.version}
        )
        killed = []
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: None)
        monkeypatch.setattr(
            EmbeddedLemonade,
            "_daemon_alive",
            staticmethod(lambda p: not killed),
        )
        monkeypatch.setattr(EmbeddedLemonade, "_kill_tree", staticmethod(killed.append))

        assert manager.stop() is True
        assert killed == [4321], "stalled daemon was never signalled"
        assert not manager.state_path.exists()

    def test_start_refuses_when_a_different_version_is_running(
        self, manager, monkeypatch
    ):
        manager._write_state(
            {"pid": 1, "port": 4321, "api_key": "k", "version": "11.7.0"}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: {"ok": True})
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.start()
        assert "11.7.0" in str(exc.value)
        assert "stop" in str(exc.value)

    def test_live_instance_is_reported_with_its_base_url(self, manager, monkeypatch):
        manager._write_state(
            {"pid": 42, "port": 4321, "api_key": "k", "version": manager.version}
        )
        monkeypatch.setattr(EmbeddedLemonade, "_health", lambda *a, **k: {"ok": True})
        status = manager.status()
        assert status.running is True
        assert status.port == 4321
        assert status.base_url == "http://localhost:4321/api/v1"

    def test_stop_reports_false_when_nothing_runs(self, manager):
        assert manager.stop() is False


class TestUninstall:
    """Removing the private runtime must be complete and process-safe."""

    def test_uninstall_removes_the_runtime_tree(self, manager):
        manager.root.mkdir(parents=True)
        (manager.root / "state.json").write_text("{}", encoding="utf-8")
        (manager.root / "cache").mkdir()
        (manager.root / "cache" / "backend.bin").write_bytes(b"backend")

        assert manager.uninstall() is True
        assert not manager.root.exists()
        assert manager.uninstall() is False

    def test_uninstall_refuses_a_running_instance(self, manager, monkeypatch):
        manager._write_state(
            {"pid": 4321, "port": 13305, "api_key": "k", "version": manager.version}
        )
        monkeypatch.setattr(manager, "_health", lambda *args, **kwargs: {"ok": True})

        with pytest.raises(EmbeddedLemonadeError, match="embedded Lemonade.*running"):
            manager.uninstall()
        assert manager.state_path.exists()

    def test_uninstall_refuses_an_unresponsive_instance(self, manager, monkeypatch):
        manager._write_state(
            {"pid": 4321, "port": 13305, "api_key": "k", "version": manager.version}
        )
        monkeypatch.setattr(manager, "_health", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            EmbeddedLemonade, "_daemon_alive", staticmethod(lambda pid: True)
        )

        with pytest.raises(EmbeddedLemonadeError, match="embedded Lemonade.*running"):
            manager.uninstall()
        assert manager.state_path.exists()


class TestCredentials:
    """The API key must not leak into scrollback, history or CI logs."""

    def test_env_file_holds_both_variables(self, manager):
        path = manager._write_env_file(4321, "s3cret-key")
        body = path.read_text(encoding="utf-8")
        assert "s3cret-key" in body
        assert "http://localhost:4321/api/v1" in body
        assert "LEMONADE_BASE_URL" in body and "LEMONADE_API_KEY" in body

    @pytest.mark.skipif(platform.system() == "Windows", reason="POSIX file modes only")
    def test_env_file_is_owner_readable_only(self, manager):
        path = manager._write_env_file(4321, "s3cret-key")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    @pytest.mark.skipif(platform.system() == "Windows", reason="POSIX file modes only")
    def test_state_file_is_owner_readable_only(self, manager):
        manager._write_state(
            {"pid": 1, "port": 2, "api_key": "k", "version": manager.version}
        )
        assert stat.S_IMODE(manager.state_path.stat().st_mode) == 0o600

    def test_clearing_state_removes_the_credentials_too(self, manager):
        # Credentials naming a dead port are worse than none: whatever grabs
        # that port next would inherit the trust.
        manager._write_state(
            {"pid": 1, "port": 2, "api_key": "k", "version": manager.version}
        )
        manager._write_env_file(2, "k")
        manager._clear_state()
        assert not manager.env_path.exists()
        assert not manager.state_path.exists()

    def test_load_command_names_the_env_file(self, manager):
        assert str(manager.env_path) in manager.env_load_command()


class TestFailureMessages:
    """Errors must name what failed and what to do about it."""

    def test_start_without_install_permission_says_how_to_install(self, manager):
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.start(install_if_missing=False)
        assert "not installed" in str(exc.value)
        assert "gaia lemonade embedded start" in str(exc.value)

    def test_backend_install_without_a_server_says_to_start_one(self, manager):
        with pytest.raises(EmbeddedLemonadeError) as exc:
            manager.install_backend("llamacpp:vulkan")
        assert "gaia lemonade embedded start" in str(exc.value)
