# GAIA Binary Distribution — Phase 1 Implementation Plan

> **Partly superseded since this was written (2026-07-25).** Two changes landed
> that overlap it, so re-verify current state before starting a task:
> - #2522 added a `build-tui` job to `publish.yml` that attaches the six targets
>   to the GitHub Release with a `SHA256SUMS`, plus an `install_tui()` in
>   `installer/scripts/install.sh` — which is Linux-only, so the darwin builds
>   still have no install path.
> - #2530 publishes the hub as a `type: component` package to the R2 catalog
>   (`hub/components/terminal-hub/gaia-agent.yaml`), which is the artifact-store
>   half this plan describes.
>
> Still true as of writing this note: no release carries a `gaia-<os>-<arch>`
> asset, the hub catalog holds only `email`, `gaia tui` exits 2, and
> `amd-gaia.ai/install.sh` returns 200 but installs the Python side only — so
> the plan's premise (the binary is not obtainable) holds even though some
> plumbing exists.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gaia` — the Go binary — installable in one command from a published, hash-verified artifact, and take the `gaia` name without breaking a single command anyone runs today.

**Architecture:** `build_tui.yml` already cross-compiles six targets and throws them away as 14-day CI artifacts. This plan changes their destination: a generated manifest (`platform → {sha256, size, urls[]}`) published to the hub artifact store with GitHub Releases as a same-hash mirror, consumed by a ~100-line POSIX shell / PowerShell installer served from the URL the website has advertised (and 404'd) for months. The rename rides along, made safe by a forwarding shim in cobra's root command that hands unknown subcommands to `gaia-dev` rather than dead-ending.

**Tech Stack:** Go 1.x + cobra (`tui/`), GitHub Actions, Python `setup.py` console_scripts, POSIX sh + PowerShell, Astro (`website/`), pytest, `go test`.

## Global Constraints

- **No Claude attribution anywhere** — not in commits, code comments, docs, or PR text. No `Co-Authored-By: Claude` trailers.
- **No silent fallbacks.** A hash mismatch, an exhausted mirror list, or an unsupported platform is a hard error naming what failed, what to do, and where to look. Never "use it anyway."
- **A shipped remedy command must actually parse.** Run every command string you put in user-facing text before committing it. Enforced by `tests/unit/test_remedy_commands_are_runnable.py`.
- **Never judge a command's success through a pipe.** Use `cmd > /tmp/out.txt 2>&1; echo "exit=$?"` and check `$?` on its own line.
- **`amd-gaia.ai` links inside `src/gaia/` must keep the `/docs/` prefix** (enforced by `tests/unit/test_amd_gaia_urls.py`). Exception: `/install.sh` and `/install.ps1` are allowlisted at the site root — which is exactly what Task 6 publishes.
- **Nothing large downloads without an explicit yes and a stated size** (spec §1.3). Applies to every artifact except the `gaia` binary itself, whose consent is the install command the user ran.
- **Release builds are stripped** — `-ldflags="-s -w"`. Size target 15 MB, current ~16.5 MB (darwin/arm64).
- **Platform key format is `<goos>-<goarch>`** using Go's names (`darwin-arm64`, `linux-amd64`, `windows-amd64`), matching `build_tui.yml`'s existing matrix. Do **not** use the npm-style `darwin-arm64`/`win32-x64` mix from `binaries.lock.json`.

**Do not touch in this plan:** `docs/guides/email.mdx`, `docs/docs.json`. Other sessions own them.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tui/internal/cli/root.go` | root command; `--version` wiring; the unknown-command forwarder | 1, 7 |
| `tui/internal/cli/forward.go` | **new** — resolve and exec `gaia-dev`, or explain what replaced the command | 7 |
| `tui/internal/cli/forward_test.go` | **new** — forwarder unit tests | 7 |
| `tui/internal/cli/root_test.go` | **new** — `--version` and arg-routing tests | 1 |
| `util/gen_binary_manifest.py` | **new** — walk built binaries, emit `manifest.json` with sha256 + size + mirror URLs | 3 |
| `tests/unit/test_gen_binary_manifest.py` | **new** — manifest generator tests | 3 |
| `.github/workflows/build_tui.yml` | add release-asset upload + manifest generation + publish | 2, 3, 4 |
| `installer/scripts/get-gaia.sh` | **new** — POSIX installer: resolve platform, read manifest, verify, install | 5 |
| `installer/scripts/get-gaia.ps1` | **new** — Windows equivalent | 5 |
| `tests/unit/test_get_gaia_script.py` | **new** — installer script tests against a local fixture manifest | 5 |
| `website/public/install.sh`, `install.ps1` | published copies, generated at deploy time | 6 |
| `.github/workflows/deploy_website.yml` | copy the installers into `public/` before build | 6 |
| `setup.py:333-337` | drop `gaia`, add `gaia-dev`, keep `gaia-cli` as deprecated | 8 |
| `tui/internal/daemon/client.go:245-254` | stop resolving the engine by `PATH` | 9 |
| `src/gaia/apps/webui/services/backend-installer.cjs` | `findGaiaBin()` must not silently resolve the Go binary | 10 |
| `tests/unit/test_remedy_commands_are_runnable.py` | un-invert the guardrail so it fails on stale remedies | 11 |

---

## Task 0: Verify the two unexecuted cobra claims

Spec §6.4 flags two structural reads of cobra source that were never run. Both drive later tasks. This is a five-minute task and it gates Task 1 and Task 7.

**Files:** none modified.

**Interfaces:**
- Produces: a confirmed or refuted answer for (a) whether `gaia hub install email` reaches `hubCmd` with `ArbitraryArgs` and silently ignores the extra words, and (b) whether `gaia --version` errors.

- [ ] **Step 1: Run both probes, redirecting (never piping)**

```bash
cd tui
go run ./cmd/gaia hub install email > /tmp/gaia-hub.txt 2>&1; echo "exit=$?"; cat /tmp/gaia-hub.txt
go run ./cmd/gaia --version          > /tmp/gaia-ver.txt 2>&1; echo "exit=$?"; cat /tmp/gaia-ver.txt
go run ./cmd/gaia version            > /tmp/gaia-vsub.txt 2>&1; echo "exit=$?"; cat /tmp/gaia-vsub.txt
```

- [ ] **Step 2: Record the outcomes in the plan file**

Append a short "Task 0 results" block to this document with the three exit codes and outputs.

Expected, per the audit: `--version` exits non-zero with `unknown flag: --version` (cobra only registers the flag when `rootCmd.Version != ""`, and `root.go` never sets it). `version` as a subcommand works. `hub install email` is the consequential one — if `hubCmd` accepts and ignores `install email`, a user following an old doc gets the browsing TUI instead of an install, silently.

- [ ] **Step 3: Commit the results**

```bash
git add docs/superpowers/plans/2026-07-25-gaia-binary-distribution-phase1.md
git commit -m "docs(plan): record cobra behaviour probes for phase 1"
```

---

## Task 1: Make `gaia --version` work

A published artifact needs a version the user can read back, and the installer script asserts on it in Task 5.

**Files:**
- Modify: `tui/internal/cli/root.go:14-28`
- Test: `tui/internal/cli/root_test.go` (create)

**Interfaces:**
- Consumes: `version`, `commit`, `date` package vars from `tui/internal/cli/version.go:9-13`, already injected by `build_tui.yml` via `-X`.
- Produces: `gaia --version` prints the same string as `gaia version` and exits 0.

- [ ] **Step 1: Write the failing test**

Create `tui/internal/cli/root_test.go`:

```go
package cli

import (
	"bytes"
	"strings"
	"testing"
)

func TestVersionFlagIsRegistered(t *testing.T) {
	if rootCmd.Version == "" {
		t.Fatal("rootCmd.Version is empty, so cobra never registers --version")
	}
}

func TestVersionFlagPrintsVersion(t *testing.T) {
	var out bytes.Buffer
	rootCmd.SetOut(&out)
	rootCmd.SetErr(&out)
	rootCmd.SetArgs([]string{"--version"})
	defer func() {
		rootCmd.SetArgs(nil)
		rootCmd.SetOut(nil)
		rootCmd.SetErr(nil)
	}()

	if err := rootCmd.Execute(); err != nil {
		t.Fatalf("--version returned an error: %v", err)
	}
	if !strings.Contains(out.String(), version) {
		t.Fatalf("--version output %q does not contain version %q", out.String(), version)
	}
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd tui && go test ./internal/cli/ -run TestVersionFlag -v > /tmp/t.txt 2>&1; echo "exit=$?"; cat /tmp/t.txt`
Expected: FAIL — `rootCmd.Version is empty, so cobra never registers --version`

