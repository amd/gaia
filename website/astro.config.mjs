// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  // applyBaseStyles:false — src/design/global.css owns the @tailwind directives
  // so the g-* component primitives land in the `components` layer and inline
  // utilities still override them.
  integrations: [tailwind({ applyBaseStyles: false })],
  site: 'https://amd-gaia.ai',
  vite: {
    server: {
      allowedHosts: [
        '.ngrok-free.app',
        '.ngrok.io',
        '.ngrok.app',
      ],
    },
  },
});
