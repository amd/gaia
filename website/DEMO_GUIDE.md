# GAIA Website - Demo Guide

Quick guide to run the website locally and demo to stakeholders.

## Prerequisites

- **Node.js v20.19.x** (LTS) - [Download](https://nodejs.org/)
- **GAIA repo** cloned at `C:\Users\14255\Work\gaia3` (for code snippets)

## Quick Start (5 minutes)

### 1. Install Dependencies

```bash
cd C:\Users\14255\Work\Projects\GaiaWebsite\website
npm install
```

If you see rollup errors, run:
```bash
rm -rf node_modules package-lock.json
npm install
```

### 2. Start Development Server

```bash
npm run dev
```

The site will open at **http://localhost:4321**

### 3. Open in Browser

Navigate to http://localhost:4321 and you'll see:
- ✅ Hero section with animated typewriter
- ✅ Code examples with **live snippets from GAIA repo**
- ✅ Tabbed interface (Knowledge, MCP, Coding, Automation)
- ✅ Neural network background animations

## Demo Flow

### For Your Boss:

1. **Show the Hero** (0:30)
   - Point out "100% private" and "local" messaging
   - Highlight the animated text (local/free/private rotation)

2. **Code Snippets** (2:00)
   - Click through tabs: Knowledge → MCP → Coding → Automation
   - **Key point**: "These are the actual code examples from our GitHub repo, synced automatically"
   - Show the typing animation
   - Demonstrate copy-to-clipboard

3. **Installation** (0:30)
   - Toggle between Linux/Windows tabs
   - Show how simple the install is (one curl command)

4. **Technical Highlight** (1:00)
   - "The code you see is pulled directly from our examples folder"
   - "When we update examples, the website auto-updates at build time"
   - "Zero manual copy-paste, always in sync"

## Key Talking Points

✨ **Code Synchronization**
- Website reads actual Python code from `gaia3/examples/`
- No hardcoded snippets = no drift
- Changes to examples automatically reflect on site

🎨 **Visual Design**
- Neural network/circuit board theme
- AMD red accent color (#ED1C24)
- Clean, modern interface inspired by Bun.sh

🚀 **Performance**
- Static site generation (Astro)
- Blazing fast load times
- All code baked in at build time

## Build for Production

When ready to deploy:

```bash
npm run build
npm run preview  # Test production build locally
```

Output goes to `dist/` folder - ready for Cloudflare Pages, Netlify, or any static host.

## Troubleshooting

### "Cannot find example files"

Check that GAIA repo is at the correct path:
```bash
ls ../../../gaia3/examples/weather_agent.py
```

Should show the file. If not, adjust path in `src/utils/codeSnippets.ts`.

### "Module not found" errors

```bash
rm -rf node_modules package-lock.json
npm install
```

### Code snippets not showing

Check browser console (F12) for errors. The snippets load at build time, so rebuild:
```bash
npm run dev  # Restart dev server
```

## Next Steps

After demo approval:

1. ✅ Deploy to Cloudflare Pages (10 min setup)
2. ✅ Point custom domain (amd-gaia.ai)
3. ✅ Set up auto-deploy from Git
4. ✅ Add analytics (optional)

---

**Pro Tip**: Open browser DevTools (F12) and show the Network tab during demo - they'll see it's a static site with instant page loads and no API calls.