- [ ] **Step 3: Set the version and its template**

In `tui/internal/cli/root.go`, inside `func init()` (after line 31's existing flag registration), add:

```go
	// cobra only registers --version when Version is non-empty. Keep the text
	// identical to `gaia version` so both spellings agree.
	rootCmd.Version = version
	rootCmd.SetVersionTemplate(
		fmt.Sprintf("gaia %s (commit: %s, built: %s)\n", version, commit, date))
```

`fmt` is already imported at `root.go:4`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd tui && go test ./internal/cli/ -run TestVersionFlag -v > /tmp/t.txt 2>&1; echo "exit=$?"; cat /tmp/t.txt`
Expected: PASS, both tests.

- [ ] **Step 5: Confirm both spellings agree in a real build**

```bash
cd tui
go run ./cmd/gaia --version > /tmp/a.txt 2>&1; echo "exit=$?"
go run ./cmd/gaia version   > /tmp/b.txt 2>&1; echo "exit=$?"
diff /tmp/a.txt /tmp/b.txt; echo "diff-exit=$?"
```
Expected: both exit 0, `diff-exit=0`.

- [ ] **Step 6: Commit**

```bash
git add tui/internal/cli/root.go tui/internal/cli/root_test.go
git commit -m "fix(tui): register --version so the flag works, not just the subcommand"
```

---

## Task 2: Publish the binaries as GitHub Release assets

Smallest useful step and the mirror leg of §3.3. No user-visible change; unblocks Tasks 3–5.

**Files:**
- Modify: `.github/workflows/build_tui.yml` (add a `release` job after `size-check`)

**Interfaces:**
- Produces: on a `v*` tag, assets named `gaia-<goos>-<goarch>[.exe]` attached to the GitHub Release, one per matrix target.

- [ ] **Step 1: Add the tag trigger**

In `.github/workflows/build_tui.yml`, extend the `on:` block (currently lines 10-23) with:

```yaml
  push:
    tags:
      - 'v*'
```

Keep the existing `branches: [main]` push trigger and its `paths:` filter. Note that a tag push has no `paths` filter, which is intended — a release must build regardless of what changed.

- [ ] **Step 2: Add the release job**

Append to `.github/workflows/build_tui.yml`:

```yaml
  release:
    name: Attach binaries to the release
    needs: [build, size-check]
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Download all platform artifacts
        uses: actions/download-artifact@v8
        with:
          pattern: gaia-*
          path: release-assets
          merge-multiple: true

      - name: List what will be uploaded
        run: |
          ls -la release-assets/ > /tmp/assets.txt 2>&1; echo "exit=$?"
          cat /tmp/assets.txt
          COUNT=$(ls release-assets/ | wc -l)
          echo "asset count: $COUNT"
          if [ "$COUNT" -lt 6 ]; then
            echo "::error::expected 6 platform binaries, found $COUNT"
            exit 1
          fi

      - name: Upload to the release
        uses: softprops/action-gh-release@v2
        with:
          files: release-assets/*
          fail_on_unmatched_files: true
```

The count gate is deliberate: a silently-missing platform is how a user on that platform gets a 404 from the installer instead of an error naming their platform.

- [ ] **Step 3: Validate the workflow parses**

Run: `cd /Users/kovtcharov/Work/gaia && python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build_tui.yml'))" > /tmp/y.txt 2>&1; echo "exit=$?"; cat /tmp/y.txt`
Expected: exit=0, no output.

- [ ] **Step 4: Dry-run the job logic locally**

```bash
mkdir -p /tmp/release-assets && cd /tmp/release-assets
for p in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64 windows-arm64; do touch "gaia-$p"; done
COUNT=$(ls | wc -l); echo "count=$COUNT"
rm -rf /tmp/release-assets
```
Expected: `count=6`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/build_tui.yml
git commit -m "ci(tui): publish the six platform binaries as release assets"
```

---

## Task 3: Generate the binary manifest

The manifest is the contract every installer and the future self-update path reads. It must carry a real byte count — `binaries.lock.json` shipping `"size": 0` is the bug that makes the consent rule (§1.3) unenforceable.

**Files:**
- Create: `util/gen_binary_manifest.py`
- Test: `tests/unit/test_gen_binary_manifest.py`
- Modify: `.github/workflows/build_tui.yml` (release job)

**Interfaces:**
- Consumes: a directory of files named `gaia-<goos>-<goarch>[.exe]` (Task 2's `release-assets/`).
- Produces: `build_manifest(assets_dir: Path, version: str, hub_base: str, gh_base: str) -> dict` and a CLI writing `manifest.json`. Schema:

```json
{
  "schemaVersion": 1,
  "component": "gaia",
  "version": "0.1.0",
  "platforms": {
    "darwin-arm64": {
      "sha256": "<64 hex chars>",
      "size": 17301504,
      "filename": "gaia-darwin-arm64",
      "urls": ["<hub url>", "<github url>"]
    }
  }
}
```

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gen_binary_manifest.py`:

```python
import hashlib
import json
import pytest

from util.gen_binary_manifest import build_manifest, UnknownPlatformError

HUB = "https://hub.amd-gaia.ai/bin/gaia"
GH = "https://github.com/amd/gaia/releases/download"


def _write(tmp_path, name, payload=b"binary-bytes"):
    p = tmp_path / name
    p.write_bytes(payload)
    return p


def test_manifest_records_real_sha256_and_size(tmp_path):
    payload = b"some binary content"
    _write(tmp_path, "gaia-darwin-arm64", payload)

    m = build_manifest(tmp_path, "0.1.0", HUB, GH)
    entry = m["platforms"]["darwin-arm64"]

    assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
    assert entry["size"] == len(payload)
    assert entry["size"] > 0


def test_manifest_lists_hub_first_then_github_mirror(tmp_path):
    _write(tmp_path, "gaia-linux-amd64")

    m = build_manifest(tmp_path, "0.1.0", HUB, GH)
    urls = m["platforms"]["linux-amd64"]["urls"]

    assert len(urls) == 2
    assert urls[0] == f"{HUB}/0.1.0/gaia-linux-amd64"
    assert urls[1] == f"{GH}/v0.1.0/gaia-linux-amd64"


def test_windows_exe_suffix_is_stripped_from_the_platform_key(tmp_path):
    _write(tmp_path, "gaia-windows-amd64.exe")

    m = build_manifest(tmp_path, "0.1.0", HUB, GH)

    assert "windows-amd64" in m["platforms"]
    assert m["platforms"]["windows-amd64"]["filename"] == "gaia-windows-amd64.exe"


def test_unrecognised_filename_raises_rather_than_being_skipped(tmp_path):
    _write(tmp_path, "gaia-solaris-sparc")

    with pytest.raises(UnknownPlatformError) as exc:
        build_manifest(tmp_path, "0.1.0", HUB, GH)

    assert "solaris-sparc" in str(exc.value)


def test_empty_directory_raises(tmp_path):
    with pytest.raises(ValueError) as exc:
        build_manifest(tmp_path, "0.1.0", HUB, GH)

    assert "no binaries" in str(exc.value).lower()


def test_manifest_is_deterministic(tmp_path):
    _write(tmp_path, "gaia-linux-amd64")
    _write(tmp_path, "gaia-darwin-arm64")

    a = json.dumps(build_manifest(tmp_path, "0.1.0", HUB, GH), sort_keys=True)
    b = json.dumps(build_manifest(tmp_path, "0.1.0", HUB, GH), sort_keys=True)

    assert a == b
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_gen_binary_manifest.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: FAIL — `ModuleNotFoundError: No module named 'util.gen_binary_manifest'`

- [ ] **Step 3: Write the implementation**

Create `util/gen_binary_manifest.py`:

```python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generate the `gaia` binary manifest consumed by the installers.

One entry per platform: the SHA-256 every client verifies against, a real byte
count so the install screen can state a size before downloading, and an ordered
mirror list (hub first, GitHub Releases second) sharing that single hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1
COMPONENT = "gaia"

# Go's own GOOS-GOARCH names, matching build_tui.yml's matrix.
KNOWN_PLATFORMS = {
    "linux-amd64",
    "linux-arm64",
    "darwin-amd64",
    "darwin-arm64",
    "windows-amd64",
    "windows-arm64",
}


class UnknownPlatformError(ValueError):
    """A built artifact does not map to a known platform key."""


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _platform_key(filename: str) -> str:
    stem = filename[: -len(".exe")] if filename.endswith(".exe") else filename
    if not stem.startswith("gaia-"):
        raise UnknownPlatformError(
            f"{filename!r} does not match the expected 'gaia-<goos>-<goarch>' shape. "
            f"Check the -o flag in .github/workflows/build_tui.yml."
        )
    key = stem[len("gaia-") :]
    if key not in KNOWN_PLATFORMS:
        raise UnknownPlatformError(
            f"{filename!r} resolves to platform {key!r}, which is not in KNOWN_PLATFORMS. "
            f"If a target was added to build_tui.yml, add it here too; known: "
            f"{sorted(KNOWN_PLATFORMS)}"
        )
    return key


def build_manifest(assets_dir: Path, version: str, hub_base: str, gh_base: str) -> dict:
    """Build the manifest dict for every binary in ``assets_dir``.

    Raises loudly on an unknown filename or an empty directory — a silently
    skipped platform reaches users as a 404 from the installer.
    """
    assets_dir = Path(assets_dir)
    files = sorted(p for p in assets_dir.iterdir() if p.is_file())
    if not files:
        raise ValueError(
            f"no binaries found in {assets_dir}. The release job downloads them "
            f"from the build matrix artifacts; check that step ran."
        )

    platforms: dict[str, dict] = {}
    for path in files:
        key = _platform_key(path.name)
        sha256, size = _sha256_and_size(path)
        platforms[key] = {
            "sha256": sha256,
            "size": size,
            "filename": path.name,
            "urls": [
                f"{hub_base.rstrip('/')}/{version}/{path.name}",
                f"{gh_base.rstrip('/')}/v{version}/{path.name}",
            ],
        }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "component": COMPONENT,
        "version": version,
        "platforms": dict(sorted(platforms.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets_dir", type=Path)
    parser.add_argument("--version", required=True, help="release version without a leading v")
    parser.add_argument("--hub-base", default="https://hub.amd-gaia.ai/bin/gaia")
    parser.add_argument("--gh-base", default="https://github.com/amd/gaia/releases/download")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = build_manifest(args.assets_dir, args.version, args.hub_base, args.gh_base)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out} with {len(manifest['platforms'])} platforms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_gen_binary_manifest.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: PASS, 6 tests.

- [ ] **Step 5: Wire it into the release job**

In `.github/workflows/build_tui.yml`, inside the `release` job, add between "List what will be uploaded" and "Upload to the release":

```yaml
      - uses: actions/checkout@v7
        with:
          path: repo

      - uses: actions/setup-python@v6
        with:
          python-version: "3.x"

      - name: Generate the binary manifest
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          python repo/util/gen_binary_manifest.py release-assets \
            --version "$VERSION" \
            --out release-assets/manifest.json > /tmp/gen.txt 2>&1
          echo "exit=$?"
          cat /tmp/gen.txt
```

The manifest lands inside `release-assets/`, so the existing upload step attaches it to the release alongside the binaries.

Note the ordering constraint: the asset-count gate in Task 2 runs *before* this step, so it counts 6 binaries and not 7. Do not move the manifest generation above it.

- [ ] **Step 6: Run lint**

Run: `cd /Users/kovtcharov/Work/gaia && python util/lint.py --black --isort > /tmp/l.txt 2>&1; echo "exit=$?"; tail -5 /tmp/l.txt`
Expected: exit=0.

- [ ] **Step 7: Commit**

```bash
git add util/gen_binary_manifest.py tests/unit/test_gen_binary_manifest.py .github/workflows/build_tui.yml
git commit -m "feat(ci): generate a hash-and-size manifest for the gaia binary"
```

---

## Task 4: Publish the manifest and binaries to the hub artifact store

**Files:**
- Modify: `.github/workflows/build_tui.yml` (release job)

**Interfaces:**
- Consumes: `release-assets/manifest.json` and the six binaries from Task 3.
- Produces: `GET https://hub.amd-gaia.ai/bin/gaia/manifest.json` and per-platform objects at the `urls[0]` paths the manifest advertises.

- [ ] **Step 1: Read the existing publish contract**

Read `.github/workflows/release_agent_email.yml` — specifically its publish job. It POSTs each artifact to the Agent Hub Worker's `/publish` with `Bearer $GAIA_HUB_TOKEN`, and `/publish` is immutable per filename: re-publishing identical bytes is a verified 409 no-op, different bytes under a published name fails. Reuse that call shape exactly; do not invent a second upload mechanism.

- [ ] **Step 2: Add the publish step, gated on an environment**

In the `release` job, before "Upload to the release":

```yaml
      - name: Publish to the hub artifact store
        env:
          GAIA_HUB_TOKEN: ${{ secrets.GAIA_HUB_TOKEN }}
          GAIA_HUB_BASE_URL: ${{ vars.GAIA_HUB_BASE_URL || 'https://hub.amd-gaia.ai' }}
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          set -e
          for f in release-assets/*; do
            NAME=$(basename "$f")
            if [ "$NAME" = "manifest.json" ]; then KEY="bin/gaia/manifest.json"; else KEY="bin/gaia/$VERSION/$NAME"; fi
            curl -sS -X POST \
              -H "Authorization: Bearer $GAIA_HUB_TOKEN" \
              -H "X-Object-Key: $KEY" \
              --data-binary "@$f" \
              "$GAIA_HUB_BASE_URL/publish" \
              -o /tmp/pub.txt -w "http_code=%{http_code}\n"
            echo "published $KEY exit=$?"
            cat /tmp/pub.txt
          done
```

Add `environment: agent-publish` to the `release` job so a human approves before anything is published, matching `release_agent_email.yml`.

**Verify the header name and object-key convention against `workers/agent-hub/src/` before running this** — the exact header the Worker reads was not confirmed while writing this plan, and a wrong header name fails at publish time with a 4xx rather than at review time.

- [ ] **Step 3: Post-publish verification — fetch back what was published**

Add after the publish step:

```yaml
      - name: Verify every published object matches the manifest
        run: |
          set -e
          BASE="${GAIA_HUB_BASE_URL:-https://hub.amd-gaia.ai}"
          python - <<'PY' > /tmp/verify.txt 2>&1
          import hashlib, json, sys, urllib.request
          m = json.load(open("release-assets/manifest.json"))
          bad = []
          for key, entry in m["platforms"].items():
              url = entry["urls"][0]
              with urllib.request.urlopen(url, timeout=60) as r:
                  data = r.read()
              got = hashlib.sha256(data).hexdigest()
              if got != entry["sha256"]:
                  bad.append(f"{key}: manifest {entry['sha256']} != fetched {got}")
              elif len(data) != entry["size"]:
                  bad.append(f"{key}: manifest size {entry['size']} != fetched {len(data)}")
              else:
                  print(f"ok {key}")
          if bad:
              print("MISMATCH:"); [print(" ", b) for b in bad]; sys.exit(1)
          PY
          echo "exit=$?"
          cat /tmp/verify.txt
```

This is the step that catches a publish that half-succeeded. Without it, the manifest can advertise a hash the store does not serve, and the first person to find out is a user whose install fails verification.

- [ ] **Step 4: Validate the workflow parses**

Run: `cd /Users/kovtcharov/Work/gaia && python -c "import yaml; yaml.safe_load(open('.github/workflows/build_tui.yml'))" > /tmp/y.txt 2>&1; echo "exit=$?"; cat /tmp/y.txt`
Expected: exit=0.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/build_tui.yml
git commit -m "ci(tui): publish the gaia binary and manifest to the hub artifact store"
```

---

## Task 5: The install script

~100 lines of POSIX sh that resolve the platform, read the manifest, try each mirror in order, verify the hash, and install to `~/.gaia/bin/`. Plus the PowerShell twin.

**Files:**
- Create: `installer/scripts/get-gaia.sh`, `installer/scripts/get-gaia.ps1`
- Test: `tests/unit/test_get_gaia_script.py`

**Interfaces:**
- Consumes: the manifest schema from Task 3.
- Produces: `~/.gaia/bin/gaia`, executable, hash-verified; exit 0 on success, non-zero with a named reason otherwise.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_get_gaia_script.py`:

```python
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "installer" / "scripts" / "get-gaia.sh"


def _run(env, *args):
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )


@pytest.fixture
def fake_release(tmp_path):
    """A local file:// 'mirror' holding one binary and a manifest describing it."""
    store = tmp_path / "store"
    store.mkdir()
    payload = b"#!/bin/sh\necho fake-gaia\n"
    binary = store / "gaia-linux-amd64"
    binary.write_bytes(payload)

    manifest = {
        "schemaVersion": 1,
        "component": "gaia",
        "version": "0.1.0",
        "platforms": {
            "linux-amd64": {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "filename": "gaia-linux-amd64",
                "urls": [binary.as_uri()],
            }
        },
    }
    (store / "manifest.json").write_text(json.dumps(manifest))
    return store


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")
def test_installs_and_verifies(tmp_path, fake_release):
    home = tmp_path / "home"
    r = _run(
        {
            "GAIA_MANIFEST_URL": (fake_release / "manifest.json").as_uri(),
            "GAIA_INSTALL_HOME": str(home),
            "GAIA_FORCE_PLATFORM": "linux-amd64",
        }
    )
    assert r.returncode == 0, r.stderr
    installed = home / "bin" / "gaia"
    assert installed.exists()
    assert os.access(installed, os.X_OK)


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")
def test_hash_mismatch_refuses_to_install(tmp_path, fake_release):
    manifest_path = fake_release / "manifest.json"
    m = json.loads(manifest_path.read_text())
    m["platforms"]["linux-amd64"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(m))

    home = tmp_path / "home"
    r = _run(
        {
            "GAIA_MANIFEST_URL": manifest_path.as_uri(),
            "GAIA_INSTALL_HOME": str(home),
            "GAIA_FORCE_PLATFORM": "linux-amd64",
        }
    )
    assert r.returncode != 0
    assert "checksum" in (r.stderr + r.stdout).lower()
    assert not (home / "bin" / "gaia").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")
def test_unsupported_platform_names_the_platform(tmp_path, fake_release):
    home = tmp_path / "home"
    r = _run(
        {
            "GAIA_MANIFEST_URL": (fake_release / "manifest.json").as_uri(),
            "GAIA_INSTALL_HOME": str(home),
            "GAIA_FORCE_PLATFORM": "solaris-sparc",
        }
    )
    assert r.returncode != 0
    assert "solaris-sparc" in (r.stderr + r.stdout)


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh required")
def test_reports_the_size_before_downloading(tmp_path, fake_release):
    home = tmp_path / "home"
    r = _run(
        {
            "GAIA_MANIFEST_URL": (fake_release / "manifest.json").as_uri(),
            "GAIA_INSTALL_HOME": str(home),
            "GAIA_FORCE_PLATFORM": "linux-amd64",
        }
    )
    assert r.returncode == 0
    assert "MB" in (r.stdout + r.stderr) or "bytes" in (r.stdout + r.stderr)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_get_gaia_script.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: FAIL — the script does not exist.

- [ ] **Step 3: Write the installer**

Create `installer/scripts/get-gaia.sh`:

```sh
#!/bin/sh
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Install the `gaia` binary. Resolves this machine's platform, reads the
# published manifest, downloads from the first mirror that answers, and
# verifies the SHA-256 before installing. A mismatch is fatal — there is no
# "install it anyway" path.
#
#   curl -fsSL https://amd-gaia.ai/install.sh | sh
#
# Env overrides (used by the tests):
#   GAIA_MANIFEST_URL    where the manifest lives
#   GAIA_INSTALL_HOME    install root (default: $HOME/.gaia)
#   GAIA_FORCE_PLATFORM  skip uname detection

set -eu

MANIFEST_URL="${GAIA_MANIFEST_URL:-https://hub.amd-gaia.ai/bin/gaia/manifest.json}"
INSTALL_HOME="${GAIA_INSTALL_HOME:-$HOME/.gaia}"
BIN_DIR="$INSTALL_HOME/bin"

die() { echo "error: $*" >&2; exit 1; }

need() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed."
}

detect_platform() {
    if [ -n "${GAIA_FORCE_PLATFORM:-}" ]; then
        echo "$GAIA_FORCE_PLATFORM"
        return
    fi
    os=$(uname -s)
    arch=$(uname -m)
    case "$os" in
        Linux)  goos=linux ;;
        Darwin) goos=darwin ;;
        *)      die "unsupported operating system: $os. GAIA publishes linux, darwin and windows builds; see https://github.com/amd/gaia/releases" ;;
    esac
    case "$arch" in
        x86_64|amd64)  goarch=amd64 ;;
        arm64|aarch64) goarch=arm64 ;;
        *)             die "unsupported architecture: $arch. GAIA publishes amd64 and arm64 builds; see https://github.com/amd/gaia/releases" ;;
    esac
    echo "${goos}-${goarch}"
}

fetch() {
    # fetch <url> <dest>; returns non-zero without printing on failure so the
    # caller can try the next mirror.
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2" 2>/dev/null
    else
        wget -q -O "$2" "$1" 2>/dev/null
    fi
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        die "neither sha256sum nor shasum is available, so the download cannot be verified. Install one and retry."
    fi
}

json_field() {
    # json_field <file> <platform> <field> — no jq dependency.
    sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*{\([^}]*\)}.*/\1/p" "$1" |
        sed -n "s/.*\"$3\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",]*\)\"\{0,1\}.*/\1/p" |
        head -1
}

