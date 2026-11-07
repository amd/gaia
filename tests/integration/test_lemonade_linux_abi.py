# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Check the pinned Linux Lemonade runs on the oldest Ubuntu GAIA supports.

GAIA's embedded engine is the ``lemond`` binary from Lemonade's
``ubuntu-*`` embeddable tarball. Each ELF binary records the newest symbol
version it needs from every system library (``GLIBC_2.39``,
``OPENSSL_3.0.0``, ...). If any of those is newer than what Ubuntu 24.04
ships, the loader refuses the binary and GAIA's engine exits on start: Lemonade
2026.45.3 needed GLIBC_2.43 and OPENSSL_3.2.0, and broke every Linux user
(#6766). Checksums, unit tests and Windows CI all passed.

This test downloads each pinned Linux tarball, verifies it against
``EMBEDDABLE_SHA256``, and reads those requirements straight from the ELF
headers, so it runs on any OS and needs no Ubuntu image. The negative tests
read a fixture built here instead, so they hold even if Lemonade deletes the
broken release.

The engine this pins (2026.44.1) needs ``GLIBC_2.38``: ``lemond`` imports
``__isoc23_strtol`` and friends, the glibc 2.38 forms of the C23 ``strto*``
functions. That is why the docs state Ubuntu 24.04 as the Linux floor, while
the Electron app's own bundled libraries and ``uv`` all stay at 2.17 or below
and would run on 22.04 by themselves. Lowering the floor means lowering this
ceiling only after a pinned engine is measured to fit under it.

The download test skips when GitHub is unreachable or
``GAIA_SKIP_NETWORK_TESTS`` is set; with ``GAIA_REQUIRE_RELEASE_API`` set (a
Lemonade bump PR) it fails instead. The fixture tests touch no network, so the
switch leaves them running.
"""

import hashlib
import http.client
import io
import os
import struct
import tarfile
import urllib.error
import urllib.request
from typing import Dict, Sequence, Set, Tuple

import pytest

from gaia.llm.lemonade_embedded import (
    _ASSET_TEMPLATES,
    EMBEDDABLE_SHA256,
    GITHUB_RELEASE_BASE,
)
from gaia.version import LEMONADE_VERSION

# Newest symbol version Ubuntu 24.04 (noble) provides, per version namespace:
# glibc 2.39, OpenSSL 3.0.13, and GCC 14's libstdc++ and libgcc_s.
UBUNTU_24_04_CEILINGS: Dict[str, Tuple[int, ...]] = {
    "GLIBC": (2, 39),
    "OPENSSL": (3, 0, 13),
    "GLIBCXX": (3, 4, 33),
    "CXXABI": (1, 3, 15),
    "GCC": (14, 0, 0),
}

BINARIES = ("lemond", "lemonade")

#: The requirements of the 2026.45.3 x64 build that shipped in v0.25.4-rc13
#: and broke every Linux user (#6766). The check is aimed at that shape.
BROKEN_NEEDS = {
    "libm.so.6": ("GLIBC_2.43",),
    "libssl.so.3": ("OPENSSL_3.2.0",),
}

_SHT_STRTAB = 3
_SHT_GNU_VERNEED = 0x6FFFFFFE
# Elf64_Shdr: name, type, flags, addr, offset, size, link, info, align, entsize.
_SHDR = struct.Struct("<IIQQQQIIQQ")

pytestmark = pytest.mark.integration


def symbol_version_needs(elf: bytes) -> Dict[str, Set[str]]:
    """Map each needed library to the symbol versions it must provide.

    Reads the ``.gnu.version_r`` section of a little-endian ELF64 binary, the
    same table ``readelf -V`` prints under "Version needs section".
    """
    if elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 1:
        raise ValueError("not a little-endian ELF64 binary")
    (shoff,) = struct.unpack_from("<Q", elf, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", elf, 0x3A)
    sections = [
        struct.unpack_from("<IIQQQQIIQQ", elf, shoff + i * shentsize)
        for i in range(shnum)
    ]

    needs: Dict[str, Set[str]] = {}
    for _, sh_type, _, _, offset, _, link, count, _, _ in sections:
        if sh_type != _SHT_GNU_VERNEED:
            continue
        strtab = sections[link][4]

        def string(at: int, strtab: int = strtab) -> str:
            start = strtab + at
            return elf[start : elf.index(b"\0", start)].decode("ascii")

        for _ in range(count):
            _, aux_count, file_name, aux, next_need = struct.unpack_from(
                "<HHIII", elf, offset
            )
            entry = offset + aux
            for _ in range(aux_count):
                _, _, _, name, next_aux = struct.unpack_from("<IHHII", elf, entry)
                needs.setdefault(string(file_name), set()).add(string(name))
                entry += next_aux
            offset += next_need
    return needs


def elf_declaring(needs: Dict[str, Sequence[str]]) -> bytes:
    """A minimal little-endian ELF64 binary whose version-needs table says *needs*.

    Only the tables :func:`symbol_version_needs` reads are present, so the
    fixture is a few hundred readable bytes instead of a downloaded tarball
    whose release could stop existing.
    """
    strings = [library for library in needs] + [
        version for versions in needs.values() for version in versions
    ]
    at: Dict[str, int] = {}
    strtab = b"\0"
    for string in strings:
        at[string] = len(strtab)
        strtab += string.encode("ascii") + b"\0"

    # Elf64_Verneed, then one Elf64_Vernaux per version, each 16 bytes. A
    # verneed is 16 bytes plus its aux entries, and vn_next is that size.
    verneed = b""
    libraries = list(needs)
    for index, library in enumerate(libraries):
        versions = needs[library]
        size = 16 * (1 + len(versions))
        next_need = size if index + 1 < len(libraries) else 0
        verneed += struct.pack("<HHIII", 1, len(versions), at[library], 16, next_need)
        for position, version in enumerate(versions):
            next_aux = 16 if position + 1 < len(versions) else 0
            verneed += struct.pack("<IHHII", 0, 0, 2 + position, at[version], next_aux)
    verneed_at = 64
    strtab_at = verneed_at + len(verneed)
    shoff = strtab_at + len(strtab)
    # The reader takes the string table from sh_link and the entry count from
    # sh_info, so the version-needs section names section 2, a plain
    # SHT_STRTAB, and counts one entry per needed library.
    sections = [
        _SHDR.pack(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        _SHDR.pack(
            0, _SHT_GNU_VERNEED, 0, 0, verneed_at, len(verneed), 2, len(needs), 1, 16
        ),
        _SHDR.pack(0, _SHT_STRTAB, 0, 0, strtab_at, len(strtab), 0, 0, 1, 0),
    ]
    # ELF64 header as e_ident, type, machine, version, entry, phoff, shoff,
    # flags, ehsize, phentsize, phnum, shentsize, shnum, shstrndx.
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF\x02\x01\x01" + b"\0" * 9,
        3,
        62,
        1,
        0,
        0,
        shoff,
        0,
        64,
        56,
        0,
        64,
        3,
        2,
    )
    return header + verneed + strtab + b"".join(sections)


def too_new(needs: Dict[str, Set[str]]) -> Set[str]:
    """Return the requirements Ubuntu 24.04 cannot satisfy, as ``lib: VERSION``."""
    bad = set()
    for library, versions in needs.items():
        for version in versions:
            namespace, _, number = version.partition("_")
            if namespace not in UBUNTU_24_04_CEILINGS:
                bad.add(
                    f"{library}: {version} (no ceiling for {namespace}; "
                    "add Ubuntu 24.04's to UBUNTU_24_04_CEILINGS)"
                )
                continue
            # Old OpenSSL versions are spelled OPENSSL_1_1_0 rather than 1.1.0.
            parts = number.replace("_", ".").split(".")
            if not all(p.isdigit() for p in parts):
                bad.add(f"{library}: {version} (not a numbered version)")
                continue
            if tuple(int(p) for p in parts) > UBUNTU_24_04_CEILINGS[namespace]:
                bad.add(f"{library}: {version}")
    return bad


_downloads: Dict[Tuple[str, str], bytes] = {}


def _download(name: str, version: str, sha256: str) -> bytes:
    """Fetch *name* once per run; the digest is checked on every call."""
    key = (name, version)
    data = _downloads.get(key)
    if data is None:
        url = f"{GITHUB_RELEASE_BASE}/v{version}/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "GAIA/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
        except urllib.error.HTTPError as e:
            # 404/410 means the pinned release is missing; other HTTP errors may be transient.
            if e.code in (404, 410) or os.environ.get("GAIA_REQUIRE_RELEASE_API"):
                pytest.fail(f"Could not download {url}: HTTP {e.code} {e.reason}")
            pytest.skip(f"Could not download {url}: HTTP {e.code} {e.reason}")
        except (
            urllib.error.URLError,
            ConnectionError,
            TimeoutError,
            http.client.IncompleteRead,
        ) as e:
            if os.environ.get("GAIA_REQUIRE_RELEASE_API"):
                pytest.fail(f"Could not download {url}: {e}. Re-run the job.")
            pytest.skip(f"Could not download {url}: {e}")
        _downloads[key] = data
    digest = hashlib.sha256(data).hexdigest()
    assert digest == sha256, f"{name} has SHA-256 {digest}, expected {sha256}"
    return data


def test_a_missing_release_asset_fails_even_when_network_is_optional(monkeypatch):
    """A 404 says the pin is wrong; it must never turn this ABI gate green."""

    def missing(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid/asset", 404, "Not Found", {}, None
        )

    monkeypatch.delenv("GAIA_REQUIRE_RELEASE_API", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", missing)
    with pytest.raises(pytest.fail.Exception, match="HTTP 404 Not Found"):
        _download("missing.tar.gz", "0.0.0", "0" * 64)


def test_a_transient_release_http_error_still_skips_when_optional(monkeypatch):
    """A transient GitHub error is not proof that the pinned release is wrong."""

    def unavailable(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid/asset", 503, "Service Unavailable", {}, None
        )

    monkeypatch.delenv("GAIA_REQUIRE_RELEASE_API", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    with pytest.raises(pytest.skip.Exception, match="HTTP 503 Service Unavailable"):
        _download("temporarily-unavailable.tar.gz", "0.0.0", "0" * 64)


def _binaries(archive: bytes) -> Dict[str, bytes]:
    """Every ELF file in the tarball, keyed by its path inside the archive.

    Bundled shared libraries are loaded too, so they are held to the same limits.
    """
    found = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            data = tar.extractfile(member).read()
            if data[:4] == b"\x7fELF":
                found[member.name.split("/", 1)[-1]] = data
    return found


@pytest.mark.parametrize(
    "template",
    sorted(t for (system, _), t in _ASSET_TEMPLATES.items() if system == "Linux"),
)
@pytest.mark.skipif(
    bool(os.environ.get("GAIA_SKIP_NETWORK_TESTS")),
    reason="GAIA_SKIP_NETWORK_TESTS is set",
)
def test_pinned_linux_lemonade_runs_on_ubuntu_24_04(template):
    """Every pinned Linux binary needs nothing newer than Ubuntu 24.04 ships."""
    name = template.format(version=LEMONADE_VERSION)
    archive = _download(name, LEMONADE_VERSION, EMBEDDABLE_SHA256[name])
    binaries = _binaries(archive)
    assert set(BINARIES) <= set(binaries), f"{name} holds {sorted(binaries)}"

    bad = {
        binary: sorted(too_new(symbol_version_needs(elf)))
        for binary, elf in sorted(binaries.items())
    }
    bad = {binary: needs for binary, needs in bad.items() if needs}
    assert not bad, (
        f"Lemonade {LEMONADE_VERSION} in {name} needs {bad}, newer than "
        "Ubuntu 24.04 provides, so GAIA's engine "
        "would exit on start for Linux users. Keep the previous "
        "LEMONADE_VERSION and report it upstream at "
        "https://github.com/lemonade-sdk/lemonade/issues."
    )


@pytest.mark.parametrize(
    "needs,bad",
    [
        pytest.param(
            BROKEN_NEEDS,
            {"libm.so.6: GLIBC_2.43", "libssl.so.3: OPENSSL_3.2.0"},
            id="the-build-that-broke-rc13",
        ),
        # What the embedded engine in every release GAIA ships today needs.
        pytest.param(
            {"libc.so.6": ("GLIBC_2.34", "GLIBC_2.38")}, set(), id="pinned-engine"
        ),
        pytest.param(
            {"libfoo.so.1": ("FOO_1.0",)},
            {
                "libfoo.so.1: FOO_1.0 (no ceiling for FOO; add Ubuntu 24.04's "
                "to UBUNTU_24_04_CEILINGS)"
            },
            id="no-ceiling",
        ),
        pytest.param(
            {"libm.so.6": ("GLIBC_private",)},
            {"libm.so.6: GLIBC_private (not a numbered version)"},
            id="unnumbered",
        ),
    ],
)
def test_the_check_reads_a_version_needs_table_the_way_the_real_binaries_do(needs, bad):
    """The fixture goes through the same reading, and same verdict, a tarball does."""
    assert symbol_version_needs(elf_declaring(needs)) == {
        library: set(versions) for library, versions in needs.items()
    }
    assert too_new(symbol_version_needs(elf_declaring(needs))) == bad


def test_check_reads_bundled_libraries_and_skips_non_elf_files():
    """A too-new shared library in the tarball fails even if both binaries pass."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, data in (
            ("pkg/lib/libbundled.so.1", elf_declaring(BROKEN_NEEDS)),
            ("pkg/README.md", b"not an ELF"),
            ("pkg/fake.so", b"\x7fELX not an ELF either"),
        ):
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    found = _binaries(buffer.getvalue())
    assert set(found) == {"lib/libbundled.so.1"}, sorted(found)
    assert "libm.so.6: GLIBC_2.43" in too_new(
        symbol_version_needs(found["lib/libbundled.so.1"])
    )
