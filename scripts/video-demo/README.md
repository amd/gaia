# CLI Demo Video Recording Guide

Record CLI demos for GAIA documentation hosted on Mintlify with videos stored on Cloudflare R2.

## Prerequisites

- **ffmpeg**: `winget install ffmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html)
- **rclone**: For uploading to R2 - see [R2-SETUP.md](R2-SETUP.md)

**Tip:** After installing tools with winget, refresh PATH without restarting PowerShell:

```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
```

## Recording Setup

### Terminal Size

Set PowerShell window to a good recording dimension:

```powershell
# Option 1: Balanced (recommended)
$host.UI.RawUI.WindowSize = New-Object System.Management.Automation.Host.Size(100, 30)
$host.UI.RawUI.BufferSize = New-Object System.Management.Automation.Host.Size(100, 3000)

# Option 2: More square
$host.UI.RawUI.WindowSize = New-Object System.Management.Automation.Host.Size(90, 45)
$host.UI.RawUI.BufferSize = New-Object System.Management.Automation.Host.Size(90, 3000)
```

| Size | Aspect | Best For |
|------|--------|----------|
| 100×30 | Wide | Progress bars, logs |
| 90×45 | Square | General demos |
| 80×40 | Square | Compact demos |

### Font Settings

- **Font**: Cascadia Code, Consolas, or JetBrains Mono
- **Size**: 14-18pt (larger = easier to read in video)
- Right-click title bar → Properties → Font

### Recording Tools

- **OBS Studio**: Free, full-featured
- **Windows Game Bar**: `Win+G` → Record
- **ScreenToGif**: Lightweight, exports to GIF/MP4

## Compression

Use the included script to compress recordings:

```powershell
# Basic compression (MP4)
.\compress-video.ps1 -InputPath recording.mp4

# WebM format (smaller, web-optimized)
.\compress-video.ps1 -InputPath recording.mp4 -Format webm

# With cropping (removes border artifacts)
.\compress-video.ps1 -InputPath recording.mp4 -Format webm -Crop

# Custom crop amount (10 pixels from each edge)
.\compress-video.ps1 -InputPath recording.mp4 -Format webm -Crop -CropPixels 10

# Custom output name
.\compress-video.ps1 -InputPath recording.mp4 -Output gaia-init-demo.mp4
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `-InputPath` | (required) | Input video file |
| `-Output` | auto | Output filename |
| `-Format` | `mp4` | Output format (`mp4` or `webm`) |
| `-Quality` | `20` | CRF value (18-35, lower=better) |
| `-Crop` | false | Enable cropping of edges |
| `-CropPixels` | `5` | Pixels to crop from each edge |
| `-Preview` | false | Generate 10-second preview |

### Quality Settings

| CRF Value | Quality | Use Case |
|-----------|---------|----------|
| 18-20 | High | Text/terminal demos (default: 20) |
| 23-25 | Medium | General video |
| 28-30 | Low | Quick previews |

Lower CRF = better quality, larger file.

### Manual Cropping (ffmpeg)

For more control, use ffmpeg directly:

```powershell
# Crop 5 pixels from each edge
ffmpeg -i input.mp4 -vf "crop=iw-10:ih-10:5:5" -c:a copy output.mp4
```

**Format:** `crop=width:height:x:y`
- `iw-10` = input width minus 10 pixels total
- `ih-10` = input height minus 10 pixels total
- `5:5` = start position (pixels from top-left)

## Bulk Conversion

Convert all MP4 files in a folder to compressed WebM:

```powershell
# Basic usage - converts all .mp4 files to .webm
.\bulk-convert.ps1 -InputFolder ./recordings

# Custom output folder
.\bulk-convert.ps1 -InputFolder ./recordings -OutputFolder ./webm

# Enable cropping (removes border artifacts)
.\bulk-convert.ps1 -InputFolder ./recordings -Crop

# Custom crop amount
.\bulk-convert.ps1 -InputFolder ./recordings -Crop -CropPixels 10

# Keep intermediate files (for debugging)
.\bulk-convert.ps1 -InputFolder ./recordings -KeepIntermediate
```

**What it does:**
1. (Optional) Crops pixels from each edge if `-Crop` is specified
2. Compresses to VP9/WebM format
3. Cleans up intermediate files
4. Keeps only: `original.mp4` → `original.webm`

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `-InputFolder` | (required) | Folder with MP4 files |
| `-OutputFolder` | `output` | Where to save WebM files |
| `-Crop` | false | Enable cropping of edges |
| `-CropPixels` | `5` | Pixels to crop from each edge |
| `-Quality` | `20` | CRF value (18-35, lower=better) |
| `-KeepIntermediate` | false | Keep intermediate files |

## Upload to R2

See [R2-SETUP.md](R2-SETUP.md) for detailed setup instructions.

**Quick upload (after setup):**

```bash
rclone copy gaia-init-demo.mp4 r2:amd-gaia/videos/ --s3-no-check-bucket --progress
```

## Checklist

- [ ] Set terminal size (100×30 or 90×45)
- [ ] Increase font size (14-18pt)
- [ ] Clear terminal before recording
- [ ] Record demo
- [ ] Compress with script
- [ ] Upload to R2
- [ ] Embed in MDX (see [R2-SETUP.md](R2-SETUP.md#embed-in-mintlify))
- [ ] Test on docs site