json_urls() {
    sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*{\([^}]*\)}.*/\1/p" "$1" |
        tr ',' '\n' | sed -n 's/.*"\(https\{0,1\}:[^"]*\|file:[^"]*\)".*/\1/p'
}

command -v uname >/dev/null 2>&1 || die "uname is required."
need sed

PLATFORM=$(detect_platform)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

echo "Resolving the GAIA manifest..."
fetch "$MANIFEST_URL" "$TMP/manifest.json" ||
    die "could not read the release manifest at $MANIFEST_URL. Check your connection, or download a binary directly from https://github.com/amd/gaia/releases"

EXPECTED_SHA=$(json_field "$TMP/manifest.json" "$PLATFORM" sha256)
EXPECTED_SIZE=$(json_field "$TMP/manifest.json" "$PLATFORM" size)
[ -n "$EXPECTED_SHA" ] ||
    die "no build published for platform $PLATFORM. Published platforms are listed at https://github.com/amd/gaia/releases"

SIZE_MB=$(( ${EXPECTED_SIZE:-0} / 1024 / 1024 ))
echo "Downloading gaia for $PLATFORM (${SIZE_MB} MB, ${EXPECTED_SIZE} bytes)"

DOWNLOADED=0
for url in $(json_urls "$TMP/manifest.json" "$PLATFORM"); do
    echo "  trying $url"
    if fetch "$url" "$TMP/gaia"; then DOWNLOADED=1; break; fi
    echo "  ...unavailable, trying the next mirror"
