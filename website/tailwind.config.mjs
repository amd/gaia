// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      colors: {
        'gaia-bg': '#0d0d0d',
        'gaia-card': '#1e1e2e',
        'gaia-accent': '#ED1C24',
        'gaia-accent-hover': '#ff3d44',
        'gaia-text': '#e4e4e7',
        'gaia-muted': '#a1a1aa',
        'gaia-border': '#27272a',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
