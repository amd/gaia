// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { getViteConfig } from 'astro/config';

// getViteConfig, not vitest's own defineConfig: it loads astro.config.mjs and
// its plugins, which is what lets a suite `import Downloads from './x.astro'`
// and render it through the container API. Without it a .astro import is an
// unknown file type and the component stays untestable.
export default getViteConfig({
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
