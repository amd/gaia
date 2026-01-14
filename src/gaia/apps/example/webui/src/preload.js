// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { contextBridge } = require('electron');

// Expose environment variables to the renderer process
contextBridge.exposeInMainWorld('ENV', {
  GAIA_MCP_URL: process.env.GAIA_MCP_URL || 'http://localhost:8765'
});