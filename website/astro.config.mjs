// Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  integrations: [tailwind()],
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
