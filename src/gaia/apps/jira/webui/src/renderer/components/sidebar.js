// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Sidebar Component
// Left navigation panel with quick actions and saved queries

import DomHelpers from '../services/dom-helpers.js';

class Sidebar {
  constructor(containerId, onActionCallback) {
    this.containerId = containerId;
    this.onActionCallback = onActionCallback;
    
    this.initializeComponent();
    this.setupEventListeners();
  }

  initializeComponent() {
    const container = DomHelpers.getElementById(this.containerId);
    if (!container) {
      console.error(`Sidebar container not found: ${this.containerId}`);
      return;
    }

    // Create sidebar structure
    container.innerHTML = `
      <div class="sidebar-content">
        <div class="sidebar-section">
          <h3 class="sidebar-title">Quick Actions</h3>
          <div class="sidebar-actions">
            <button class="sidebar-action" data-command="What are my assigned issues?">
              <span class="action-icon">📋</span>
              <span class="action-text">My Issues</span>
            </button>
            <button class="sidebar-action" data-command="Show me high priority bugs">
              <span class="action-icon">🔥</span>
              <span class="action-text">Priority Bugs</span>
            </button>
            <button class="sidebar-action" data-command="Show me blocked issues">
              <span class="action-icon">🚫</span>
              <span class="action-text">Blocked Issues</span>
            </button>
            <button class="sidebar-action" data-command="Find issues created this week">
              <span class="action-icon">📅</span>
              <span class="action-text">This Week</span>
            </button>
          </div>
        </div>
        
        <div class="sidebar-section">
          <h3 class="sidebar-title">Saved Queries</h3>
          <div class="sidebar-actions">
            <div class="sidebar-empty">
              <p>No saved queries yet</p>
              <p class="sidebar-hint">Run a search and save it for quick access</p>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  setupEventListeners() {
    // Handle sidebar action button clicks
    const actionButtons = DomHelpers.querySelectorAll('.sidebar-action[data-command]');
    actionButtons.forEach(btn => {
      DomHelpers.addEventListener(btn, 'click', (e) => {
        e.stopPropagation();
        // Update active state
        const allActionButtons = DomHelpers.querySelectorAll('.sidebar-action');
        allActionButtons.forEach(b => DomHelpers.removeClass(b, 'active'));
        DomHelpers.addClass(btn, 'active');

        // Get command and trigger callback
        const command = btn.dataset.command;
        if (command && this.onActionCallback) {
          this.onActionCallback(command);
        }
      });
    });

    // Handle nav button clicks from the sidebar (if any)
    const navButtons = DomHelpers.querySelectorAll('.nav-btn[data-command]');
    navButtons.forEach(btn => {
      DomHelpers.addEventListener(btn, 'click', (e) => {
        e.stopPropagation();
        // Update active state - remove from ALL nav buttons
        const allNavButtons = DomHelpers.querySelectorAll('.nav-btn');
        allNavButtons.forEach(b => DomHelpers.removeClass(b, 'active'));
        DomHelpers.addClass(btn, 'active');

        // Get command and trigger callback
        const command = btn.dataset.command;
        if (command && this.onActionCallback) {
          this.onActionCallback(command);
        }
      });
    });

    // Handle settings button separately (no data-command)
    const settingsBtn = DomHelpers.querySelector('.nav-btn[data-view="settings"]');
    if (settingsBtn) {
      DomHelpers.addEventListener(settingsBtn, 'click', (e) => {
        e.stopPropagation();
        const allNavButtons = DomHelpers.querySelectorAll('.nav-btn');
        allNavButtons.forEach(b => DomHelpers.removeClass(b, 'active'));
        DomHelpers.addClass(settingsBtn, 'active');
        // Handle settings view if needed
      });
    }

    // Also handle action cards in the main content area
    const actionCards = DomHelpers.querySelectorAll('.action-card[data-command]');
    actionCards.forEach(card => {
      DomHelpers.addEventListener(card, 'click', (e) => {
        e.stopPropagation();
        const command = card.dataset.command;
        if (command && this.onActionCallback) {
          this.onActionCallback(command);
          // Also update the corresponding nav button
          const matchingNavBtn = DomHelpers.querySelector(`.nav-btn[data-command="${command}"]`);
          if (matchingNavBtn) {
            const allNavButtons = DomHelpers.querySelectorAll('.nav-btn');
            allNavButtons.forEach(b => DomHelpers.removeClass(b, 'active'));
            DomHelpers.addClass(matchingNavBtn, 'active');
          }
        }
      });
    });
  }

  setActiveAction(command) {
    const actionButtons = DomHelpers.querySelectorAll('.sidebar-action');
    actionButtons.forEach(btn => {
      if (btn.dataset.command === command) {
        DomHelpers.addClass(btn, 'active');
      } else {
        DomHelpers.removeClass(btn, 'active');
      }
    });
  }
}

// Export for use in main app
window.Sidebar = Sidebar;
export default Sidebar;