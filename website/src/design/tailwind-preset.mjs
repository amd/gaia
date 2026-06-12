// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Tailwind preset — maps the CSS variables in tokens.css to utilities.
// Consumed by the website now and the Agent UI (src/gaia/apps/webui) in
// Phase 2 via `presets: [gaiaPreset]`. Plain ESM, no framework dependency.

/** @type {import('tailwindcss').Config} */
export default {
  theme: {
    extend: {
      colors: {
        'g-bg': 'rgb(var(--g-bg) / <alpha-value>)',
        'g-surface': 'rgb(var(--g-surface) / <alpha-value>)',
        'g-border': 'rgb(var(--g-border) / <alpha-value>)',
        'g-text': 'rgb(var(--g-text) / <alpha-value>)',
        'g-muted': 'rgb(var(--g-muted) / <alpha-value>)',
        'g-gold': 'rgb(var(--g-gold) / <alpha-value>)',
        'g-gold-text': 'rgb(var(--g-gold-text) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      animation: {
        'g-drift': 'g-drift 7s ease-in-out infinite alternate',
        'g-blink': 'g-blink 1s steps(1) infinite',
        'g-pulse': 'g-pulse 0.5s ease-out 1',
        'g-rise': 'g-rise 0.5s ease-out both',
      },
    },
  },
};
