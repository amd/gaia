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
headers, so it runs on any OS and needs no Ubuntu image.

Skips when GitHub is unreachable or ``GAIA_SKIP_NETWORK_TESTS`` is set; with
``GAIA_REQUIRE_RELEASE_API`` set (a Lemonade bump PR) it fails instead.
"""

import hashlib
import io
import os
import struct
import tarfile
import urllib.error
import urllib.request
from typing import Dict, Set, Tuple

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

# The 2026.45.3 x64 build that shipped in v0.25.4-rc13, kept to prove the check catches it.
BROKEN_RELEASE = "2026.45.3"
BROKEN_ASSET = f"lemonade-embeddable-{BROKEN_RELEASE}-ubuntu-x64.tar.gz"
BROKEN_SHA256 = "fa780b91fd76cf1ef8fb2fd9a6d77506470b45dae162b6a1e46882fe2c056783"

_SHT_GNU_VERNEED = 0x6FFFFFFE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(os.environ.get("GAIA_SKIP_NETWORK_TESTS")),
        reason="GAIA_SKIP_NETWORK_TESTS is set",
    ),
]


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


def _download(name: str, version: str, sha256: str) -> bytes:
    url = f"{GITHUB_RELEASE_BASE}/v{version}/{name}"
    request = urllib.request.Request(url, headers={"User-Agent": "GAIA/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        if os.environ.get("GAIA_REQUIRE_RELEASE_API"):
            pytest.fail(f"Could not download {url}: {e}. Re-run the job.")
        pytest.skip(f"Could not download {url}: {e}")
    digest = hashlib.sha256(data).hexdigest()
    assert digest == sha256, f"{url} has SHA-256 {digest}, expected {sha256}"
    return data


def _binaries(archive: bytes) -> Dict[str, bytes]:
    found = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.rsplit("/", 1)[-1]
            if member.isfile() and name in BINARIES:
                found[name] = tar.extractfile(member).read()
    return found


@pytest.mark.parametrize(
    "template",
    sorted(t for (system, _), t in _ASSET_TEMPLATES.items() if system == "Linux"),
)
def test_pinned_linux_lemonade_runs_on_ubuntu_24_04(template):
    """Every pinned Linux binary needs nothing newer than Ubuntu 24.04 ships."""
    name = template.format(version=LEMONADE_VERSION)
    archive = _download(name, LEMONADE_VERSION, EMBEDDABLE_SHA256[name])
    binaries = _binaries(archive)
    assert set(binaries) == set(BINARIES), f"{name} holds {sorted(binaries)}"

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


def test_check_catches_the_build_that_broke_linux():
    """The 2026.45.3 x64 lemond that broke rc13 fails this check."""
    elf = _binaries(_download(BROKEN_ASSET, BROKEN_RELEASE, BROKEN_SHA256))["lemond"]
    bad = too_new(symbol_version_needs(elf))
    assert "libm.so.6: GLIBC_2.43" in bad, bad
    assert "libssl.so.3: OPENSSL_3.2.0" in bad, bad
