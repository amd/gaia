// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { contextBridge, ipcRenderer } = require('electron');

// Expose safe APIs to the renderer process
contextBridge.exposeInMainWorld('electronAPI', {
  // App info
  getConfig: () => ipcRenderer.invoke('app:getConfig'),
  getVersion: () => ipcRenderer.invoke('app:getVersion'),
  getInfo: () => ipcRenderer.invoke('app:getInfo'),

  // App-specific handlers
  sendMessage: (message) => ipcRenderer.invoke('app:sendMessage', message),
  checkConnection: () => ipcRenderer.invoke('app:checkConnection'),

  // Generic invoke for custom handlers
  invoke: (channel, ...args) => {
    return ipcRenderer.invoke(channel, ...args);
  }
});