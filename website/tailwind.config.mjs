// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import gaiaPreset from './src/design/tailwind-preset.mjs';

/** @type {import('tailwindcss').Config} */
export default {
  presets: [gaiaPreset],
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  plugins: [],
};
