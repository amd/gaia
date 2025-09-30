// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronAPI', {
  // System status
  getSystemStatus: () => ipcRenderer.invoke('get-system-status'),
  
  // Status updates from main process
  onStatusUpdate: (callback) => ipcRenderer.on('status-update', callback),
  removeAllListeners: (channel) => ipcRenderer.removeAllListeners(channel),
  
  // GAIA/Python management
  startGaiaPython: () => ipcRenderer.invoke('start-gaia-python'),
  stopGaiaPython: () => ipcRenderer.invoke('stop-gaia-python'),
  
  // MCP Bridge management
  startMcpBridge: () => ipcRenderer.invoke('start-mcp-bridge'),
  stopMcpBridge: () => ipcRenderer.invoke('stop-mcp-bridge'),
  
  // MCP responses
  onMcpResponse: (callback) => ipcRenderer.on('mcp-response', callback),
  
  // JIRA operations
  executeJiraCommand: (command) => ipcRenderer.invoke('execute-jira-command', command),
  getJiraProjects: () => ipcRenderer.invoke('get-jira-projects'),
  getMyIssues: () => ipcRenderer.invoke('get-my-issues'),
  searchJira: (query) => ipcRenderer.invoke('search-jira', query),
  createJiraIssue: (issueData) => ipcRenderer.invoke('create-jira-issue', issueData),
  
  // Application utilities
  openExternalLink: (url) => ipcRenderer.invoke('open-external-link', url),
  showSaveDialog: (options) => ipcRenderer.invoke('show-save-dialog', options),
  showOpenDialog: (options) => ipcRenderer.invoke('show-open-dialog', options)
});