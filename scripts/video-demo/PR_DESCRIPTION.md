# Add Video Demo Recording Tools and CLI Documentation Updates

## Summary

- Add video recording and compression scripts for GAIA documentation
- Add demo video for `gaia init` command to CLI reference
- Fix CLI documentation to match actual `gaia init` implementation

## Changes

### New: Video Demo Scripts (`scripts/video-demo/`)

PowerShell scripts for recording, compressing, and uploading CLI demo videos:

| File | Description |
|------|-------------|
| `compress-video.ps1` | Compress single video with H.264/VP9 encoding |
| `bulk-convert.ps1` | Batch convert MP4 folder to WebM |
| `README.md` | Recording setup, terminal sizing, compression guide |
| `R2-SETUP.md` | Cloudflare R2 setup and rclone configuration |

**Features:**
- H.264 (MP4) and VP9 (WebM) encoding with configurable CRF quality
- Optional edge cropping to remove recording artifacts
- Bulk conversion with progress display
- Download speed and compression ratio reporting

### New: Demo Video in CLI Reference

Added `gaia init` demo video to `docs/reference/cli.mdx`:
- Hosted on Cloudflare R2: `https://assets.amd-gaia.ai/videos/gaia-init.webm`
- Autoplay, loop, muted for seamless documentation experience

### Fixed: CLI Documentation Accuracy

Updated `gaia init` documentation to match actual implementation:

**Options:**
- Added missing `--minimal` flag (shortcut for `--profile minimal`)
- Added missing `--remote` flag (for remote Lemonade servers)
- Improved `--remote` description to mention version checking

**Profiles:**
| Profile | Fix |
|---------|-----|
| `minimal` | Size: ~4 GB -> ~2.5 GB |
| `chat` | Model: Qwen2.5-VL -> Qwen2.5-VL-7B |
| `rag` | Size: ~30 GB -> ~25 GB, added "and vision" to description |
| `all` | Size: ~35 GB -> ~26 GB |

**Other fixes:**
- Added note about default CPU model (`Qwen2.5-0.5B`) included in all profiles
- Fixed "What It Does" steps to match actual code flow
- Corrected "Corrupted Model Detection" tip (manual intervention, not automatic)

## Test plan

- [ ] Run `compress-video.ps1` with test video
- [ ] Run `bulk-convert.ps1` with test folder
- [ ] Verify video plays at https://assets.amd-gaia.ai/videos/gaia-init.webm
- [ ] Verify CLI docs render correctly on Mintlify
- [ ] Compare `gaia init --help` output with documentation

---

Generated with [Claude Code](https://claude.com/claude-code)
