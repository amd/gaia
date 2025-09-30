// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ChatUI } from './modules/chat-ui.js';
import { ApiClient } from './modules/api-client.js';
import { ResultsPanel } from './modules/results-panel.js';

class JaxDashboardApp {
    constructor() {
        this.apiClient = new ApiClient();
        this.chatUI = new ChatUI();
        this.resultsPanel = new ResultsPanel();
        this.connected = false;
        this.config = null;
        this.initializeApp();
    }

    async initializeApp() {
        // Initialize theme
        this.initializeTheme();

        // Load app config
        try {
            this.config = await this.apiClient.getConfig();
            console.log('App initialized:', this.config);
            this.updateAppInfo(this.config);
        } catch (error) {
            console.error('Failed to load config:', error);
        }

        // Set up event listeners
        this.setupEventListeners();

        // Check connection
        await this.checkConnection();

        // Show welcome message
        this.chatUI.addMessage('Welcome to JAX! Ask me about your JIRA issues, projects, or use the navigation buttons.', 'system');

        // Check connection periodically
        setInterval(() => this.checkConnection(), 30000);
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

    setupEventListeners() {
        // Send message
        const sendBtn = document.getElementById('send-btn');
        const messageInput = document.getElementById('message-input');

        sendBtn.addEventListener('click', () => this.sendMessage());
        messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Clear chat
        const clearChatBtn = document.getElementById('clear-chat');
        if (clearChatBtn) {
            clearChatBtn.addEventListener('click', () => {
                this.chatUI.clearMessages();
            });
        }

        // Navigation buttons with data-command
        document.querySelectorAll('.nav-btn[data-command]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                // Remove active from all nav buttons
                document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
                // Add active to clicked button
                btn.classList.add('active');
                // Send the command to chat
                const command = btn.dataset.command;
                if (command) {
                    this.sendMessage(command);
                }
            });
        });

        // Settings button
        const settingsBtn = document.querySelector('.nav-btn[data-view="settings"]');
        if (settingsBtn) {
            settingsBtn.addEventListener('click', () => {
                document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
                settingsBtn.classList.add('active');
                this.showSettings();
            });
        }

        // Action cards in main content
        document.querySelectorAll('.action-card[data-command]').forEach(card => {
            card.addEventListener('click', (e) => {
                const command = card.dataset.command;
                if (command) {
                    // Update corresponding nav button
                    const navBtn = document.querySelector(`.nav-btn[data-command="${command}"]`);
                    if (navBtn) {
                        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
                        navBtn.classList.add('active');
                    }
                    this.sendMessage(command);
                }
            });
        });

        // Refresh button
        const refreshBtn = document.getElementById('refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                const activeBtn = document.querySelector('.nav-btn.active[data-command]');
                if (activeBtn && activeBtn.dataset.command) {
                    this.sendMessage(activeBtn.dataset.command);
                }
            });
        }
    }

    async sendMessage(messageText = null) {
        const input = document.getElementById('message-input');
        const message = messageText || input.value.trim();

        if (!message) return;

        // Check connection first
        if (!this.connected) {
            this.chatUI.addMessage('Not connected to GAIA MCP Bridge. Please ensure it is running with: gaia mcp start', 'error');
            return;
        }

        // Disable input while processing
        const sendBtn = document.getElementById('send-btn');
        const btnText = sendBtn.querySelector('.btn-text');
        const btnSpinner = sendBtn.querySelector('.btn-spinner');

        input.disabled = true;
        sendBtn.disabled = true;

        // Show spinner
        if (btnText) btnText.style.display = 'none';
        if (btnSpinner) btnSpinner.style.display = 'inline-block';

        // Add user message to chat (only if from input, not from button click)
        if (!messageText) {
            this.chatUI.addMessage(message, 'user');
            input.value = '';
        } else {
            // Show command being executed
            this.chatUI.addMessage(message, 'user');
        }

        try {
            // Send to API
            const response = await this.apiClient.sendMessage(message);
            console.log('API Response:', response);

            // Add assistant response
            this.chatUI.addMessage(response.message, 'assistant');

            // Show results in the middle column
            if (response.data) {
                console.log('Showing results in panel:', response.data);
                this.resultsPanel.show(response.data);
            } else {
                console.log('No data to show in results panel');
            }
        } catch (error) {
            this.chatUI.addMessage(`Error: ${error.message}`, 'error');
            // Check connection again in case it was lost
            await this.checkConnection();
        } finally {
            // Re-enable input and hide spinner
            input.disabled = false;
            sendBtn.disabled = false;

            // Hide spinner, show text
            if (btnText) btnText.style.display = 'inline-block';
            if (btnSpinner) btnSpinner.style.display = 'none';

            input.focus();
        }
    }

    handleNavigation(view) {
        // This method is no longer needed with the new layout
        // Navigation is handled directly in setupEventListeners
    }

    showSettings() {
        // In a browser environment, we can't access import.meta directly
        // It will be replaced by Vite at build time
        const env = this.config?.environment || 'development';
        this.chatUI.addMessage(
            'Settings:\n\n' +
            '• MCP Bridge URL: ' + this.apiClient.mcpBaseUrl + '\n' +
            '• Connected: ' + (this.connected ? 'Yes ✅' : 'No ❌') + '\n' +
            '• Environment: ' + env + '\n' +
            '• Version: ' + (this.config?.version || '1.0.0') + '\n\n' +
            (this.connected
                ? 'Connection is active and healthy.'
                : 'MCP Bridge is not running. Start it with:\n  gaia mcp start'),
            'system'
        );
    }

    async checkConnection() {
        const statusEl = document.getElementById('connection-status');
        const sendBtn = document.getElementById('send-btn');

        try {
            this.connected = await this.apiClient.checkConnection();
        } catch (error) {
            console.error('Connection check failed:', error);
            this.connected = false;
        }

        // Update UI based on connection status
        statusEl.classList.toggle('connected', this.connected);
        statusEl.querySelector('.status-text').textContent =
            this.connected ? 'Connected' : 'Disconnected';

        // Update send button state
        if (!this.connected) {
            sendBtn.title = 'MCP Bridge not connected';
        } else {
            sendBtn.title = 'Send message';
        }

        return this.connected;
    }

    updateAppInfo(config) {
        // Update version in sidebar
        const versionEl = document.querySelector('.sidebar-header .version');
        if (versionEl && config.version) {
            versionEl.textContent = `v${config.version}`;
        }

        // Update page title
        if (config.displayName) {
            document.title = config.displayName;
        }
    }
}

// Initialize app when DOM is ready
console.log('App script loaded, checking DOM state...');

// Check if DOM is already loaded (which happens when script is loaded dynamically)
if (document.readyState === 'loading') {
    console.log('DOM still loading, waiting...');
    document.addEventListener('DOMContentLoaded', () => {
        console.log('DOM ready event fired, initializing app...');
        window.app = new JaxDashboardApp();
    });
} else {
    // DOM is already loaded
    console.log('DOM already loaded, initializing app immediately...');
    window.app = new JaxDashboardApp();
}