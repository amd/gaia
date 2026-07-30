// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Tailwind preset — maps the CSS variables in tokens.css to utilities.
// Consumed by the website now and the Agent UI (src/gaia/apps/webui) in
// Phase 2 via `presets: [gaiaPreset]`. Plain ESM, no framework dependency.
//
// Two color shapes (see tokens.css):
//   rgb(var(--x) / <alpha-value>)  -> supports opacity modifiers: text-g-gold/40
//   var(--x)                       -> already rgba(); NO opacity modifier.
//                                     Surfaces and hairlines are translucent so
//                                     cards let the starfield show through; use
//                                     the -2 variant for the "hover" step.

/** @type {import('tailwindcss').Config} */
export default {
  theme: {
    extend: {
      colors: {
        // Solid — opacity modifiers work.
        'g-bg': 'rgb(var(--g-bg) / <alpha-value>)',
        'g-bg2': 'rgb(var(--g-bg2) / <alpha-value>)',
        'g-text': 'rgb(var(--g-text) / <alpha-value>)',
        'g-muted': 'rgb(var(--g-muted) / <alpha-value>)',
        'g-faint': 'rgb(var(--g-faint) / <alpha-value>)',
        'g-gold': 'rgb(var(--g-gold) / <alpha-value>)',
        'g-gold2': 'rgb(var(--g-gold2) / <alpha-value>)',
        'g-gold-text': 'rgb(var(--g-gold-text) / <alpha-value>)',
        'g-on-gold': 'rgb(var(--g-on-gold) / <alpha-value>)',
        'g-code-bg': 'rgb(var(--g-code-bg) / <alpha-value>)',
        'g-code-text': 'rgb(var(--g-code-text) / <alpha-value>)',

        // Translucent — no opacity modifier.
        'g-surface': 'var(--g-surface)',
        'g-surface2': 'var(--g-surface2)',
        'g-border': 'var(--g-border)',
        'g-border2': 'var(--g-border2)',
        'g-gold-dim': 'var(--g-gold-dim)',
        'g-hdr': 'var(--g-hdr)',
      },
      fontFamily: {
        // Space Grotesk carries the identity — headings and the wordmark only.
        display: ['Space Grotesk', 'Inter', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      maxWidth: {
        'g-content': '1200px',
      },
      borderRadius: {
        'g-card': '14px',
        'g-panel': '12px',
        'g-btn': '9px',
        'g-chip': '10px',
        'g-chip-lg': '13px',
        'g-badge': '5px',
        'g-pill': '100px',
      },
      boxShadow: {
        'g-card': 'var(--g-shadow-card)',
        'g-btn': 'var(--g-shadow-btn)',
        'g-terminal': 'var(--g-shadow-terminal)',
      },
      transitionTimingFunction: {
        // The design's easing curve — used by every hover lift and the entrance.
        'g-out': 'cubic-bezier(0.2, 0.7, 0.2, 1)',
      },
      animation: {
        'g-drift': 'g-drift 7s ease-in-out infinite alternate',
        'g-blink': 'g-blink 1s steps(1) infinite',
        'g-pulse': 'g-pulse 0.5s ease-out 1',
        'g-rise': 'g-rise 0.7s cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'g-marquee': 'g-marquee 34s linear infinite',
      },
      backgroundImage: {
        'g-sky-overlay': 'var(--g-sky-overlay)',
      },
    },
  },
};
