// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Example local configuration override
// Copy this file to 'config.local.js' and modify as needed
// config.local.js is gitignored and won't be committed

// Example: Override MCP Bridge URL for remote access
window.APP_CONFIG = Object.assign(window.APP_CONFIG || {}, {
    MCP_BASE_URL: 'https://770f708428c8.ngrok.app',
    DEBUG: true
});