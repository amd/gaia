// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// JAX Configuration
//
// To override these settings locally:
// 1. Create a file called 'config.local.js' in the same directory
// 2. Override any settings you want, e.g.:
//    window.APP_CONFIG = { MCP_BASE_URL: 'https://your-ngrok-url.ngrok-free.app' };
// 3. The config.local.js file is gitignored and won't be committed

window.APP_CONFIG = {
    // GAIA MCP Bridge URL
    MCP_BASE_URL: 'http://localhost:8765',

    // Enable debug logging in console
    DEBUG: false,

    // Application environment
    ENVIRONMENT: 'development'
};