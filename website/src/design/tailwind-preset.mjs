// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GAIA Tailwind preset — maps the CSS variables in tokens.css to utilities.
// Plain ESM, no framework dependency.
//
// Two color shapes (see tokens.css):
//   rgb(var(--x) / <alpha-value>)  -> supports opacity modifiers: text-g-accent/40
//   var(--x)                       -> already rgba(); NO opacity modifier.
//                                     Surfaces and hairlines are translucent so
//                                     a card reads on the canvas and on the
//                                     band; use the -2 variant for "hover".

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
        'g-accent': 'rgb(var(--g-accent) / <alpha-value>)',
        'g-accent2': 'rgb(var(--g-accent2) / <alpha-value>)',
        'g-accent-text': 'rgb(var(--g-accent-text) / <alpha-value>)',
        // Accent fill — same value in both themes, so it can carry white text.
        'g-accent-fill': 'rgb(var(--g-accent-fill) / <alpha-value>)',
        'g-accent-fill2': 'rgb(var(--g-accent-fill2) / <alpha-value>)',
        'g-on-accent': 'rgb(var(--g-on-accent) / <alpha-value>)',
        'g-code-bg': 'rgb(var(--g-code-bg) / <alpha-value>)',
        'g-code-text': 'rgb(var(--g-code-text) / <alpha-value>)',
        'g-code-faint': 'rgb(var(--g-code-faint) / <alpha-value>)',
        'g-code-accent': 'rgb(var(--g-code-accent) / <alpha-value>)',
        'g-code-amber': 'rgb(var(--g-code-amber) / <alpha-value>)',
        'g-focus': 'rgb(var(--g-focus) / <alpha-value>)',

        // Translucent — no opacity modifier.
        'g-surface': 'var(--g-surface)',
        'g-surface2': 'var(--g-surface2)',
        'g-border': 'var(--g-border)',
        'g-border2': 'var(--g-border2)',
        'g-accent-dim': 'var(--g-accent-dim)',
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
        'g-pulse': 'g-pulse 0.5s ease-out 1',
        'g-rise': 'g-rise 0.7s cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'g-marquee': 'g-marquee 34s linear infinite',
      },
    },
  },
};
