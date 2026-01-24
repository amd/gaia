# Add Video Demo Scripts

## Summary

Add tools for recording, compressing, and uploading CLI demo videos for GAIA documentation.

## Changes

### New: `scripts/video-demo/`

| File | Description |
|------|-------------|
| `compress-video.ps1` | Compress single video (H.264/VP9) |
| `compress-video.sh` | Linux/macOS version |
| `bulk-convert.ps1` | Batch convert MP4 to WebM |
| `README.md` | Recording setup guide |
| `R2-SETUP.md` | Cloudflare R2 upload setup |

### Updated: `docs/reference/cli.mdx`

- Added `gaia init` demo video
- Minor documentation accuracy fixes

## Test Plan

- [x] Video plays at https://assets.amd-gaia.ai/videos/gaia-init.webm
- [ ] Test compression scripts locally

---

Generated with [Claude Code](https://claude.com/claude-code)
