// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export class ChatUI {
    constructor() {
        this.messagesContainer = document.getElementById('messages');
    }

    addMessage(content, type = 'assistant') {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${type}`;

        const headerEl = document.createElement('div');
        headerEl.className = 'message-header';
        headerEl.textContent = type === 'user' ? 'You' :
                              type === 'error' ? 'Error' :
                              type === 'system' ? 'System' : 'JAX Assistant';

        const contentEl = document.createElement('div');
        contentEl.className = 'message-content';

        // Handle different content types
        if (typeof content === 'string') {
            contentEl.innerHTML = this.sanitizeHTML(this.formatMessage(content));
        } else if (content instanceof HTMLElement) {
            contentEl.appendChild(content);
        } else {
            contentEl.textContent = JSON.stringify(content, null, 2);
        }

        messageEl.appendChild(headerEl);
        messageEl.appendChild(contentEl);
        this.messagesContainer.appendChild(messageEl);

        // Scroll to bottom
        this.scrollToBottom();
    }

    formatMessage(text) {
        // Convert markdown-like formatting to HTML
        return text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>')
            .replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank">$1</a>');
    }

    sanitizeHTML(html) {
        const div = document.createElement('div');
        div.innerHTML = html;
        // Remove dangerous elements
        const dangerous = div.querySelectorAll('script,iframe,object,embed,form,input,textarea,link,style,meta,base');
        dangerous.forEach(el => el.remove());
        // Remove event handlers and javascript: URLs
        div.querySelectorAll('*').forEach(el => {
            [...el.attributes].forEach(attr => {
                if (attr.name.startsWith('on') || (attr.name === 'href' && attr.value.trimStart().toLowerCase().startsWith('javascript:'))) {
                    el.removeAttribute(attr.name);
                }
            });
        });
        return div.innerHTML;
    }

    clearMessages() {
        this.messagesContainer.innerHTML = '';
        this.addMessage('Chat cleared. How can I help you with your JIRA tasks today?', 'system');
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    showTypingIndicator() {
        const indicator = document.createElement('div');
        indicator.className = 'message assistant typing';
        indicator.id = 'typing-indicator';
        indicator.innerHTML = `
            <div class="message-header">JAX Assistant</div>
            <div class="message-content">
                <span class="loading"><span></span></span>
                <span>Thinking</span>
            </div>
        `;
        this.messagesContainer.appendChild(indicator);
        this.scrollToBottom();
    }

    hideTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) {
            indicator.remove();
        }
    }
}