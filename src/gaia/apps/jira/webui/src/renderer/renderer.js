// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// JAX Renderer - Three Column Layout
// Chat-centric interface with sidebar navigation

import DomHelpers from './services/dom-helpers.js';
import apiClient from './services/api-client.js';
import Sidebar from './components/sidebar.js';
import ChatComponent from './components/chat-component.js';
import ResultsPanel from './components/results-panel.js';

class JaxDashboardApp {
  constructor() {
    this.sidebar = null;
    this.chatComponent = null;
    this.resultsPanel = null;
    this.systemStatus = {
      gaiaReady: false,
      pythonBridge: false
    };
    
    this.initialize();
  }

  async initialize() {
    console.log('🚀 Initializing JAX App...');

    try {
      // Initialize theme
      this.initializeTheme();

      // Initialize components
      this.initializeComponents();

      // Setup global event handlers
      this.setupEventHandlers();

      // Check initial system status
      await this.checkSystemStatus();

      console.log('✅ JAX App initialized successfully');
    } catch (error) {
      console.error('❌ Failed to initialize JAX App:', error);
    }
  }

  initializeTheme() {
    // Check for saved theme preference or system preference
    const savedTheme = localStorage.getItem('theme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    const theme = savedTheme || (systemPrefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);

    // Update toggle button icons
    this.updateThemeToggleIcon(theme);

    // Setup theme toggle
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
      themeToggle.addEventListener('click', () => this.toggleTheme());
    }
  }

  toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    this.updateThemeToggleIcon(newTheme);
  }

  updateThemeToggleIcon(theme) {
    const sunIcon = document.querySelector('.sun-icon');
    const moonIcon = document.querySelector('.moon-icon');

    if (theme === 'dark') {
      if (sunIcon) sunIcon.style.display = 'none';
      if (moonIcon) moonIcon.style.display = 'block';
    } else {
      if (sunIcon) sunIcon.style.display = 'block';
      if (moonIcon) moonIcon.style.display = 'none';
    }
  }

  initializeComponents() {
    // Initialize Sidebar Component
    this.sidebar = new Sidebar('sidebar', (command) => {
      // Handle sidebar action clicks by sending to chat
      if (this.chatComponent) {
        this.chatComponent.sendMessage(command);
      }
    });

    // Initialize Chat Component
    this.chatComponent = new ChatComponent('chat-pane', (parsedResult, query) => {
      // Callback to handle results from chat
      this.handleChatResult(parsedResult, query);
    });

    // Initialize Results Panel
    this.resultsPanel = new ResultsPanel('results-pane');

    console.log('📦 Components initialized');
  }

  setupEventHandlers() {
    // Handle status updates from main process
    apiClient.onStatusUpdate((event, message) => {
      console.log('📡 Status update received:', message);
      this.updateConnectionStatus(message);
    });

    // Handle window events
    window.addEventListener('beforeunload', () => {
      this.cleanup();
    });

    console.log('🎧 Event handlers setup complete');
  }

  handleChatResult(parsedResult, query) {
    console.log('💬 Chat result received:', parsedResult.type, query);
    
    // Update results panel with the parsed result
    if (this.resultsPanel) {
      this.resultsPanel.updateResults(parsedResult, query);
    }
  }

  async checkSystemStatus() {
    try {
      const status = await apiClient.getSystemStatus();
      this.systemStatus = status;
      console.log('🔍 System status:', status);
      
      this.updateConnectionIndicator(status.gaiaReady && status.pythonBridge);
    } catch (error) {
      console.error('❌ Error checking system status:', error);
      this.updateConnectionIndicator(false);
    }
  }

  updateConnectionStatus(message) {
    console.log('📊 Connection status update:', message);
    
    // Update header status if it exists
    const statusText = DomHelpers.querySelector('.status-text');
    if (statusText) {
      statusText.textContent = message;
    }
  }

  updateConnectionIndicator(connected) {
    const statusDot = DomHelpers.querySelector('.status-dot');
    const statusText = DomHelpers.querySelector('.status-text');
    
    if (statusDot) {
      statusDot.className = `status-dot ${connected ? 'connected' : 'error'}`;
    }
    
    if (statusText && connected) {
      statusText.textContent = 'Connected';
    } else if (statusText && !connected) {
      statusText.textContent = 'Connecting...';
    }
  }

  showLoading(show = true, message = 'Processing your request...') {
    const overlay = DomHelpers.getElementById('loading-overlay');
    const loadingText = overlay?.querySelector('.loading-text');

    if (overlay) {
      if (show) {
        if (loadingText && message) {
          loadingText.textContent = message;
        }
        DomHelpers.addClass(overlay, 'visible');
      } else {
        DomHelpers.removeClass(overlay, 'visible');
      }
    }
  }

  showLoadingOverlay(message = 'Processing your request...') {
    this.showLoading(true, message);
  }

  hideLoadingOverlay() {
    this.showLoading(false);
  }

  cleanup() {
    console.log('🧹 Cleaning up JAX App...');
    // Component cleanup if needed
  }

  // Public methods for external access (if needed)
  getChatComponent() {
    return this.chatComponent;
  }

  getResultsPanel() {
    return this.resultsPanel;
  }

  getSystemStatus() {
    return this.systemStatus;
  }
}

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
  console.log('🎬 DOM loaded, starting JAX...');
  const app = new JaxDashboardApp();
  window.jiraDashboard = app;
  window.app = app; // Expose as app for easier access
});

// Auto-check system status periodically
setInterval(async () => {
  if (window.jiraDashboard) {
    await window.jiraDashboard.checkSystemStatus();
  }
}, 30000); // Check every 30 seconds