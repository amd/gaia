# Cloudflare R2 Setup Guide

Setup guide for uploading videos to Cloudflare R2 for GAIA documentation.

## Prerequisites

- **rclone**: For uploading files
  - Windows: `winget install Rclone.Rclone`
  - Linux: `curl https://rclone.org/install.sh | sudo bash`
  - See [rclone.org/install](https://rclone.org/install/) for more options

**Tip:** After installing with winget, refresh PATH without restarting PowerShell:

```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
```

## R2 Bucket Setup (One-Time)

### Create Bucket

```bash
wrangler r2 bucket create amd-gaia
```

Or via Cloudflare Dashboard: R2 → Create bucket

### Enable Public Access

1. Cloudflare Dashboard → R2 → amd-gaia
2. Settings → Public access → Allow access
3. Note your public URL: `https://pub-xxxx.r2.dev`

### Custom Domain

1. Cloudflare Dashboard → R2 → amd-gaia → **Settings**
2. Scroll to **Custom Domains** → Click **Connect Domain**
3. Enter: `assets.amd-gaia.ai`
4. Click **Continue** → **Connect Domain**

If `amd-gaia.ai` is already on Cloudflare, DNS is auto-configured. May take a few minutes to propagate.

**Public URL:** `https://assets.amd-gaia.ai/`

## Get R2 API Credentials

Before configuring rclone, you need API credentials from Cloudflare:

1. Go to [Cloudflare Dashboard](https://dash.cloudflare.com/) → R2
2. Click **Manage R2 API Tokens** (right sidebar)
3. Click **Create API Token**
4. Select **Object Read & Write** permission for your bucket
5. Click **Create API Token**
6. **Copy and save both values** (shown only once!):
   - **Access Key ID** (shorter, ~32 characters)
   - **Secret Access Key** (longer, ~64 characters)
7. Note your **Account ID** from the dashboard URL: `dash.cloudflare.com/<account_id>/r2`

## Configure rclone

Run the interactive config:

```bash
rclone config
```

### Step-by-Step Configuration

| Prompt | Enter | Explanation |
|--------|-------|-------------|
| `n/s/q>` | `n` | Create a **n**ew remote |
| `name>` | `r2` | Name for this remote (use in commands like `r2:bucket/`) |
| `Storage>` | `4` or `s3` | S3-compatible storage (R2 uses S3 API) |
| `provider>` | `6` or `Cloudflare` | Cloudflare R2 Storage |
| `env_auth>` | Enter (false) | We'll enter credentials manually, not from environment |
| `access_key_id>` | Your Access Key ID | The shorter key from Cloudflare |
| `secret_access_key>` | Your Secret Access Key | The longer key from Cloudflare |
| `region>` | Enter (leave empty) | R2 doesn't use AWS regions |
| `endpoint>` | `https://<account_id>.r2.cloudflarestorage.com` | **Full URL** with your Account ID |
| `acl>` | Enter (empty) | R2 doesn't support ACLs; public access is set in Cloudflare |
| `server_side_encryption>` | Enter (none) | Not needed for public video files |
| `sse_kms_key_id>` | Enter (none) | Only needed if using KMS encryption |
| `storage_class>` | Enter (default) | R2 doesn't use AWS storage classes |
| `Edit advanced config?` | `n` | Advanced options not needed |
| `y/e/d>` | `y` | Confirm and save |

### Common Mistakes to Avoid

| Mistake | Problem | Correct Value |
|---------|---------|---------------|
| Entering credentials at `env_auth>` | Expects true/false, not a key | Press Enter or type `1` |
| Just the Account ID for endpoint | Missing URL prefix | `https://<account_id>.r2.cloudflarestorage.com` |
| Selecting AWS regions | R2 doesn't use regions | Leave empty (press Enter) |
| Setting provider to AWS | Wrong provider | Select `6` (Cloudflare) |

### Example Final Configuration

```
Options:
- type: s3
- provider: Cloudflare
- access_key_id: <your_access_key_id>
- secret_access_key: <your_secret_access_key>
- endpoint: https://<your_account_id>.r2.cloudflarestorage.com
```

**Note:** Provider should show `Cloudflare`, not `AWS`. If it shows `AWS`, edit the remote and fix it.

### Verify Setup

```bash
# List bucket contents (works with Object Read/Write token)
rclone ls r2:amd-gaia/

# List all buckets (requires Admin token)
rclone lsd r2:
```

If `rclone ls r2:amd-gaia/` returns without error, the setup is correct (empty bucket = no output).

## Upload Files

### Using rclone (recommended)

```bash
# Upload single file
rclone copy video.mp4 r2:amd-gaia/videos/ --s3-no-check-bucket

# Upload with progress
rclone copy video.mp4 r2:amd-gaia/videos/ --s3-no-check-bucket --progress

# List files in bucket
rclone ls r2:amd-gaia/videos/
```

**Note:** The `--s3-no-check-bucket` flag is required if your API token has Object Read/Write permissions only (not Admin).

### Using wrangler

```bash
wrangler r2 object put amd-gaia/videos/video.mp4 --file=video.mp4
```

### Using Cloudflare Dashboard

1. Go to R2 → Your bucket
2. Click "Upload"
3. Drag and drop file

## Embed in Mintlify

Add to your MDX documentation:

```html
<!-- Basic video with controls -->
<video
  controls
  className="w-full rounded-lg"
  src="https://assets.amd-gaia.ai/videos/gaia-init-demo.mp4"
/>

<!-- Autoplay loop (GIF-like) -->
<video
  autoPlay
  loop
  muted
  playsInline
  className="w-full rounded-lg"
  src="https://assets.amd-gaia.ai/videos/gaia-init-demo.mp4"
/>

<!-- With poster image -->
<video
  controls
  poster="https://assets.amd-gaia.ai/videos/gaia-init-poster.png"
  className="w-full rounded-lg"
  src="https://assets.amd-gaia.ai/videos/gaia-init-demo.mp4"
/>
```

## Troubleshooting

### "Endpoint wrong" error

Edit the config file directly:

```powershell
# Windows
notepad $env:APPDATA\rclone\rclone.conf

# Linux/Mac
nano ~/.config/rclone/rclone.conf
```

Ensure endpoint is the full URL:

```ini
[r2]
type = s3
provider = Cloudflare
access_key_id = your_access_key
secret_access_key = your_secret_key
endpoint = https://your_account_id.r2.cloudflarestorage.com
```

### Provider shows "AWS" instead of "Cloudflare"

Edit the remote:

```bash
rclone config
# Select 'e' to edit
# Select your remote
# At provider prompt, enter '6' for Cloudflare
```

### Test connection

```bash
rclone lsd r2:
rclone ls r2:amd-gaia/
```

### "CreateBucket: Access Denied" when uploading

This happens because rclone tries to verify/create the bucket, but your token doesn't have admin permissions. Add the `--s3-no-check-bucket` flag:

```bash
rclone copy video.mp4 r2:amd-gaia/videos/ --s3-no-check-bucket
```

### "Access Denied" errors

1. Check your API token has **Object Read & Write** permissions
2. Verify the bucket name matches exactly (case-sensitive)
3. Use `--s3-no-check-bucket` flag for uploads
4. Regenerate API token if needed
