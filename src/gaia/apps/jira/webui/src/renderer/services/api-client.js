// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// API Client
// IPC communication wrapper for renderer process

class ApiClient {
  constructor() {
    this.electronAPI = window.electronAPI;
  }

  // System status
  async getSystemStatus() {
    return await this.electronAPI.getSystemStatus();
  }

  async startGaiaPython() {
    return await this.electronAPI.startGaiaPython();
  }

  async stopGaiaPython() {
    return await this.electronAPI.stopGaiaPython();
  }

  // JIRA operations
  async executeJiraCommand(command) {
    return await this.electronAPI.executeJiraCommand(command);
  }

  async getJiraProjects() {
    return await this.electronAPI.getJiraProjects();
  }

  async getMyIssues() {
    return await this.electronAPI.getMyIssues();
  }

  async searchJira(query) {
    return await this.electronAPI.searchJira(query);
  }

  async createJiraIssue(issueData) {
    return await this.electronAPI.createJiraIssue(issueData);
  }

  // Application management
  async openExternalLink(url) {
    return await this.electronAPI.openExternalLink(url);
  }

  async showSaveDialog(options) {
    return await this.electronAPI.showSaveDialog(options);
  }

  async showOpenDialog(options) {
    return await this.electronAPI.showOpenDialog(options);
  }

  // Event listeners
  onStatusUpdate(callback) {
    this.electronAPI.onStatusUpdate(callback);
  }

  onMcpResponse(callback) {
    this.electronAPI.onMcpResponse(callback);
  }
}

// Export singleton instance
window.apiClient = new ApiClient();
export default window.apiClient;