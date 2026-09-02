# Terminal Hub installers

Native double-click installers for the GAIA terminal hub. Each one puts two
executables on disk and on `PATH`:

- **`gaia-tui`** — the hub itself (built from [`tui/`](../../tui))
- **`gaia-agent`** — the flagship agent the hub spawns as a child process
  (`TransportSubprocess` in `tui/internal/catalog/catalog.go`)

Shipping only the first installs a front end with nothing behind it, which is
why the sidecar is bundled rather than fetched on first run.

| Platform | Builder | Artifact |
| --- | --- | --- |
| Windows x64 | [`nsis/build-setup.sh`](nsis/build-setup.sh) | `gaia-<version>-win-x64-setup.exe` |
| macOS arm64 / x64 | [`macos/build-pkg.sh`](macos/build-pkg.sh) | `gaia-<version>-darwin-<arch>.pkg` |
| Linux x64 | [`linux/build-packages.sh`](linux/build-packages.sh) | `gaia_<version>_amd64.deb`, `gaia-<version>.x86_64.rpm` |

**No installer is built for `win-arm64` or `linux-arm64`** — the flagship agent
publishes no build for either, and [`fetch_sidecar.py`](fetch_sidecar.py) refuses
those platform keys rather than let a half-empty installer get built.

## Staging a payload

All three builders take `--payload <dir>` holding `gaia-tui` and `gaia-agent`
(with `.exe` suffixes on Windows). The Windows and macOS builders read
`LICENSE.md` from that directory too; the Linux one reads it from the repo root
instead, because it already resolves the repo for its `.desktop` file and icon.
Stage all three and every builder is satisfied. Build the first, fetch the
second:

```bash
cd tui && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o ../stage/gaia-tui ./cmd/gaia && cd ..
cp LICENSE.md stage/
python installer/tui/fetch_sidecar.py --platform linux-x64 --out stage
```

`fetch_sidecar.py` verifies the downloaded sidecar's SHA-256 against the digest
committed in `hub/agents/gaia/npm/binaries.lock.json` — never against one served
by the same host as the download, which would only prove the host agrees with
itself. A mismatch deletes the file and exits non-zero, and a placeholder digest
in the lock is refused outright rather than downgraded to that weaker check. Same
contract as `hub/agents/gaia/npm/src/fetch.ts`. There is no unverified path; do
not add one.

The lock ships `PENDING-replace-with-real-sha256` until a release fills it in, so
the installer build fails until it is regenerated with
`hub/agents/gaia/python/packaging/gen_binaries_lock.py`. That is the intended
state — the same placeholder already blocks `npx @amd-gaia/gaia`.

On Windows the payload also needs `gaia-tui.exe` to carry its icon, which the Go
linker only embeds when a resource object sits beside the main package:

```bash
tui/scripts/gen-winres.sh --version 0.23.0     # or: make -C tui winres
python util/check_pe_resources.py bin/gaia-win-x64.exe
```

## Building

CI does all of this in `.github/workflows/release_components.yml`, which also
smoke-tests every installer on a runner that did not build it. Locally, each
builder is self-contained — see the per-platform READMEs for the toolchain each
one needs.