done
[ "$DOWNLOADED" -eq 1 ] ||
    die "every mirror failed for $PLATFORM. Check your connection or a proxy, then retry; binaries are also at https://github.com/amd/gaia/releases"

ACTUAL_SHA=$(sha256_of "$TMP/gaia")
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    die "checksum mismatch for $PLATFORM.
  expected $EXPECTED_SHA
  actual   $ACTUAL_SHA
Refusing to install. This is a corrupted download or a tampered file. Retrying is safe; if it fails twice, report it at https://github.com/amd/gaia/issues"
fi

mkdir -p "$BIN_DIR"
chmod +x "$TMP/gaia"
mv "$TMP/gaia" "$BIN_DIR/gaia"

echo "Installed gaia to $BIN_DIR/gaia"
case ":$PATH:" in
    *":$BIN_DIR:"*) echo "Run: gaia" ;;
    *) echo "Add it to your PATH:  export PATH=\"$BIN_DIR:\$PATH\""
       echo "Then run: gaia" ;;
esac
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_get_gaia_script.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -30 /tmp/t.txt`
Expected: PASS, 4 tests.

- [ ] **Step 5: Shellcheck it**

Run: `cd /Users/kovtcharov/Work/gaia && shellcheck -s sh installer/scripts/get-gaia.sh > /tmp/sc.txt 2>&1; echo "exit=$?"; cat /tmp/sc.txt`
Expected: exit=0. If `shellcheck` is not installed, install it or note the skip explicitly — do not silently pass.

- [ ] **Step 6: Write the PowerShell twin**

Create `installer/scripts/get-gaia.ps1`:

```powershell
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Install the `gaia` binary on Windows. Same contract as get-gaia.sh: resolve
# the platform, read the manifest, try each mirror, verify SHA-256 before
# installing. A mismatch is fatal.
#
#   irm https://amd-gaia.ai/install.ps1 | iex

$ErrorActionPreference = 'Stop'

$ManifestUrl = if ($env:GAIA_MANIFEST_URL) { $env:GAIA_MANIFEST_URL }
               else { 'https://hub.amd-gaia.ai/bin/gaia/manifest.json' }
