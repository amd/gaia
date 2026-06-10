// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import gaiaPreset from './src/design/tailwind-preset.mjs';

/** @type {import('tailwindcss').Config} */
export default {
  presets: [gaiaPreset],
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      // TEMP: legacy palette kept while pages migrate to g-* tokens.
      // Removed in the final verification task once no gaia-* class remains.
      colors: {
        'gaia-bg': '#0d0d0d',
        'gaia-card': '#1e1e2e',
        'gaia-accent': '#ED1C24',
        'gaia-accent-hover': '#ff3d44',
        'gaia-text': '#e4e4e7',
        'gaia-muted': '#a1a1aa',
        'gaia-border': '#27272a',
      },
    },
  },
  plugins: [],
};
