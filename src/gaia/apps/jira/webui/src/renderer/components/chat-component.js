// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Chat Component
// Enhanced chat interface for JIRA operations

import DomHelpers from '../services/dom-helpers.js';
import apiClient from '../services/api-client.js';
import resultParser from '../services/result-parser.js';

class ChatComponent {
  constructor(containerId, onResultCallback) {
    this.containerId = containerId;
    this.onResultCallback = onResultCallback;
    this.messages = [];
    
    this.initializeComponent();
    this.setupEventListeners();
    this.addWelcomeMessage();
  }

  initializeComponent() {
    const container = DomHelpers.getElementById(this.containerId);
    if (!container) {
      console.error(`Chat container not found: ${this.containerId}`);
      return;
    }

    // Create chat structure
    container.innerHTML = `
      <div class="chat-header">
        <h2>💬 AI Assistant</h2>
        <p class="chat-subtitle">Ask anything about your JIRA projects</p>
      </div>
      
      <div class="chat-content">
        <div id="chat-messages" class="chat-messages"></div>
        
        <div class="chat-input-area">
          <form id="chat-form" class="chat-form">
            <div class="chat-input-group">
              <textarea 
                id="chat-input" 
                class="chat-input" 
                placeholder="Ask me about your JIRA workflow..."
                rows="1"
              ></textarea>
              <button type="submit" class="chat-send-button" id="send-button" aria-label="Send message">
                <svg class="send-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"></line>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                </svg>
                <svg class="loading-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: none;">
                  <circle cx="12" cy="12" r="10"></circle>
                  <path d="M12 6v6l4 2"></path>
                </svg>
              </button>
            </div>
          </form>
        </div>
      </div>
    `;
  }

  setupEventListeners() {
    // Chat form submission
    const chatForm = DomHelpers.getElementById('chat-form');
    DomHelpers.addEventListener(chatForm, 'submit', (e) => {
      e.preventDefault();
      this.sendMessage();
    });

    // Chat input auto-resize and enter key handling
    const chatInput = DomHelpers.getElementById('chat-input');
    DomHelpers.addEventListener(chatInput, 'input', () => {
      this.autoResizeTextarea(chatInput);
    });
    
    // Focus on input when clicking in the input area
    const inputArea = DomHelpers.querySelector('.chat-input-area');
    DomHelpers.addEventListener(inputArea, 'click', (e) => {
      if (e.target === inputArea || e.target.classList.contains('chat-input-group')) {
        chatInput.focus();
      }
    });

    DomHelpers.addEventListener(chatInput, 'keypress', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    // Status updates from main process
    apiClient.onStatusUpdate((_event, message) => {
      this.updateStatus(message);
    });
  }

  addWelcomeMessage() {
    this.addMessage({
      type: 'ai',
      content: `Hello! I'm your JIRA AI assistant. I can help you with:

• **Finding and searching issues** - "Show me high priority bugs" or "Find issues assigned to me"
• **Creating new issues** - "Create a bug for login problem" or "Add a task for user testing"
• **Managing projects** - "What projects are available?" or "Show project details"
• **Analyzing workflows** - "What's my workload?" or "Show sprint progress"

Try one of the quick action buttons above, or just type your request in natural language!`,
      timestamp: new Date()
    });
  }

  async sendMessage(messageText = null) {
    const chatInput = DomHelpers.getElementById('chat-input');
    const sendButton = DomHelpers.getElementById('send-button');
    
    const message = messageText || chatInput.value.trim();
    if (!message) return;

    // Clear input and reset size
    chatInput.value = '';
    this.autoResizeTextarea(chatInput);

    // Add user message
    this.addMessage({
      type: 'user',
      content: message,
      timestamp: new Date()
    });

    // Show loading state with pulsing ellipsis in chat
    this.setLoading(true);

    try {
      // Execute command
      const result = await apiClient.executeJiraCommand(message);
      console.log('Chat received result:', result);
      
      // Parse result for display
      const parsedResult = resultParser.parseResult(result);
      
      // Send to results panel
      if (this.onResultCallback) {
        this.onResultCallback(parsedResult, message);
      }
      
      // Format response for chat
      const response = resultParser.formatChatResponse(parsedResult);
      
      // Add AI response
      this.addMessage({
        type: 'ai',
        content: response,
        timestamp: new Date()
      });

    } catch (error) {
      console.error('Error sending chat message:', error);
      this.addMessage({
        type: 'ai',
        content: `Sorry, I encountered an error processing your request: ${error.message}`,
        timestamp: new Date()
      });
    } finally {
      this.setLoading(false);
      chatInput.focus();
    }
  }

  addMessage(message) {
    const chatMessages = DomHelpers.getElementById('chat-messages');
    if (!chatMessages) return;

    const messageElement = DomHelpers.createElement('div', `chat-message ${message.type}-message`);
    
    const avatar = message.type === 'ai' ? '🤖' : '👤';
    const time = message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    
    messageElement.innerHTML = `
      <div class="message-avatar">${avatar}</div>
      <div class="message-content">
        <div class="message-text">${this.formatMessageContent(message.content)}</div>
        <div class="message-time">${time}</div>
      </div>
    `;

    chatMessages.appendChild(messageElement);
    DomHelpers.scrollToBottom(chatMessages);

    // Store message
    this.messages.push(message);
  }

  formatMessageContent(content) {
    // Convert newlines to <br> and preserve formatting
    return DomHelpers.escapeHtml(content)
      .replace(/\n/g, '<br>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>');
  }

  autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    const newHeight = Math.min(Math.max(textarea.scrollHeight, 40), 120);
    textarea.style.height = newHeight + 'px';
  }