$InstallHome = if ($env:GAIA_INSTALL_HOME) { $env:GAIA_INSTALL_HOME }
               else { Join-Path $env:USERPROFILE '.gaia' }
$BinDir = Join-Path $InstallHome 'bin'

function Get-Platform {
    if ($env:GAIA_FORCE_PLATFORM) { return $env:GAIA_FORCE_PLATFORM }
    switch ($env:PROCESSOR_ARCHITECTURE) {
        'AMD64' { return 'windows-amd64' }
        'ARM64' { return 'windows-arm64' }
        default {
            throw "unsupported architecture: $($env:PROCESSOR_ARCHITECTURE). " +
                  "GAIA publishes amd64 and arm64 builds; see https://github.com/amd/gaia/releases"
        }
    }
}

$platform = Get-Platform

Write-Host 'Resolving the GAIA manifest...'
try {
    $manifest = Invoke-RestMethod -Uri $ManifestUrl -UseBasicParsing
} catch {
    throw "could not read the release manifest at $ManifestUrl. Check your connection or " +
          "proxy, or download a binary directly from https://github.com/amd/gaia/releases"
}

$entry = $manifest.platforms.$platform
if (-not $entry) {
    throw "no build published for platform $platform. Published platforms are listed at " +
          "https://github.com/amd/gaia/releases"
}

$sizeMb = [math]::Round($entry.size / 1MB, 1)
Write-Host "Downloading gaia for $platform ($sizeMb MB, $($entry.size) bytes)"

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$dest = Join-Path $tmp 'gaia.exe'

try {
    $downloaded = $false
    foreach ($url in $entry.urls) {
        Write-Host "  trying $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
            $downloaded = $true
            break
        } catch {
            Write-Host '  ...unavailable, trying the next mirror'
        }
    }
    if (-not $downloaded) {
        throw "every mirror failed for $platform. Check your connection or a proxy, then " +
              "retry; binaries are also at https://github.com/amd/gaia/releases"
    }

    $actual = (Get-FileHash -Path $dest -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $entry.sha256.ToLower()) {
        throw @"
checksum mismatch for $platform.
  expected $($entry.sha256)
  actual   $actual
Refusing to install. This is a corrupted download or a tampered file. Retrying is safe;
if it fails twice, report it at https://github.com/amd/gaia/issues
"@
    }

    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    Move-Item -Path $dest -Destination (Join-Path $BinDir 'gaia.exe') -Force
} finally {
    Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Installed gaia to $BinDir\gaia.exe"
if ($env:Path -notlike "*$BinDir*") {
    Write-Host "Add it to your PATH:  `$env:Path = `"$BinDir;`$env:Path`""
}
Write-Host 'Then run: gaia'
```

- [ ] **Step 6b: Verify the PowerShell parses**

On Windows, or with `pwsh` available:

```bash
pwsh -NoProfile -Command "\$null = [System.Management.Automation.Language.Parser]::ParseFile('installer/scripts/get-gaia.ps1', [ref]\$null, [ref]\$errs); if (\$errs) { \$errs; exit 1 }" > /tmp/ps.txt 2>&1; echo "exit=$?"; cat /tmp/ps.txt
```
Expected: exit=0. If `pwsh` is unavailable on this machine, say so explicitly and mark this step as verified-in-CI rather than silently skipping it.

- [ ] **Step 7: Commit**

```bash
git add installer/scripts/get-gaia.sh installer/scripts/get-gaia.ps1 tests/unit/test_get_gaia_script.py
git commit -m "feat(installer): add a hash-verifying one-line installer for the gaia binary"
```

---

## Task 6: Serve the installers at the advertised URLs

`website/src/pages/index.astro:76` and `docs/quickstart.mdx:111,133` have advertised `https://amd-gaia.ai/install.sh` and `install.ps1` for months. Nothing publishes them. They 404.

**Files:**
- Modify: `.github/workflows/deploy_website.yml`
- Test: `tests/unit/test_get_gaia_script.py` (add one test)

**Interfaces:**
- Produces: `https://amd-gaia.ai/install.sh` and `/install.ps1` serving Task 5's scripts.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_get_gaia_script.py`:

```python
import re

REPO = Path(__file__).resolve().parents[2]


def test_deploy_workflow_copies_the_installers_into_public():
    wf = (REPO / ".github" / "workflows" / "deploy_website.yml").read_text()
    assert "get-gaia.sh" in wf, (
        "deploy_website.yml does not copy installer/scripts/get-gaia.sh into "
        "website/public/install.sh, so https://amd-gaia.ai/install.sh 404s"
    )
    assert "get-gaia.ps1" in wf


def test_advertised_install_urls_match_what_is_published():
    """Every advertised install URL must be one the deploy step actually creates."""
    published = {"install.sh", "install.ps1"}
    advertised = set()
    for rel in ("website/src/pages/index.astro", "docs/quickstart.mdx"):
        text = (REPO / rel).read_text()
        advertised |= set(re.findall(r"amd-gaia\.ai/(install\.[a-z0-9]+)", text))
    assert advertised, "no install URLs found — did the docs change shape?"
    assert advertised <= published, f"advertised but never published: {advertised - published}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_get_gaia_script.py -k install -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: FAIL on the first test — `get-gaia.sh` is not in the deploy workflow.

- [ ] **Step 3: Add the copy step**

In `.github/workflows/deploy_website.yml`, immediately before the site build step:

```yaml
      - name: Publish the install scripts at the advertised URLs
        run: |
          set -e
          mkdir -p website/public
          cp installer/scripts/get-gaia.sh  website/public/install.sh
          cp installer/scripts/get-gaia.ps1 website/public/install.ps1
          ls -l website/public/install.* > /tmp/pub.txt 2>&1; echo "exit=$?"
          cat /tmp/pub.txt
```

Copying at deploy time rather than committing duplicates keeps one source of truth — the scripts the tests exercise are the scripts users download.

- [ ] **Step 3b: Resolve the collision with the existing venv installer**

`installer/scripts/install.sh` is a *different* script — it installs `uv`, builds `~/.gaia/venv`, and pip-installs `amd-gaia`. It has never been published, but it shares the name this task is now claiming at the site root. Two things must happen so the two do not get confused for each other:

1. **The user-facing URL serves the user-facing thing.** `/install.sh` is `get-gaia.sh`. This is the point of the task.
2. **The venv installer is relabelled as developer setup, not deleted.** Update its header comment to say so, and update the "Next steps" text it prints — `install.sh:205` and `install.ps1:161` currently tell the user to run `gaia init`, which after Task 8 does not exist. It becomes `gaia-dev init`.

Verify the new advice parses before committing:

```bash
cd /Users/kovtcharov/Work/gaia && gaia-dev init --help > /tmp/i.txt 2>&1; echo "exit=$?"; head -3 /tmp/i.txt
```
Expected: exit=0.

Do **not** repoint `installer/scripts/install.sh` at the Go binary in this task. It is the developer venv path and Phase 1 is not changing what developers get.

- [ ] **Step 4: Run to verify both tests pass**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_get_gaia_script.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy_website.yml tests/unit/test_get_gaia_script.py
git commit -m "fix(website): publish install.sh and install.ps1 at the URLs the docs advertise"
```

---

## Task 7: The forwarding shim

Spec §6.2. Without it, day one of the rename breaks `gaia init` — the first command in the quickstart — for every existing user, along with ~1,800 doc references and ~650 runtime remedy strings.

**Files:**
- Create: `tui/internal/cli/forward.go`, `tui/internal/cli/forward_test.go`
- Modify: `tui/internal/cli/root.go` (Execute)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `func forwardToDev(args []string, lookPath func(string) (string, error), exec func(string, []string) error) error`
  - `var errNoDevCLI = errors.New(...)`
  - `func replacementFor(command string) (string, bool)` — maps a retired command to its replacement sentence.

- [ ] **Step 1: Write the failing test**

Create `tui/internal/cli/forward_test.go`:

```go
package cli

import (
	"errors"
	"os/exec"
	"strings"
	"testing"
)

func TestForwardsUnknownCommandToDevCLI(t *testing.T) {
	var gotBin string
	var gotArgs []string

	err := forwardToDev(
		[]string{"eval", "agent", "--category", "rag_quality"},
		func(name string) (string, error) { return "/usr/bin/" + name, nil },
		func(bin string, args []string) error { gotBin, gotArgs = bin, args; return nil },
	)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotBin != "/usr/bin/gaia-dev" {
		t.Fatalf("forwarded to %q, want /usr/bin/gaia-dev", gotBin)
	}
	if strings.Join(gotArgs, " ") != "eval agent --category rag_quality" {
		t.Fatalf("forwarded args %q, arguments must pass through verbatim", gotArgs)
	}
}

func TestWithoutDevCLIItExplainsTheReplacement(t *testing.T) {
	err := forwardToDev(
		[]string{"init"},
		func(string) (string, error) { return "", exec.ErrNotFound },
		func(string, []string) error { t.Fatal("must not exec when gaia-dev is absent"); return nil },
	)

	if err == nil {
		t.Fatal("expected an error when gaia-dev is absent")
	}
	msg := err.Error()
	if !strings.Contains(msg, "gaia doctor") {
		t.Fatalf("error %q must name what replaced `gaia init`", msg)
	}
}

func TestUnknownAndUnmappedCommandStillFailsLoudly(t *testing.T) {
	err := forwardToDev(
		[]string{"nonsense-command"},
		func(string) (string, error) { return "", exec.ErrNotFound },
		func(string, []string) error { return nil },
	)

	if !errors.Is(err, errNoDevCLI) {
		t.Fatalf("got %v, want errNoDevCLI", err)
	}
	if !strings.Contains(err.Error(), "nonsense-command") {
		t.Fatalf("error %q must name the command the user typed", err.Error())
	}
}

func TestReplacementsAreKnownForTheRetiredCommands(t *testing.T) {
	for _, cmd := range []string{"init", "chat", "hub", "daemon"} {
		if _, ok := replacementFor(cmd); !ok {
			t.Errorf("no replacement sentence for retired command %q", cmd)
		}
	}
}

func TestEmptyArgsIsNotForwarded(t *testing.T) {
	err := forwardToDev(nil, nil, func(string, []string) error {
		t.Fatal("must not exec with no command")
		return nil
	})
	if err == nil {
		t.Fatal("expected an error for an empty command")
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd tui && go test ./internal/cli/ -run "Forward|Replacement|EmptyArgs" -v > /tmp/t.txt 2>&1; echo "exit=$?"; cat /tmp/t.txt`
Expected: FAIL — `undefined: forwardToDev`

- [ ] **Step 3: Write the implementation**

Create `tui/internal/cli/forward.go`:

```go
package cli

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"syscall"
)

// errNoDevCLI is returned when a command this binary does not implement is
// typed on a machine that has no gaia-dev to forward it to.
var errNoDevCLI = errors.New("gaia-dev is not installed")

const devCLI = "gaia-dev"

// replacements maps a command that used to live on the Python `gaia` to the
// sentence telling a user what to do instead. Only user-facing commands appear
// here; developer commands forward to gaia-dev and need no entry.
var replacements = map[string]string{
	"init":      "setup is built in now — press `c` in `gaia`, or run `gaia doctor`",
	"chat":      "run an agent instead: `gaia run <agent-id>`, or press enter on it in `gaia`",
	"hub":       "`gaia list`, `gaia install <id>` and `gaia uninstall <id>` replace it",
	"daemon":    "`gaia status` shows the background service; `gaia daemon` controls it",
	"install":   "`gaia install <agent-id>` installs an agent; setup is `gaia doctor`",
	"uninstall": "`gaia uninstall <agent-id>` removes one agent",
	"connectors": "accounts are connected from inside the agent that uses them — " +
		"run the agent and follow its prompts",
}

// replacementFor returns the guidance sentence for a retired command.
func replacementFor(command string) (string, bool) {
	s, ok := replacements[command]
	return s, ok
}

// execve replaces this process with bin. Separated so tests can substitute it.
func execve(bin string, args []string) error {
	return syscall.Exec(bin, append([]string{bin}, args...), os.Environ())
}

// forwardToDev handles a command this binary does not implement.
//
// On a developer machine it hands the arguments to gaia-dev verbatim. On a user
// machine — where gaia-dev does not exist — it fails loudly, naming what
// replaced the command when we know, and never guessing.
func forwardToDev(
	args []string,
	lookPath func(string) (string, error),
	execFn func(string, []string) error,
) error {
	if len(args) == 0 {
		return fmt.Errorf("no command given")
	}
	command := args[0]

	if lookPath != nil {
		if bin, err := lookPath(devCLI); err == nil {
			fmt.Fprintf(os.Stderr,
				"note: `gaia %s` is a developer command; forwarding to %s. "+
					"Run it as `%s %s` in future.\n",
				command, devCLI, devCLI, command)
			return execFn(bin, args)
		}
	}

	if hint, ok := replacementFor(command); ok {
		return fmt.Errorf("`gaia %s` is no longer a command — %s", command, hint)
	}

	return fmt.Errorf(
		"%w, so `gaia %s` cannot run. It is a developer command: install the "+
			"developer tools in a checkout (`uv pip install -e \".[dev]\"`) and run "+
			"`%s %s`",
		errNoDevCLI, command, devCLI, command)
}

