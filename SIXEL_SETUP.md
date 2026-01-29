# Enabling Sixel Graphics in Windows Terminal

Sixel allows full-resolution image display directly in the terminal (not pixelated blocks).

## Current Status

**What you see now:** Unicode block characters (▄) - low resolution preview
**With Sixel enabled:** Actual high-resolution image in terminal

---

## Option 1: Windows Terminal Preview (Recommended)

### 1. Install Windows Terminal Preview

Download from Microsoft Store or:
```powershell
winget install Microsoft.WindowsTerminal.Preview
```

### 2. Enable Sixel (Experimental)

1. Open Windows Terminal Preview
2. Press `Ctrl+,` to open Settings
3. Click "Open JSON file" (bottom left)
4. Add to your profile:

```json
{
    "profiles": {
        "defaults": {
            "experimental.rendering.software": false,
            "experimental.pixelShaderPath": null
        },
        "list": [
            {
                "guid": "{574e775e-4f2a-5b96-ac1e-a2962a402336}",
                "name": "PowerShell",
                "commandline": "pwsh.exe -NoLogo",
                "experimental.pixelShaderEffects": {
                    "sixel": true
                }
            }
        ]
    }
}
```

3. Save and restart Windows Terminal Preview

### 3. Verify Sixel Support

```bash
# In Windows Terminal Preview
python -c "
from term_image.image import from_file, AutoImage
from pathlib import Path
img_path = '.gaia/cache/sd/images/test_image_SD-Turbo_20260129_143306.png'
img = from_file(img_path)
print(f'Type: {type(img).__name__}')
# Should print 'GraphicsImage' if Sixel is enabled
# Will print 'BlockImage' if falling back to blocks
"
```

---

## Option 2: Alternative Terminals with Built-in Sixel

### WezTerm (Best Sixel Support on Windows)

```powershell
winget install wez.wezterm
```

WezTerm has excellent Sixel support out of the box. Run `gaia sd` in WezTerm for full-res images.

### Kitty (Linux/macOS)

```bash
# Linux
sudo apt install kitty

# macOS
brew install kitty
```

---

## Option 3: Current Approach (Works Now)

The current implementation:
1. Shows Unicode block preview (works everywhere)
2. Prompts to open in default image viewer

**For your demo:**
Just press `Y` when prompted and the image opens in Windows Photo Viewer (full quality).

---

## Why Sixel Isn't Enabled by Default

- **Windows Terminal** stable doesn't support Sixel yet (coming in future updates)
- **PowerShell** uses whatever terminal it runs in
- **Windows Terminal Preview** has experimental Sixel but disabled by default

---

## Quick Test

Try in WezTerm if you want to see Sixel working now:

```bash
# Install WezTerm
winget install wez.wezterm

# Open WezTerm and run
cd C:\Users\14255\Work\gaia7
.venv\Scripts\activate
gaia sd "test" --sd-model SD-Turbo
```

You should see the actual image rendered inline (not blocks)!

---

## For Your Demo

**Recommendation:** Keep using standard Windows Terminal with the current approach:
1. Shows nice block preview (good enough to see composition)
2. Opens full image in viewer when you press Y

This works reliably across all systems without requiring beta software.

The block preview aspect ratio is now fixed (no more horizontal stretching).
