# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shared hostile-archive test set for ``gaia.utils.archive.safe_extract``."""

import io
import platform
import stat
import tarfile
import zipfile

import pytest

from gaia.utils.archive import ArchiveError, ArchiveSizeError, safe_extract

POSIX_ONLY = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="creating symlinks needs a privilege CI does not hold on Windows",
)


def _tar(path, members):
    """Write a tar.gz from ``(TarInfo, bytes | None)`` pairs."""
    with tarfile.open(path, "w:gz") as tf:
        for info, data in members:
            if data is not None:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            else:
                tf.addfile(info)
    return path


def _file(name, data=b"x", mode=0o644):
    info = tarfile.TarInfo(name)
    info.mode = mode
    return info, data


def _link(name, linkname, kind=tarfile.SYMTYPE):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info, None


def _zip(path, entries):
    """Write a zip from ``(name, data, unix_mode | None)`` triples."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            zf.writestr(info, data)
    return path


def _nothing_written(tmp_path, dest):
    assert not dest.exists() or not any(dest.iterdir())
    assert not (tmp_path / "escaped.txt").exists()


TRAVERSING_NAMES = [
    "../escaped.txt",
    "a/../../escaped.txt",
    "a/../b.txt",
    "..\\escaped.txt",
    "/tmp/escaped.txt",
    "C:/escaped.txt",
    "\\\\server\\share\\escaped.txt",
]


@pytest.mark.parametrize("name", TRAVERSING_NAMES)
def test_tar_rejects_traversing_and_absolute_names(tmp_path, name):
    archive = _tar(tmp_path / "a.tar.gz", [_file("ok.txt"), _file(name)])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError, match="escapes the destination directory"):
        safe_extract(archive, dest)
    _nothing_written(tmp_path, dest)


@pytest.mark.parametrize("name", TRAVERSING_NAMES)
def test_zip_rejects_traversing_names_loudly(tmp_path, name):
    # zipfile would silently rewrite these; a tampered archive must fail instead.
    archive = _zip(tmp_path / "a.zip", [("ok.txt", b"x", None), (name, b"x", None)])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError, match="escapes the destination directory"):
        safe_extract(archive, dest)
    _nothing_written(tmp_path, dest)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_tar_links_refused_by_default(tmp_path, kind):
    archive = _tar(
        tmp_path / "a.tar.gz", [_file("ok.txt"), _link("inner", "ok.txt", kind)]
    )
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError, match="is a link"):
        safe_extract(archive, dest)
    _nothing_written(tmp_path, dest)


def test_zip_symlink_refused_by_default(tmp_path):
    archive = _zip(
        tmp_path / "a.zip", [("evil", b"../../etc/passwd", stat.S_IFLNK | 0o777)]
    )
    with pytest.raises(ArchiveError, match="is a link"):
        safe_extract(archive, tmp_path / "dest")


@pytest.mark.parametrize(
    "linkname", ["../outside", "../../etc/passwd", "/etc/passwd", "d/../../x"]
)
def test_symlink_pointing_outside_refused_even_when_allowed(tmp_path, linkname):
    archive = _tar(tmp_path / "a.tar.gz", [_file("ok.txt"), _link("esc", linkname)])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError, match="outside"):
        safe_extract(archive, dest, allow_links=True)
    _nothing_written(tmp_path, dest)


def test_hardlink_pointing_outside_refused_even_when_allowed(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    archive = _tar(
        tmp_path / "a.tar.gz", [_link("stolen", "../secret.txt", tarfile.LNKTYPE)]
    )
    with pytest.raises(ArchiveError, match="outside"):
        safe_extract(archive, tmp_path / "dest", allow_links=True)


def test_zip_symlink_pointing_outside_refused_even_when_allowed(tmp_path):
    archive = _zip(tmp_path / "a.zip", [("evil", b"../../x", stat.S_IFLNK | 0o777)])
    with pytest.raises(ArchiveError, match="outside"):
        safe_extract(archive, tmp_path / "dest", allow_links=True)


@POSIX_ONLY
def test_symlink_cannot_become_a_path_component_for_a_later_write(tmp_path):
    # 'hop' points at an in-tree dir; if it were created before 'hop/pwned.txt'
    # the write would follow it. Links go last, so the file lands in a real
    # directory 'hop' and the link then fails to replace it.
    archive = _tar(
        tmp_path / "a.tar.gz",
        [
            _link("hop", "sub"),
            _file("hop/pwned.txt", b"owned"),
        ],
    )
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError, match="could not create link"):
        safe_extract(archive, dest, allow_links=True)
    assert (dest / "hop").is_dir() and not (dest / "hop").is_symlink()
    assert not (dest / "sub").exists()


@POSIX_ONLY
def test_in_tree_links_are_recreated_when_allowed(tmp_path):
    archive = _tar(
        tmp_path / "a.tar.gz",
        [
            _file("lib/libfoo.so.1", b"elf"),
            _link("lib/libfoo.so", "libfoo.so.1"),
            _link("bin/hard", "lib/libfoo.so.1", tarfile.LNKTYPE),
        ],
    )
    dest = tmp_path / "dest"
    safe_extract(archive, dest, allow_links=True)
    assert (dest / "lib" / "libfoo.so").is_symlink()
    assert (dest / "lib" / "libfoo.so").read_bytes() == b"elf"
    assert (dest / "bin" / "hard").read_bytes() == b"elf"


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_tar_device_and_fifo_members_refused(tmp_path, kind):
    info = tarfile.TarInfo("dev/node")
    info.type = kind
    archive = _tar(tmp_path / "a.tar.gz", [_file("ok.txt"), (info, None)])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveError, match="device or special file"):
        safe_extract(archive, dest, allow_links=True)
    _nothing_written(tmp_path, dest)


@pytest.mark.parametrize("mode", [stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO])
def test_zip_device_and_fifo_members_refused(tmp_path, mode):
    archive = _zip(tmp_path / "a.zip", [("dev/node", b"", mode | 0o644)])
    with pytest.raises(ArchiveError, match="device or special file"):
        safe_extract(archive, tmp_path / "dest")


@POSIX_ONLY
def test_setuid_setgid_and_sticky_bits_dropped(tmp_path):
    archive = _tar(tmp_path / "a.tar.gz", [_file("rooted", b"", mode=0o7755)])
    dest = tmp_path / "dest"
    safe_extract(archive, dest)
    mode = (dest / "rooted").stat().st_mode
    assert not mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    assert mode & 0o777 == 0o755


@POSIX_ONLY
def test_existing_symlink_in_dest_cannot_redirect_a_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "hop").symlink_to(outside)
    archive = _tar(tmp_path / "a.tar.gz", [_file("hop/escaped.txt")])
    with pytest.raises(ArchiveError, match="escapes the destination directory"):
        safe_extract(archive, dest)
    assert not (outside / "escaped.txt").exists()


def test_ordinary_tar_and_zip_unpack(tmp_path):
    tar_path = _tar(
        tmp_path / "a.tar.gz",
        [_file("./top.txt", b"t"), _file("sub/inner.txt", b"i", mode=0o755)],
    )
    safe_extract(tar_path, tmp_path / "outt")
    assert (tmp_path / "outt" / "top.txt").read_bytes() == b"t"
    assert (tmp_path / "outt" / "sub" / "inner.txt").read_bytes() == b"i"

    zip_path = _zip(
        tmp_path / "a.zip",
        [("dir/", b"", stat.S_IFDIR | 0o755), ("dir/f.txt", b"z", None)],
    )
    safe_extract(zip_path, tmp_path / "outz")
    assert (tmp_path / "outz" / "dir" / "f.txt").read_bytes() == b"z"


def test_kind_overrides_the_suffix(tmp_path):
    archive = _zip(tmp_path / "bundle.download", [("f.txt", b"z", None)])
    safe_extract(archive, tmp_path / "out", kind="zip")
    assert (tmp_path / "out" / "f.txt").read_bytes() == b"z"


def test_unknown_suffix_is_refused(tmp_path):
    bogus = tmp_path / "asset.7z"
    bogus.write_bytes(b"x")
    with pytest.raises(ArchiveError, match="cannot tell the format"):
        safe_extract(bogus, tmp_path / "dest")


def test_corrupt_zip_raises_the_native_error(tmp_path):
    bogus = tmp_path / "asset.zip"
    bogus.write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        safe_extract(bogus, tmp_path / "dest")


# --- Size caps ---------------------------------------------------------------


def _sized_archive(tmp_path, fmt, sizes):
    names = [f"d/f{i}.bin" for i in range(len(sizes))]
    if fmt == "zip":
        return _zip(
            tmp_path / "a.zip",
            [(n, b"x" * s, None) for n, s in zip(names, sizes)],
        )
    return _tar(
        tmp_path / "a.tar.gz", [_file(n, b"x" * s) for n, s in zip(names, sizes)]
    )


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_member_over_cap_refused_before_writing(tmp_path, fmt):
    archive = _sized_archive(tmp_path, fmt, [10, 101])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveSizeError) as excinfo:
        safe_extract(archive, dest, max_member_bytes=100)
    err = excinfo.value
    assert (err.scope, err.member, err.size, err.limit) == (
        "member",
        "d/f1.bin",
        101,
        100,
    )
    assert err.declared
    _nothing_written(tmp_path, dest)


@pytest.mark.parametrize("fmt", ["zip", "tar"])
def test_total_over_cap_refused_before_writing(tmp_path, fmt):
    archive = _sized_archive(tmp_path, fmt, [60, 60])
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveSizeError) as excinfo:
        safe_extract(archive, dest, max_member_bytes=100, max_total_bytes=100)
    err = excinfo.value
    assert (err.scope, err.size, err.limit) == ("total", 120, 100)
    assert err.declared
    _nothing_written(tmp_path, dest)


def test_archive_at_the_caps_unpacks(tmp_path):
    archive = _sized_archive(tmp_path, "zip", [50, 50])
    dest = tmp_path / "dest"
    safe_extract(archive, dest, max_member_bytes=50, max_total_bytes=100)
    assert (dest / "d" / "f1.bin").read_bytes() == b"x" * 50


@pytest.mark.parametrize(
    "caps,scope",
    [({"max_member_bytes": 100}, "member"), ({"max_total_bytes": 100}, "total")],
)
def test_member_larger_than_its_header_is_cut_off(tmp_path, monkeypatch, caps, scope):
    archive = _sized_archive(tmp_path, "zip", [10])
    real_open = zipfile.ZipFile.open

    def lying_open(self, name, *args, **kwargs):
        if getattr(name, "filename", name) == "d/f0.bin":
            return io.BytesIO(b"x" * 1000)
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", lying_open)
    dest = tmp_path / "dest"
    with pytest.raises(ArchiveSizeError) as excinfo:
        safe_extract(archive, dest, **caps)
    err = excinfo.value
    assert (err.scope, err.limit, err.declared) == (scope, 100, False)
    assert err.size > 100
    assert not (dest / "d" / "f0.bin").exists()


@pytest.mark.parametrize("cap", ["max_member_bytes", "max_total_bytes"])
def test_negative_cap_is_refused(tmp_path, cap):
    archive = _sized_archive(tmp_path, "zip", [1])
    with pytest.raises(ValueError, match=cap):
        safe_extract(archive, tmp_path / "dest", **{cap: -1})


# --- Every caller routes through safe_extract --------------------------------


def _skills_cli(archive, dest):
    from gaia.skills.cli import _unpack

    _unpack(archive, dest)


def _skills_install(archive, dest):
    from gaia.skills.install import _unpack_bundle

    _unpack_bundle(archive, dest, name="demo")


def _hub_cpp(archive, dest):
    from gaia.hub.installer import _install_cpp_artifact

    _install_cpp_artifact(archive.read_bytes(), archive.name, dest)


def _lemonade_embedded(archive, dest):
    from gaia.llm.lemonade_embedded import _extract

    _extract(archive, dest)


def _caller_error(caller):
    if caller in (_skills_cli, _skills_install):
        from gaia.skills.errors import SkillValidationError

        return SkillValidationError
    if caller is _hub_cpp:
        from gaia.hub.installer import InstallError

        return InstallError
    from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

    return EmbeddedLemonadeError


@pytest.mark.parametrize(
    "caller",
    [_skills_cli, _skills_install, _hub_cpp, _lemonade_embedded],
    ids=lambda c: c.__name__,
)
def test_callers_refuse_a_zip_entry_zipfile_would_rewrite(tmp_path, caller):
    archive = _zip(tmp_path / "bundle.zip", [("sub/../SKILL.md", b"x", None)])
    with pytest.raises(_caller_error(caller), match="escapes the destination"):
        caller(archive, tmp_path / "dest")
    assert not (tmp_path / "dest" / "sub" / "SKILL.md").exists()


@pytest.mark.parametrize(
    "caller", [_skills_cli, _skills_install, _hub_cpp], ids=lambda c: c.__name__
)
def test_link_refusing_callers_reject_a_zip_symlink(tmp_path, caller):
    archive = _zip(
        tmp_path / "bundle.zip",
        [("SKILL.md", b"x", None), ("evil", b"../../x", stat.S_IFLNK | 0o777)],
    )
    with pytest.raises(_caller_error(caller), match="is a link"):
        caller(archive, tmp_path / "dest")
    assert not (tmp_path / "dest" / "evil").exists()