// lookPathFunc is the indirection point for tests at the Execute boundary.
var lookPathFunc = exec.LookPath
```

- [ ] **Step 4: Run to verify the tests pass**

Run: `cd tui && go test ./internal/cli/ -run "Forward|Replacement|EmptyArgs" -v > /tmp/t.txt 2>&1; echo "exit=$?"; cat /tmp/t.txt`
Expected: PASS, 5 tests.

- [ ] **Step 5: Wire it into Execute**

Replace `tui/internal/cli/root.go`'s `Execute` body (lines 47-53) with:

```go
func Execute() error {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == "tui" {
		args = args[1:]
		rootCmd.SetArgs(args)
	}

	// A command we do not implement is not an error yet: on a developer machine
	// it belongs to gaia-dev. Only the flagless, non-help case is forwarded, so
	// `gaia --help` and bare `gaia` keep their own behaviour.
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		if _, _, err := rootCmd.Find(args); err != nil {
			return forwardToDev(args, lookPathFunc, execve)
		}
	}

	return rootCmd.Execute()
}
```

Add `"strings"` to the import block at `root.go:3-10`.

- [ ] **Step 6: Test the wiring end to end**

```bash
cd tui
go build -o /tmp/gaia-test ./cmd/gaia > /tmp/b.txt 2>&1; echo "build-exit=$?"; cat /tmp/b.txt
PATH=/usr/bin:/bin /tmp/gaia-test init > /tmp/i.txt 2>&1; echo "exit=$?"; cat /tmp/i.txt
PATH=/usr/bin:/bin /tmp/gaia-test list > /tmp/l.txt 2>&1; echo "exit=$?"; head -3 /tmp/l.txt
```
Expected: `init` exits non-zero and mentions `gaia doctor`; `list` runs its own command and does not forward.

- [ ] **Step 7: Run the whole package's tests and vet**

Run: `cd tui && go test ./... -count=1 > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt && go vet ./... > /tmp/v.txt 2>&1; echo "vet-exit=$?"; cat /tmp/v.txt`
Expected: both exit 0.

- [ ] **Step 8: Commit**

```bash
git add tui/internal/cli/forward.go tui/internal/cli/forward_test.go tui/internal/cli/root.go
git commit -m "feat(tui): forward unknown commands to gaia-dev instead of dead-ending"
```

**Note for the implementer:** `syscall.Exec` does not exist on Windows. If `go vet` or the build fails on a Windows target, split `execve` into `forward_unix.go` and `forward_windows.go` with build tags, using `exec.Command` + `cmd.Run()` + `os.Exit(cmd.ProcessState.ExitCode())` on Windows. Do not paper over it with a runtime check.

---

## Task 8: Move the console scripts

**Files:**
- Modify: `setup.py:333-337`
- Test: `tests/unit/test_console_scripts.py` (create)

**Interfaces:**
- Produces: `gaia-dev` and `gaia-mcp` console scripts; `gaia-cli` retained as a deprecated alias; **no** `gaia`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_console_scripts.py`:

