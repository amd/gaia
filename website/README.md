# GAIA Website

Developer-focused landing page for GAIA - Local AI Agents framework.

## Tech Stack

- **Framework**: [Astro](https://astro.build) (static, fast)
- **Styling**: [Tailwind CSS](https://tailwindcss.com)
- **Hosting**: Cloudflare Pages (recommended)

## Development

```bash
# Install dependencies
npm install

# Start dev server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## Project Structure

```
website/
├── public/              # Static assets (favicon, robots.txt)
├── src/
│   ├── components/      # Astro components
│   │   ├── Header.astro
│   │   ├── Hero.astro
│   │   ├── WhyNotX.astro
│   │   ├── CodeExamples.astro
│   │   ├── Benchmarks.astro
│   │   ├── BuiltWith.astro
│   │   ├── Integrations.astro
│   │   ├── QuickStart.astro
│   │   ├── TrustSignals.astro
│   │   └── Footer.astro
│   ├── layouts/
│   │   └── Layout.astro # Base HTML layout
│   └── pages/
│       └── index.astro  # Landing page
├── astro.config.mjs
├── tailwind.config.mjs
└── package.json
```

## Design System

- **Background**: `#0d0d0d`
- **Card Background**: `#1e1e2e`
- **Accent (AMD Red)**: `#ED1C24`
- **Text**: `#e4e4e7`
- **Muted Text**: `#a1a1aa`
- **Font (Code)**: JetBrains Mono
- **Font (UI)**: Inter

## Deployment

### Railway (Recommended for Quick Deploy)

See **[RAILWAY_DEPLOY.md](./RAILWAY_DEPLOY.md)** for detailed instructions.

**Quick setup:**
1. Push code to `github.com/amd/gaia-website`
2. Connect Railway to your GitHub repo
3. Railway auto-detects Astro and deploys
4. Set custom domain to `amd-gaia.ai`

**Auto-deploy:** Push to GitHub → Railway builds and deploys automatically

### Cloudflare Pages (Recommended for Production)

1. Connect your repository to Cloudflare Pages
2. Set build command: `npm run build`
3. Set output directory: `dist`
4. Deploy

**Benefits:** Faster global CDN, unlimited bandwidth, better DDoS protection

### Manual Deploy

```bash
npm run build
# Upload contents of dist/ to your hosting provider
```

## Assets Needed

Before launch, add these assets to `public/`:

- [ ] `og-image.png` (1200x630) - Social share image
- [ ] Integration logos (VS Code, Blender, Jira, Docker)
- [ ] GAIA logo SVG (if different from favicon)

## License

MIT License - Copyright (C) 2024-2026 Advanced Micro Devices, Inc.
