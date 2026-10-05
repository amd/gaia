# Terminal Hub installers

Native double-click installers for the GAIA terminal hub. Each one puts two
executables on disk and on `PATH`:

- **`gaia-tui`** — the hub itself (built from [`tui/`](../../tui))
- **`gaia-agent`** — the flagship agent the hub spawns as a child process
  (`TransportSubprocess` in `tui/internal/catalog/catalog.go`)

Shipping only the first installs a front end with nothing behind it, which is
why the sidecar is bundled rather than fetched on first run.

**On Windows this is the one GAIA installer.** `nsis/gaia-setup.nsi` asks for the
desktop app (Agent UI), the terminal, or both — at least one — and installs the
desktop app by running the unmodified electron-builder
`gaia-agent-ui-<version>-x64-setup.exe` it embeds. That setup stays the app's
owner (files, Installed-apps entry, shortcuts, autostart, uninstaller) because
electron-updater re-runs exactly that file. The terminal keeps the
`GAIA Terminal Hub` identity the terminal-only setup used, so either earlier
installer is upgraded in place. Silent installs take `/S /COMPONENTS=ui,tui`; the
exit codes are listed in `gaia-setup.nsi` and [Install GAIA](../../docs/guides/install.mdx).

| Platform | Builder | Artifact |
| --- | --- | --- |
| Windows x64 | [`nsis/build-setup.sh`](nsis/build-setup.sh) | `gaia-<version>-win-x64-setup.exe` (desktop app and/or terminal) |
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

The Windows setup also bundles IBM Plex Mono for its Windows Terminal profile.
Stage it separately and pass the directory as `--fonts`. Same contract: the
release zip and every face are checked against the digests committed in
[`fonts/fonts.lock.json`](fonts/fonts.lock.json), and the
[fonts README](fonts/README.md) records the pinned version:

```bash
python installer/tui/fetch_fonts.py --out stage/fonts
```

The Windows setup also needs the desktop app's setup, passed as
`--agent-ui-setup`. [`fetch_agent_ui_setup.py`](fetch_agent_ui_setup.py) fetches
it from a named source and checks its name, PE header and size — never "latest":

```bash
python installer/tui/fetch_agent_ui_setup.py --release v0.24.1 --out stage/agent-ui
```

On Windows the payload also needs `gaia-tui.exe` to carry its icon, which the Go
linker only embeds when a resource object sits beside the main package:

```bash
tui/scripts/gen-winres.sh --version 0.23.0     # or: make -C tui winres
python util/check_pe_resources.py bin/gaia-win-x64.exe
```

## Building

CI builds the macOS and Linux packages in `.github/workflows/tui_installers.yml`
and the Windows setup in `.github/workflows/windows_setup.yml`; both smoke-test on
a runner that did not build them, on PRs (via `build_tui.yml`) and on release
(via `release_components.yml`). The Windows smoke test is
[`nsis/smoke-test.ps1`](nsis/smoke-test.ps1). It installs, writes HKCU/HKLM keys,
fonts and shortcuts, and uninstalls — run it only on a throwaway machine. Locally, each
builder is self-contained — see the per-platform READMEs for the toolchain each
one needs.