```python
import re
from pathlib import Path

SETUP = Path(__file__).resolve().parents[2] / "setup.py"


def _console_scripts() -> dict[str, str]:
    text = SETUP.read_text()
    block = re.search(r'"console_scripts":\s*\[(.*?)\]', text, re.S)
    assert block, "console_scripts block not found in setup.py"
    scripts = {}
    for line in block.group(1).splitlines():
        m = re.search(r'"([\w-]+)\s*=\s*([\w.:]+)"', line)
        if m:
            scripts[m.group(1)] = m.group(2)
    return scripts


def test_pip_does_not_install_a_command_named_gaia():
    """`gaia` belongs to the Go binary. Two programs with one name is the bug."""
    assert "gaia" not in _console_scripts()


def test_gaia_dev_is_the_developer_cli():
    assert _console_scripts().get("gaia-dev") == "gaia.cli:main"


def test_gaia_cli_alias_is_retained_for_migration():
    assert _console_scripts().get("gaia-cli") == "gaia.cli:main"


def test_gaia_mcp_is_unchanged():
    assert _console_scripts().get("gaia-mcp") == "gaia.mcp.mcp_bridge:main"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_console_scripts.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: FAIL on the first two — `gaia` is present and `gaia-dev` is absent.

- [ ] **Step 3: Edit setup.py**

Replace `setup.py:333-338`:

```python
        "console_scripts": [
            # `gaia` is the Go binary (tui/). This package must not install a
            # second program under that name — see
            # docs/superpowers/specs/2026-07-25-tui-packaging-design.md
            "gaia-dev = gaia.cli:main",
            "gaia-cli = gaia.cli:main",  # deprecated alias, retained for migration
            "gaia-mcp = gaia.mcp.mcp_bridge:main",
        ]
    },
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_console_scripts.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: PASS, 4 tests.

- [ ] **Step 5: Verify a real install produces the right commands**

```bash
cd /Users/kovtcharov/Work/gaia
python -m pip install -e . --no-deps -q > /tmp/pi.txt 2>&1; echo "exit=$?"; tail -5 /tmp/pi.txt
command -v gaia-dev > /tmp/w.txt 2>&1; echo "gaia-dev-exit=$?"; cat /tmp/w.txt
```
Expected: `gaia-dev-exit=0`.

- [ ] **Step 6: Commit**

```bash
git add setup.py tests/unit/test_console_scripts.py
git commit -m "feat(cli): rename the Python entry point to gaia-dev and stop shipping gaia"
```

---

## Task 9: Stop resolving the engine by PATH

Spec §2.3. `tui/internal/daemon/client.go:246-254` runs `exec.LookPath("gaia")` and then `gaia daemon start`. After Task 8 that resolves to the Go binary itself. Its remedy text also advises `pip install -e .`, which is now wrong.

**Files:**
- Modify: `tui/internal/daemon/client.go:245-254`
- Test: `tui/internal/daemon/client_test.go` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `gaiaDaemonStart` resolves `gaia-dev` explicitly and never `gaia`.

- [ ] **Step 1: Write the failing test**

Add to `tui/internal/daemon/client_test.go` (create if absent, `package daemon`):

```go
package daemon

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDaemonLauncherNeverResolvesTheNameGaia(t *testing.T) {
	src, err := os.ReadFile("client.go")
	if err != nil {
		t.Fatalf("cannot read client.go: %v", err)
	}
	if strings.Contains(string(src), `LookPath("gaia")`) {
		t.Fatal(`client.go still calls LookPath("gaia"), which now resolves to the Go binary itself`)
	}
}

func TestDaemonLauncherRemedyDoesNotAdvisePipInstallDashE(t *testing.T) {
	src, _ := os.ReadFile("client.go")
	if strings.Contains(string(src), "pip install -e .") {
		t.Fatal("client.go advises `pip install -e .`, which is not how a user gets the daemon")
	}
}

func TestGaiaDaemonStartResolvesGaiaDev(t *testing.T) {
	dir := t.TempDir()
	stub := filepath.Join(dir, "gaia-dev")
	if err := os.WriteFile(stub, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)

	cmd, err := gaiaDaemonStart(t.Context())
	if err != nil {
		t.Fatalf("gaiaDaemonStart returned %v with gaia-dev on PATH", err)
	}
	if filepath.Base(cmd.Path) != "gaia-dev" {
		t.Fatalf("launcher resolved %q, want gaia-dev", cmd.Path)
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd tui && go test ./internal/daemon/ -run "Launcher|GaiaDaemonStart" -v > /tmp/t.txt 2>&1; echo "exit=$?"; cat /tmp/t.txt`
Expected: FAIL on all three.

- [ ] **Step 3: Fix the launcher**

Replace `tui/internal/daemon/client.go:245-254`:

```go
// gaiaDaemonStart builds the default launcher command.
//
// Resolves `gaia-dev` explicitly, never `gaia` — `gaia` is this binary, and
// asking the OS to find it produces a process that execs a subcommand it does
// not have.
func gaiaDaemonStart(ctx context.Context) (*exec.Cmd, error) {
	bin, err := exec.LookPath("gaia-dev")
	if err != nil {
		return nil, &StartError{Reason: "the GAIA background service is not installed, " +
			"so it cannot be started. Run `gaia doctor` to install it"}
	}
	return exec.CommandContext(ctx, bin, "daemon", "start"), nil
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd tui && go test ./internal/daemon/ -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: PASS.

- [ ] **Step 5: Verify the remedy actually parses**

`gaia doctor` does not exist until Phase 3. Until it does, the remedy must name something that works today. Change the message to `Run \`gaia-dev daemon start\`` and add a `TODO`-free note in the spec's §9.6 list instead. Then re-run:

```bash
cd /Users/kovtcharov/Work/gaia && gaia-dev daemon --help > /tmp/d.txt 2>&1; echo "exit=$?"; head -5 /tmp/d.txt
```
Expected: exit=0. **If it does not parse, the remedy is wrong and must be changed before commit** — this is the exact failure class CLAUDE.md names.

- [ ] **Step 6: Commit**

```bash
git add tui/internal/daemon/client.go tui/internal/daemon/client_test.go
git commit -m "fix(tui): resolve gaia-dev for the daemon launcher, never gaia"
```

---

## Task 10: Stop `findGaiaBin()` resolving the wrong program

The highest-severity item in the rename audit, because it fails **silently**.

**Files:**
- Modify: `src/gaia/apps/webui/services/backend-installer.cjs`
- Test: `tests/electron/` (follow the existing Jest layout there)

**Interfaces:**
- Produces: `findGaiaBin()` returns only a venv-resident `gaia-dev`, never a `gaia` found on `PATH`.

- [ ] **Step 1: Read the current implementation**

```bash
cd /Users/kovtcharov/Work/gaia && grep -n "findGaiaBin" -A 30 src/gaia/apps/webui/services/backend-installer.cjs > /tmp/f.txt 2>&1; echo "exit=$?"; cat /tmp/f.txt
```

- [ ] **Step 2: Write the failing test**

Create `tests/electron/backend-installer-find-gaia-bin.test.cjs` (the directory uses `.test.cjs`; match it). Match the module-loading style already used by the other files in `tests/electron/` — if they use `jest.mock` for `fs`, do the same rather than introducing a second pattern:

```js
const path = require('path');
const os = require('os');
const fs = require('fs');

const { findGaiaBin } = require('../../src/gaia/apps/webui/services/backend-installer.cjs');

describe('findGaiaBin', () => {
  let tmpHome;

  beforeEach(() => {
    tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'gaia-test-'));
  });

  afterEach(() => {
    fs.rmSync(tmpHome, { recursive: true, force: true });
    delete process.env.GAIA_HOME;
    delete process.env.PATH_OVERRIDE_FOR_TEST;
  });

  it('resolves the venv gaia-dev by absolute path', () => {
    const binDir = path.join(tmpHome, 'venv', process.platform === 'win32' ? 'Scripts' : 'bin');
    fs.mkdirSync(binDir, { recursive: true });
    const exe = path.join(binDir, process.platform === 'win32' ? 'gaia-dev.exe' : 'gaia-dev');
    fs.writeFileSync(exe, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    process.env.GAIA_HOME = tmpHome;

    expect(findGaiaBin()).toBe(exe);
  });

  it('returns null rather than a `gaia` found on PATH', () => {
    // A Go `gaia` on PATH must never satisfy this resolver: it is a different
    // program and calling `gaia init` on it does not do what this caller wants.
    const decoyDir = path.join(tmpHome, 'decoy');
    fs.mkdirSync(decoyDir, { recursive: true });
    const decoy = path.join(decoyDir, process.platform === 'win32' ? 'gaia.exe' : 'gaia');
    fs.writeFileSync(decoy, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    process.env.GAIA_HOME = tmpHome;
    process.env.PATH = `${decoyDir}${path.delimiter}${process.env.PATH}`;

    expect(findGaiaBin()).toBeNull();
  });

  it('returns null when nothing is installed, rather than guessing', () => {
    process.env.GAIA_HOME = tmpHome;
    expect(findGaiaBin()).toBeNull();
  });
});
```

If `findGaiaBin` is not currently exported from `backend-installer.cjs`, add it to that file's `module.exports` as part of Step 4 — the resolver is already a named unit and testing it directly is cheaper than driving the whole installer.

- [ ] **Step 3: Run to verify it fails**

