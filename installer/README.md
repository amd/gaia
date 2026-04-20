# GAIA Installer

This directory holds everything that goes into producing and running GAIA's install-time artifacts: build scripts, platform-specific installer assets, and release helpers. It was created as part of the [desktop installer plan](../docs/plans/desktop-installer.mdx).

The architectural inspiration for this layout is Lemonade Server's installer tree: <https://github.com/lemonade-sdk/lemonade>. That project keeps a single `installer/` directory as the source of truth for NSIS, DEB, macOS, and Linux packaging, and GAIA follows the same shape so the two codebases stay easy to cross-reference.

## Layout

```
installer/
├── README.md      ← you are here
├── scripts/       build + bootstrap scripts (PowerShell, bash, batch)
├── version/       version normalization and bump helpers (Node)
├── nsis/          Windows NSIS installer config + assets  (populated in Phase C/E)
├── debian/        Debian packaging metadata               (populated in Phase C/E)
├── macos/         DMG layout, entitlements, Info.plist    (populated in Phase E)
└── linux/         .desktop file + AppImage assets         (populated in Phase E)
```

Empty subdirectories (`nsis/`, `debian/`, `macos/`, `linux/`) are placeholders kept via `.gitkeep`. They will be populated when Phases C and E of the [desktop installer plan](../docs/plans/desktop-installer.mdx) land.

## `scripts/` — bootstrap and build scripts

| Script | Purpose |
|---|---|
| `install.ps1` | One-shot Windows installer pulled via `irm https://amd-gaia.ai/install.ps1 \| iex` |
| `install.sh` | One-shot Linux/macOS installer pulled via `curl ... \| bash` |
| `build-ui-installer.ps1` / `.sh` | Build the Electron Agent UI installer locally |
| `start-agent-ui.ps1` / `.sh` | Launch the Agent UI (backend + frontend) during development |
| `start-lemonade.ps1` / `.sh` / `.bat` | Launch a local Lemonade Server for development and CI |

### Building the Agent UI installer locally

The current local build path is the `build-ui-installer` scripts. From the repo root:

```bash
# Linux / macOS
./installer/scripts/build-ui-installer.sh

# Windows (PowerShell)
.\installer\scripts\build-ui-installer.ps1
```

This wraps `npm run make` in `src/gaia/apps/webui/`, which today uses `electron-forge`.

> **Note:** Phase C of the desktop installer plan migrates the Agent UI from
> `electron-forge` to `electron-builder` and introduces `npm run package:{win,mac,linux}`.
> Once that phase lands, the commands above will move to the `package:*` npm scripts.
> See [§7 Phase C](../docs/plans/desktop-installer.mdx) for the migration plan.

## `version/` — version helpers

| Script | Purpose |
|---|---|
| `bump-ui-version.mjs` | Sync `src/gaia/apps/webui/package.json` with `src/gaia/version.py`. Run with `--check` in CI. |
| `release-ui.mjs` | Release helper that tags and publishes a new Agent UI version. |

Typical usage:

```bash
# Verify package.json matches version.py (used in CI)
node installer/version/bump-ui-version.mjs --check

# Sync package.json to match version.py
node installer/version/bump-ui-version.mjs

# Full release flow
node installer/version/release-ui.mjs
```

## Future content (tracked in the plan)

- **`nsis/`** — `installer.nsh`, `installer-banner.bmp`, `installer-sidebar.bmp`, `icon.ico` (Phases C + E)
- **`debian/`** — DEB control files and postinstall/postrm hooks (Phase C)
- **`macos/`** — `Info.plist`, `entitlements.mac.plist`, `dmg-background.png`, `icon.icns` (Phase E)
- **`linux/`** — `gaia-ui.desktop`, `gaia-ui.png` for AppImage/DEB (Phase E)
- **`scripts/after-pack.cjs`** — `electron-builder` `afterPack` hook for locale pruning (Phase C)
- **`version/normalize.mjs`** — 4-part → 3-part SemVer normalizer (Phase C)

See [`docs/plans/desktop-installer.mdx`](../docs/plans/desktop-installer.mdx) §8 for the full file-by-file layout.
