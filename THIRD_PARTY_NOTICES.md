# Third-Party Notices

GAIA is licensed under the MIT License (see [LICENSE.md](LICENSE.md)). Some GAIA
distributions include the shared libraries below, which stay under their own licence. Unless
an entry says otherwise, that is the GNU Lesser General Public License, version 2.1 or later
(LGPL-2.1-or-later); the full text is in [licenses/LGPL-2.1.txt](licenses/LGPL-2.1.txt).
GAIA ships each library unmodified and dynamically linked; the source for each is the
published upstream release linked below.

## Programs fetched at setup or on request

**Needs confirmation by AMD legal before release. This table records what was found; it
is not legal clearance.**

GAIA does not ship or rehost these programs. It uses one already installed, and
otherwise, only when the user agrees, fetches it from the source named here. Each runs
as a separate process under its own licence.

| Program | Version | Licence (SPDX) | Source | How GAIA gets it | Conditions |
|---|---|---|---|---|---|
| Git (MinGit, Windows only) | 2.56.1 | GPL-2.0-only; bundles MSYS2 and other components under their own licences | https://github.com/git-for-windows/git/releases/tag/v2.56.1.windows.1 | Downloads the publisher's zip, checks its pinned SHA-256, unpacks it under `~/.gaia/programs`. Not redistributed by GAIA | GPL source is offered by the publisher at the same release. Confirm that downloading on the user's behalf is not distribution by AMD |
| Tesseract OCR | Whatever the package manager serves (5.4.0 on winget when checked) | Apache-2.0; English trained data Apache-2.0 | Windows: winget `UB-Mannheim.TesseractOCR` (https://github.com/UB-Mannheim/tesseract/wiki); macOS: Homebrew `tesseract` | Runs the user's package manager. Not redistributed by GAIA | The Windows build comes from UB Mannheim, which the Tesseract project points to but does not publish itself. Not version-pinned |
| ffmpeg | Whatever the package manager serves | The winget `Gyan.FFmpeg` package is a GPL-3.0-or-later build with GPL and patent-encumbered codecs enabled; Homebrew's build is also GPL | Windows: winget `Gyan.FFmpeg` (https://www.gyan.dev/ffmpeg/builds/); macOS: Homebrew `ffmpeg` | Runs the user's package manager (`ensure_ffmpeg`). Not redistributed by GAIA | A GPL build, not an LGPL one. Codec patents may apply to the user's use. Decide whether to keep this package or name an LGPL build |
| LibreOffice | Whatever the package manager serves (26.8.3.1 on winget when checked) | MPL-2.0; bundles components under LGPL-3.0-or-later and others | Windows: winget `TheDocumentFoundation.LibreOffice`; macOS: Homebrew cask `libreoffice` (https://www.libreoffice.org/download/) | Runs the user's package manager, and only when the user names it. Not redistributed by GAIA | MPL source is offered by the publisher |

## Lemonade

- **Ships in:** the GAIA desktop app, which carries Lemonade's unmodified embeddable release
  archive. This entry is under the Apache License 2.0, not the LGPL.
- **Copyright:** Copyright the Lemonade contributors.
- **Licence:** https://github.com/lemonade-sdk/lemonade/blob/main/LICENSE
- **Source:** https://github.com/lemonade-sdk/lemonade/releases

## FFmpeg

- **Ships in:** the GAIA desktop installers, inside Electron 45.0.0 (Chromium 156.0.8078.48).
  Electron's own `LICENSES.chromium.html`, installed alongside the app, carries its notice.
- **Copyright:** Copyright (c) the FFmpeg developers.
- **Source:** https://chromium.googlesource.com/chromium/src/+/refs/tags/156.0.8078.48/third_party/ffmpeg/

## libquadmath (GCC)

- **Ships in:** the Linux agent binaries, vendored by the numpy and faiss-cpu Linux
  wheels. The vendored copy is stripped, so its exact GCC version is not recorded.
- **Copyright:** Copyright (C) Free Software Foundation, Inc.
- **Source:** https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libquadmath

## libgfortran (GCC)

- **Ships in:** the Linux agent binaries, vendored by the numpy and faiss-cpu Linux
  wheels. The vendored copy is stripped, so its exact GCC version is not recorded.
- **Licence:** GPL-3.0-or-later WITH GCC-exception-3.1. The GCC Runtime Library Exception,
  in [licenses/GCC-exception-3.1.txt](licenses/GCC-exception-3.1.txt), permits conveying
  it with GAIA under GAIA's own terms.
- **Copyright:** Copyright (C) Free Software Foundation, Inc.
- **Source:** https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libgfortran

## libasound (alsa-lib)

- **Ships in:** the Linux agent binaries, vendored by the sherpa-onnx Linux wheel
  (alsa-lib 1.2.12 in sherpa-onnx 2.0.1).
- **Copyright:** Copyright (c) Jaroslav Kysela, Abramo Bagnara, Takashi Iwai and the ALSA
  project contributors.
- **Source:** https://github.com/alsa-project/alsa-lib/releases/tag/v1.2.12

## libkeyutils (keyutils)

- **Ships in:** the Linux agent binaries, from the Rocky Linux 9 build container's
  OpenSSL/Kerberos (keyutils-libs 1.6.3).
- **Copyright:** Copyright (C) Red Hat, Inc. Written by David Howells.
- **Source:** https://git.kernel.org/pub/scm/linux/kernel/git/dhowells/keyutils.git/tag/?h=v1.6.3
