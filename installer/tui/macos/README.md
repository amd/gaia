# macOS installer for the GAIA terminal hub

Builds `gaia-<version>-darwin-<arch>.pkg`, which installs two command-line tools:

| Path | Mode |
| --- | --- |
| `/usr/local/bin/gaia-tui` | 0755 |
| `/usr/local/bin/gaia-agent` | 0755 |
| `/usr/local/share/doc/gaia-tui/LICENSE.md` | 0644 |

Nothing else is written, and the package is constrained to the system volume —
`/usr/local/bin` is not relocatable, so there is no destination to choose.

No directory entry for `/usr`, `/usr/local`, `/usr/local/bin` or
`/usr/local/share` is shipped. Installing one would reset that directory to
`root:wheel`, which breaks `brew` on an Intel Mac where Homebrew owns
`/usr/local` as the user. That is why there are **two** component packages
rather than one: each is rooted at a staging directory holding only its own
files, so the shared parents are the `--install-location` and never payload.

| Component | Identifier | Install location |
| --- | --- | --- |
| binaries | `ai.amd.gaia.terminal-hub` | `/usr/local/bin` |
| licence | `ai.amd.gaia.terminal-hub.docs` | `/usr/local/share/doc/gaia-tui` |

`productbuild` combines them into the single `.pkg` a user double-clicks. Both
BOMs are read back after `pkgbuild` and the build fails if either the expected
files are missing or a `/usr` entry appears. The installer creates a missing
install location itself, so nothing has to `mkdir` it first.

## Uninstall it

A `.pkg` ships no uninstaller — that is the macOS convention. Remove the files,
then forget both receipts (`--forget` drops the receipt only; it deletes nothing,
so the order matters):

```bash
sudo rm -f /usr/local/bin/gaia-tui /usr/local/bin/gaia-agent
sudo rm -rf /usr/local/share/doc/gaia-tui
sudo pkgutil --forget ai.amd.gaia.terminal-hub
sudo pkgutil --forget ai.amd.gaia.terminal-hub.docs
```

`~/.gaia` — chats, documents, memory, config — is never touched.

## Build it

```bash
installer/tui/macos/build-pkg.sh \
  --version 1.4.0 \
  --arch arm64 \
  --payload ./stage/darwin-arm64 \
  --out ./dist
```

All four arguments are required.

- `--arch` is `arm64` or `x64`.
- `--payload` is a directory that already contains two executables named
  `gaia-tui` and `gaia-agent` (no extension) plus `LICENSE.md`. The script copies
  them; it does not build them. A missing `LICENSE.md` is a hard error — every
  package GAIA ships carries the licence.
- `--out` is created if it does not exist. The result is
  `<out>/gaia-<version>-darwin-<arch>.pkg`.

Requires macOS with the Xcode command line tools (`xcode-select --install`) for
`pkgbuild` and `productbuild`. Two build-host floors apply, both above the
macOS 11 floor the *installed* package enforces:

- `pkgbuild --min-os-version 11.0` makes the component package unreadable to a
  `productbuild` older than 11, so the build host must be macOS 11+.
- Signing additionally needs macOS 12+, where `plutil` gained JSON parsing and
  the `raw` extract format used to read the notarization status.

## Signing and notarization

Four environment variables control this, and they are **all-or-nothing**:

| Variable | Used for |
| --- | --- |
| `APPLE_INSTALLER_IDENTITY` | `productbuild --sign` — the full *Developer ID Installer* identity name, e.g. `Developer ID Installer: Advanced Micro Devices, Inc. (ABCDE12345)` |
| `APPLE_ID` | `notarytool --apple-id` |
| `APPLE_APP_SPECIFIC_PASSWORD` | `notarytool --password` — an app-specific password, not the Apple ID password |
| `APPLE_TEAM_ID` | `notarytool --team-id` |

- **All four set** → the package is signed, submitted to Apple's notary service,
  and stapled. If signing or notarization fails, the build fails and the `.pkg`
  is deleted. It is never downgraded to an unsigned artifact that looks like a
  success.
- **None set** → an unsigned package, plus a warning on stderr. Users will see
  Gatekeeper's "unidentified developer" prompt.
- **Some set** → hard error naming exactly which are missing. A half-configured
  keychain quietly emitting an unsigned artifact is the failure mode this
  prevents.

List the installer identities in your keychain with:

```bash
security find-identity -v | grep 'Developer ID Installer'
```

Note that installer certificates do **not** appear under
`security find-identity -p codesigning`.

### The payload binaries must already be signed — and CI does not do this yet

**Setting the four variables above is not enough to turn signing on.** Apple's
notary service rejects a package containing unsigned or ad-hoc-signed
executables, so `build-pkg.sh` refuses to submit one — and a *Developer ID
Application* certificate (a different certificate from the *Installer* one in
the table above) is what signs them. `release_components.yml` imports no
keychain and signs no binary, so today it passes `--allow-unsigned` and every
published `.pkg` is unsigned.

Enabling signing therefore needs three things together, not one: the four
variables, a Developer ID **Application** certificate imported into the runner's
keychain, and a workflow step that codesigns both binaries before they are
staged. Wiring only the secrets turns the macOS leg red.

Codesign the Go binaries *before* staging them:

```bash
codesign --force --options runtime --timestamp \
  --sign "Developer ID Application: … ($APPLE_TEAM_ID)" \
  ./stage/darwin-arm64/gaia-tui ./stage/darwin-arm64/gaia-agent
```

`--options runtime` (the hardened runtime) is required for notarization.

`notarytool submit --wait` exits 0 even when Apple returns `Invalid` — it reports
*completion*, not acceptance. The build parses the JSON status instead of
trusting the exit code, and prints the notary log when the status is anything
other than `Accepted`.

## Layout

| File | Role |
| --- | --- |
| `build-pkg.sh` | Entry point; the only thing CI calls |
| `distribution.xml` | `productbuild --distribution` template; `@VERSION@`, `@HOST_ARCHITECTURES@`, `@PKG_IDENTIFIER@`, `@DOCS_IDENTIFIER@`, `@COMPONENT_PKG@` and `@DOCS_COMPONENT_PKG@` are substituted at build time |
| `scripts/postinstall` | Runs with the binaries component. Verifies both binaries landed and are executable, then prints their paths. A non-zero exit here is surfaced by Installer.app |

Minimum OS: macOS 11.

## Architecture gating

A script-free distribution has no real architecture check, so `hostArchitectures`
stands in for one — it selects the architecture Installer.app itself runs as, and
each package pins it to the architecture of its own payload:

- The **arm64** package will not open on Intel, which is the outcome worth
  having: that payload could not execute there anyway.
- The **x86_64** package asks for Rosetta on Apple silicon. Its payload needs
  Rosetta regardless, so the prompt is honest rather than gratuitous.

Blocking the x86_64 package on Apple silicon outright would need a JavaScript
`installation-check`, which in turn means dropping `require-scripts="false"`.
Not worth it while the download page serves the right architecture.
