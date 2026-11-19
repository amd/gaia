# Third-Party Notices

GAIA is licensed under the MIT License (see [LICENSE.md](LICENSE.md)). Some GAIA
distributions include the shared libraries below, which stay under their own licence. Unless
an entry says otherwise, that is the GNU Lesser General Public License, version 2.1 or later
(LGPL-2.1-or-later); the full text is in [licenses/LGPL-2.1.txt](licenses/LGPL-2.1.txt).
GAIA ships each library unmodified and dynamically linked; the source for each is the
published upstream release linked below.

## Python libraries downloaded at setup

**Needs confirmation by AMD legal before release. This table records what was found; it
is not legal clearance.**

GAIA does not ship or rehost these. On a `pip` or `uv` install, `gaia init` offers to
download them from PyPI (https://pypi.org) into the user's own Python, at the version
pinned in the `data-libraries` extra of `setup.py`. Each stays under its own licence, and
each wheel carries its licence and notice files into the user's `site-packages`. The GAIA
desktop app bundles its own copies of some of them; its build checks those separately.

| Library | Version | Licence (SPDX) | Source | How GAIA gets it | Conditions |
|---|---|---|---|---|---|
| pandas | 2.3.3 | BSD-3-Clause | https://pypi.org/project/pandas/2.3.3/ | Downloaded from PyPI at setup | Keep the copyright and licence notice |
| numpy | 2.2.6 | BSD-3-Clause; bundled parts under 0BSD, MIT, Zlib and CC0-1.0; the Linux wheels vendor libgfortran and libquadmath (see below) | https://pypi.org/project/numpy/2.2.6/ | Downloaded from PyPI at setup | Keep the notices in the wheel |
| matplotlib | 3.10.9 | PSF-based matplotlib licence (no SPDX id); bundles fonts under the Bitstream Vera licence and OFL-1.1 | https://pypi.org/project/matplotlib/3.10.9/ | Downloaded from PyPI at setup | Keep the licence agreement and font notices |
| openpyxl | 3.1.5 | MIT | https://pypi.org/project/openpyxl/3.1.5/ | Downloaded from PyPI at setup | Keep the notice |
| xlsxwriter | 3.3.1 | BSD-2-Clause | https://pypi.org/project/XlsxWriter/3.3.1/ | Downloaded from PyPI at setup | Keep the notice |
| python-docx | 1.2.0 | MIT | https://pypi.org/project/python-docx/1.2.0/ | Downloaded from PyPI at setup | Keep the notice |
| python-pptx | 1.0.2 | MIT | https://pypi.org/project/python-pptx/1.0.2/ | Downloaded from PyPI at setup | Keep the notice |
| pypdf | 7.1.1 | BSD-3-Clause | https://pypi.org/project/pypdf/7.1.1/ | Downloaded from PyPI at setup | Keep the notice |
| pypdfium2 | 5.15.2 | BSD-3-Clause OR Apache-2.0; bundles the PDFium binary (BSD-3-Clause, Apache-2.0 and its dependencies' licences) | https://pypi.org/project/pypdfium2/5.15.2/ | Downloaded from PyPI at setup | Keep the licence files in the wheel |
| reportlab | 5.1.0 | BSD-3-Clause | https://pypi.org/project/reportlab/5.1.0/ | Downloaded from PyPI at setup | Keep the notice |
| duckdb | 1.5.6 | MIT | https://pypi.org/project/duckdb/1.5.6/ | Downloaded from PyPI at setup | Keep the notice |
| pyarrow | 25.0.1 | Apache-2.0; bundles third-party code listed in its `LICENSE.txt` and `NOTICE.txt` | https://pypi.org/project/pyarrow/25.0.1/ | Downloaded from PyPI at setup | Keep `LICENSE.txt` and `NOTICE.txt` |

pip also installs what these libraries themselves require (for example lxml, contourpy,
fonttools, kiwisolver, cycler, pyparsing, pytz, tzdata and et-xmlfile). GAIA does not pin
those: their versions are whatever PyPI resolves on the day, and their licences were not
reviewed for this table. Pillow (MIT-CMU), python-dateutil (Apache-2.0 OR BSD-3-Clause)
and charset-normalizer (MIT) are requirements of GAIA itself and are installed with it.

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
