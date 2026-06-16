# Manual R2 upload — email agent binaries

How to publish frozen email-agent binaries to the GAIA assets bucket **by hand**
(the "I run rclone myself" path). This produces the exact same objects and lock
as the CI release (`.github/workflows/release_agent_email.yml`) — same bucket,
same `hub/` prefix, same `assets.amd-gaia.ai` origin — so a hand-upload and a CI
release are interchangeable.

> **One-time rclone setup** (remote name, R2 credentials, endpoint) is in
> [`scripts/video-demo/R2-SETUP.md`](../../../../../scripts/video-demo/R2-SETUP.md).
> That guide configures a remote named `gaia` against the `amd-gaia` bucket — the
> same bucket the docs videos use. This page assumes that remote exists.

## Layout

Binaries are served by a plain public GET; the SHA-256 in `binaries.lock.json`
is the integrity gate the npm `fetch` CLI enforces (no server-side checksum).

```
R2:  amd-gaia/hub/agents/python/email/<version>/email-agent-<platform>[.exe]
URL: https://assets.amd-gaia.ai/hub/agents/python/email/<version>/email-agent-<platform>[.exe]
```

Platforms: `win32-x64` (`.exe`), `darwin-arm64`, `darwin-x64`, `linux-x64`.

## One command

Stage the per-platform binaries into a folder named `email-agent-<platform>[.exe]`,
then:

```bash
# from the repo root, with rclone remote 'gaia' configured
hub/agents/python/email/packaging/upload_to_r2.sh 0.1.0 ./staging
```

The script:

1. asserts the version matches `package.json` **and** `gaia-agent.yaml` (fails loudly otherwise),
2. hashes each binary it is about to upload (so the lock can never drift from the bytes),
3. `rclone copyto`s each binary + `gaia-agent.yaml` to `gaia:amd-gaia/hub/agents/python/email/<version>/`,
4. regenerates `hub/agents/npm/agent-email/binaries.lock.json` with the real hashes.

Absent platforms keep their existing lock entry, so a **Windows-only** hand-upload
won't wipe the mac/linux entries — upload the rest later and rerun.

Env overrides: `R2_REMOTE` (default `gaia`), `R2_BUCKET` (default `amd-gaia`),
`ASSETS_BASE_URL` (default `https://assets.amd-gaia.ai`).

## Where the binaries come from

PyInstaller does **not** cross-compile, so each platform must be frozen natively
(`freeze.py` — see [`README.md`](README.md)). You can only build `win32-x64` on a
Windows box. For an all-platform release without four machines, let CI build them
and **download its build artifacts**, then rclone them up yourself:

```bash
# download the 4 platform artifacts from a release_agent_email.yml run into ./staging,
# unzip so files are named email-agent-<platform>[.exe], then:
hub/agents/python/email/packaging/upload_to_r2.sh 0.1.0 ./staging
```

This is the clean split: **CI builds, you publish.**

## Verify, then publish npm

```bash
cd hub/agents/npm/agent-email
npm ci && npm run build
node dist/cli.js fetch --out ./verify --platform win32-x64   # --out is required; repeat per platform — downloads + checks SHA-256
npm publish --access public                                  # or let CI do it via OIDC trusted publishing
```

If `fetch` errors with a hash mismatch, the uploaded bytes don't match the lock —
re-run the upload; do **not** hand-edit the lock.

## Manual upload without the script

Equivalent raw commands (the script just wraps these + the hashing + lock regen):

```bash
VER=0.1.0
DEST="gaia:amd-gaia/hub/agents/python/email/$VER"
# --exclude '*.json' keeps the *.meta.json sidecars out of the public dir.
rclone copy ./staging/ "$DEST/" --s3-no-check-bucket --progress --exclude '*.json'
# Upload the manifest too, so the hand path matches CI byte-for-byte.
rclone copyto hub/agents/python/email/gaia-agent.yaml "$DEST/gaia-agent.yaml" \
  --s3-no-check-bucket
python hub/agents/python/email/packaging/gen_binaries_lock.py \
  --base-url "https://assets.amd-gaia.ai/hub/agents/python/email/$VER" \
  --version "$VER" --lock hub/agents/npm/agent-email/binaries.lock.json \
  --meta staging/<platform>.meta.json   # one --meta per platform
```

`--s3-no-check-bucket` is required for Object-Read/Write tokens (no bucket-create
permission) — see [`R2-SETUP.md`](../../../../../scripts/video-demo/R2-SETUP.md).