  setLoading(loading) {
    const chatMessages = DomHelpers.getElementById('chat-messages');
    const sendButton = DomHelpers.getElementById('send-button');
    
    if (loading) {
      // Add typing indicator
      const typingIndicator = DomHelpers.createElement('div', 'chat-message ai-message typing-indicator');
      typingIndicator.id = 'typing-indicator';
      typingIndicator.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content">
          <div class="typing-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
      `;
      chatMessages.appendChild(typingIndicator);
      DomHelpers.scrollToBottom(chatMessages);
      
      sendButton.disabled = true;
      const sendIcon = sendButton.querySelector('.send-icon');
      const loadingIcon = sendButton.querySelector('.loading-icon');
      if (sendIcon) sendIcon.style.display = 'none';
      if (loadingIcon) {
        loadingIcon.style.display = 'block';
        loadingIcon.classList.add('spinning');
      }
    } else {
      // Remove typing indicator
      const typingIndicator = DomHelpers.getElementById('typing-indicator');
      if (typingIndicator) {
        typingIndicator.remove();
      }
      
      sendButton.disabled = false;
      const sendIcon = sendButton.querySelector('.send-icon');
      const loadingIcon = sendButton.querySelector('.loading-icon');
      if (sendIcon) sendIcon.style.display = 'block';
      if (loadingIcon) {
        loadingIcon.style.display = 'none';
        loadingIcon.classList.remove('spinning');
      }
    }
  }

  updateStatus(message) {
    const statusText = DomHelpers.querySelector('.chat-status .status-text');
    const statusDot = DomHelpers.querySelector('.chat-status .status-dot');
    
    if (statusText) {
      statusText.textContent = message;
    }
    
    if (statusDot) {
      // Update status dot based on message
      if (message.toLowerCase().includes('ready')) {
        statusDot.className = 'status-dot connected';
      } else if (message.toLowerCase().includes('error') || message.toLowerCase().includes('failed')) {
        statusDot.className = 'status-dot error';
      } else {
        statusDot.className = 'status-dot warning';
      }
    }
  }

  clearChat() {
    const chatMessages = DomHelpers.getElementById('chat-messages');
    DomHelpers.clearContent(chatMessages);
    this.messages = [];
    this.addWelcomeMessage();
  }

  getMessages() {
    return [...this.messages];
  }
}

// Export for use in main app
window.ChatComponent = ChatComponent;
export default ChatComponent;