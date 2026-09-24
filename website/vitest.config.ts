// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // src/design/literals.test.ts reads the stylesheets as text through
    // `import.meta.glob(..., { query: '?raw' })`. With Vitest's default
    // `css: false` every CSS module resolves to an empty string — including
    // the raw ones — which would leave the literal guard sweeping nothing and
    // passing vacuously. The suite asserts the glob is non-empty for the same
    // reason; this is what makes that assertion hold.
    css: true,
  },
});