Run: `cd /Users/kovtcharov/Work/gaia && npx jest tests/electron --testPathPattern backend-installer > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`

- [ ] **Step 4: Fix the resolver**

Change `findGaiaBin()` to look only at the absolute venv path and the `gaia-dev` name. Delete any `PATH` search for `gaia`. If nothing is found, return null and let the caller report it — do not fall back.

- [ ] **Step 5: Run to verify it passes**

Run: `cd /Users/kovtcharov/Work/gaia && npx jest tests/electron --testPathPattern backend-installer > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`

- [ ] **Step 6: Update the `gaia init` call site**

`backend-installer.cjs` invokes `["init", "--profile", "minimal", "--yes"]` on the resolved binary. With `gaia-dev` that still parses. Verify:

```bash
cd /Users/kovtcharov/Work/gaia && gaia-dev init --help > /tmp/i.txt 2>&1; echo "exit=$?"; head -5 /tmp/i.txt
```
Expected: exit=0.

- [ ] **Step 7: Commit**

```bash
git add src/gaia/apps/webui/services/backend-installer.cjs tests/electron/
git commit -m "fix(ui): resolve gaia-dev in the backend installer, never a PATH gaia"
```

---

## Task 11: Un-invert the remedy guardrail

`tests/unit/test_remedy_commands_are_runnable.py` currently passes *because* remedies are stale — it certifies the bug. CLAUDE.md's "a shipped remedy must actually parse" rule depends on this file being right.

**Files:**
- Modify: `tests/unit/test_remedy_commands_are_runnable.py`

**Interfaces:**
- Produces: a test that fails when any user-facing remedy string names a command the argparse surface rejects.

- [ ] **Step 1: Read it and find the inversion**

```bash
cd /Users/kovtcharov/Work/gaia && cat tests/unit/test_remedy_commands_are_runnable.py > /tmp/g.txt 2>&1; echo "exit=$?"; cat /tmp/g.txt
```

Identify why a stale remedy does not fail it — typically an over-broad skip, a `pytest.xfail`, or an allowlist that swallowed the failing cases.

- [ ] **Step 2: Write a test that proves the guardrail bites**

Add a negative test to the same file — the pattern `pypi.yml` already uses for `verify_wheel_dist.py`:

```python
def test_the_guardrail_actually_bites():
    """Plant a remedy naming a command that does not exist; the checker must reject it."""
    from tests.unit.test_remedy_commands_are_runnable import remedy_parses  # adjust to the real symbol

    assert remedy_parses("gaia-dev init --profile minimal") is True
    assert remedy_parses("gaia-dev definitely-not-a-command") is False
```

Adjust the imported symbol to whatever the file actually exposes; if it exposes nothing importable, extract the check into a module-level function first, then test it.

- [ ] **Step 3: Run to verify the negative case fails today**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_remedy_commands_are_runnable.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -30 /tmp/t.txt`

- [ ] **Step 4: Fix the checker so it rejects unknown commands**

Remove the inversion. The checker must parse each remedy string against the real argparse surface and fail on a non-zero parse.

- [ ] **Step 5: Run and fix every remedy it now catches**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/test_remedy_commands_are_runnable.py -v > /tmp/t.txt 2>&1; echo "exit=$?"; tail -40 /tmp/t.txt`

Expect a batch of failures naming stale remedies. Fix each by changing the string to a command that parses. **Do not add to an allowlist.** Known-wrong remedies to expect, from the freeze study: `src/gaia/hub/installer.py:401-441` and `src/gaia/installer/init_command.py:435-470`.

- [ ] **Step 6: Run the full unit suite**

Run: `cd /Users/kovtcharov/Work/gaia && python -m pytest tests/unit/ -q > /tmp/t.txt 2>&1; echo "exit=$?"; tail -20 /tmp/t.txt`
Expected: exit=0.

- [ ] **Step 7: Lint and commit**

```bash
cd /Users/kovtcharov/Work/gaia
python util/lint.py --all > /tmp/l.txt 2>&1; echo "exit=$?"; tail -5 /tmp/l.txt
git add tests/unit/test_remedy_commands_are_runnable.py src/gaia/
git commit -m "fix(tests): make the remedy guardrail fail on stale commands instead of certifying them"
```

---

## Task 12: Phase 1 acceptance

**Files:** none modified. This is the gate.

- [ ] **Step 1: Build and install from the real script against a local manifest**

```bash
cd /Users/kovtcharov/Work/gaia/tui
go build -ldflags="-s -w" -o /tmp/store/gaia-$(go env GOOS)-$(go env GOARCH) ./cmd/gaia > /tmp/b.txt 2>&1; echo "exit=$?"
cd /Users/kovtcharov/Work/gaia
python util/gen_binary_manifest.py /tmp/store --version 0.0.0-local \
  --hub-base "file:///tmp/store" --gh-base "file:///tmp/store" \
  --out /tmp/store/manifest.json > /tmp/m.txt 2>&1; echo "exit=$?"; cat /tmp/m.txt
GAIA_MANIFEST_URL="file:///tmp/store/manifest.json" GAIA_INSTALL_HOME=/tmp/gaia-home \
  sh installer/scripts/get-gaia.sh > /tmp/inst.txt 2>&1; echo "exit=$?"; cat /tmp/inst.txt
```

Note the manifest's `urls[0]` will be `file:///tmp/store/0.0.0-local/gaia-...`, which does not exist. Either place the binary at that path first or pass `--hub-base "file:///tmp"` such that the constructed path resolves. Confirm the installed file runs:

```bash
/tmp/gaia-home/bin/gaia --version > /tmp/v.txt 2>&1; echo "exit=$?"; cat /tmp/v.txt
```
Expected: exit=0, prints a version.

- [ ] **Step 2: Confirm the consent rule is observable**

The installer must have printed a size before downloading. Check `/tmp/inst.txt` contains an MB figure. If it printed `0 MB`, the manifest size is wrong and Task 3 regressed.

- [ ] **Step 3: Confirm tamper rejection**

```bash
python - <<'PY'
import json, pathlib
p = pathlib.Path("/tmp/store/manifest.json")
m = json.loads(p.read_text())
for e in m["platforms"].values():
    e["sha256"] = "0" * 64
p.write_text(json.dumps(m))
PY
GAIA_MANIFEST_URL="file:///tmp/store/manifest.json" GAIA_INSTALL_HOME=/tmp/gaia-home2 \
  sh installer/scripts/get-gaia.sh > /tmp/tamper.txt 2>&1; echo "exit=$?"; cat /tmp/tamper.txt
```
Expected: **non-zero exit**, the word `checksum`, and no file at `/tmp/gaia-home2/bin/gaia`.

- [ ] **Step 4: Confirm no command a user knows is dead**

```bash
PATH=/usr/bin:/bin /tmp/gaia-home/bin/gaia init  > /tmp/c1.txt 2>&1; echo "exit=$?"; cat /tmp/c1.txt
PATH=/usr/bin:/bin /tmp/gaia-home/bin/gaia chat  > /tmp/c2.txt 2>&1; echo "exit=$?"; cat /tmp/c2.txt
```
Expected: both fail with a sentence naming the replacement — never "unknown command".

- [ ] **Step 5: Full test suite + lint**

```bash
cd /Users/kovtcharov/Work/gaia
python -m pytest tests/unit/ -q > /tmp/py.txt 2>&1; echo "pytest-exit=$?"; tail -10 /tmp/py.txt
python util/lint.py --all      > /tmp/l.txt  2>&1; echo "lint-exit=$?";   tail -5  /tmp/l.txt
cd tui && go test ./... -count=1 > /tmp/go.txt 2>&1; echo "go-exit=$?"; tail -10 /tmp/go.txt
```
Expected: all three exit 0.

- [ ] **Step 6: Clean up scratch files**

```bash
rm -rf /tmp/store /tmp/gaia-home /tmp/gaia-home2 /tmp/gaia-test /tmp/release-assets
```

- [ ] **Step 7: Commit any final fixes**

---

## Not in this plan

| Deferred | Where |
|---|---|
| Porting the management layer to Go | Phase 2 plan — gated on the launch contract being written down (spec §9.4 risk 2) |
| Homebrew / winget / apt, desktop installers, the stage-0 readiness row, `gaia doctor` | Phase 3 plan |
| Removing the agent-as-command surface, the doc sweep, `gaia tui` prefix deprecation | Phase 4 plan — gated on the capability-loss ledger (spec §9.3) |
| Code-signing the binary | Blocked externally: `.signpath/policies/gaia.policy` is scoped to `nsis-installer` artifacts and would reject a bare executable (spec §3.5) |
