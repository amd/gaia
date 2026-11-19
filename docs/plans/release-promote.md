# Promote the verified build on tag (#6611)

Today a release tag rebuilds every artifact the release-train dry run has already
built and verified for that commit, so testers never get the bytes that passed. The
rebuild also adds ~25 min to every RC. On v0.25.4-rc4, published from the exact commit
its dry run verified, only 1 of the 25 assets present in both was byte-identical:
the wheel, sdist, sidecars, installers and even the terminal binaries all differed.
This plan makes the tag workflow **verify and upload** the dry run's artifacts instead
of rebuilding them. It is Phase 5/6 of [`release-train.mdx`](release-train.mdx), done
for speed first.

**Done when** (from #6611): an RC is tagged and published within ~15 min of deciding
to, from a commit whose dry run already passed, and every published asset is
byte-identical to the verified one. `verified-build-report` in `publish.yml` measures
the second half on every release from the first increment on.

## How it works

1. **Build once.** `release_train_dryrun.yml` builds every artifact of a `main` (or `vX.Y.Z-release`) commit
   as the next RC plus its final-stamped twin, verifies them, and records each
   artifact's SHA-256 in `release-manifest.json`. It runs nightly on `main` and on demand
   (#6612), so a passing run usually exists before anyone decides to tag.
2. **Locate.** On a tag, `util/release_promote.py locate` finds the newest passing dry
   run of the tagged commit whose manifest names that tag (the RC build) or its twin
   (the final build). The manifest must be the current schema, have no failed checks,
   and record the same commit. A run that built nothing (a nightly that skipped a
   commit that already passed) is passed over.
3. **Verify.** Download that run's artifacts and re-hash every file against the
   manifest. One missing file or one wrong byte fails the release, and the message
   names the file and says what to re-run.
4. **Upload.** Re-upload the verified files under the artifact names `publish.yml`'s
   publish jobs already read (`dist`, `windows-installer`, `tui-binaries`, ...). From
   `approve-publish` onward, nothing in the publish path changes.
5. **Check after.** `release_promote.py compare --strict` reads the published release
   and fails if any asset differs from the manifest.

There is no silent rebuild. With no matching dry run the tag fails, and the error says
how to dispatch one. Rebuilding is a `workflow_dispatch` input (`rebuild: true`) a
maintainer sets on purpose, and its summary says the release was not promoted.

## What has to change first

| Gap | Why it blocks promotion | Fix |
|---|---|---|
| Dry-run build artifacts expire after 7 days | A tag more than a week after its dry run has nothing to promote | 14-day retention on the artifacts a release publishes. The nightly skips a commit that already passed, so an unchanged commit keeps its manifest but loses its artifacts at retention: the skip must rebuild once the last passing run's artifacts have expired |
| Asset names differ | The release ships `gaia-linux-amd64` from `publish.yml`'s separate `build-tui`, while the dry run catalogs `gaia-linux-x64`. The installer checksum file and the sigstore bundles exist only on the release | One TUI build feeds both; the manifest catalog becomes the source of truth for release asset names (#5172 Phase 5) |
| Provenance isn't checked | Publish must trust only a dry run of `amd/gaia` on a protected branch it releases from: `main`, or a `vX.Y.Z-release` branch (v0.25.4's dry runs were dispatched on `v0.25.4-release`), started by `schedule` or `workflow_dispatch` | `locate` checks the run's workflow path, repository, event, and that its branch is `main` or matches `v*-release`, not just the commit |
| Signing happens inside the build | Once SignPath or Apple signing secrets exist, a signed installer can't match an unsigned dry-run digest. Today no build is signed with a secret: the dry run and publish both produce unsigned Windows and ad-hoc-signed macOS builds | Signing becomes a publish step over the verified bytes. The manifest keeps the unsigned digest, and publish records the signed one next to it |

## Increments

Each one leaves releases working.

1. **Lookup + report** (this PR). `release_promote.py locate|compare`, plus a
   report-only `verified-build-report` job after the release exists. Nothing waits on
   it. It runs the lookup on every real tag and puts identical / differ / missing in
   the summary.
2. **Align the catalog.** Provenance checks in `locate`, 14-day retention, and one TUI
   build with the release's asset names. The report's *missing* and *not in the
   manifest* counts should reach 0.
3. **Promote the packages.** A `promote` job feeds `publish-pypi` and `publish-npm`
   the verified wheel, sdist and npm tarball, and `build-pypi`/`build-npm` stop
   running on a tag. These are the irreversible channels, so they go first. Add the
   `rebuild` dispatch input.
4. **Promote everything.** Installers, the setup, the sidecars and the terminal
   binaries come from the dry run, and the build jobs leave the tag path. `compare
   --strict` runs as a gate before `approve-publish` (on the downloaded files) and
   after `github-release` (on the release). The RC critical path becomes
   validate → locate/verify (~3–4 min for ~5 GB) → publish (~5 min).
5. **Signing hook**, when the SignPath or Apple secrets land.

## Risks

- **Trusting another run's artifacts.** The dry run holds no secrets and a read-only
  token, and builds only its own branch's head commit. Publish accepts a run only if its
  provenance checks out and every byte matches that same run's manifest. A
  maintainer dispatching the dry run on another branch at the same SHA builds the
  same code.
- **Shared caches.** The dry run restores its branch's caches, and publish's own
  builds already do too. Promotion adds no new cache path.
- **An RC number collision.** If rc5 is tagged on a different commit than the
  nightly's rc5, `locate` refuses: a manifest of another commit can't vouch. The fix
  is to dispatch the dry run at the tagged commit.
- **Rollback.** Each increment is one PR. Before increment 4, reverting restores the
  rebuild path unchanged.
