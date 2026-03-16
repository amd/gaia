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

        // Handle different content types safely
        if (typeof content === 'string') {
            this.renderFormattedMessage(contentEl, content);
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

    /**
     * Render formatted message content safely using DOM methods.
     * Avoids innerHTML to prevent XSS from untrusted content.
     */
    renderFormattedMessage(container, text) {
        // Split text into segments: plain text, bold, italic, code, links, newlines
        // Tokenize raw text — all output uses textContent (auto-escapes HTML)
        const tokens = this.tokenize(text);
        for (const token of tokens) {
            if (token.type === 'bold') {
                const el = document.createElement('strong');
                el.textContent = token.text;
                container.appendChild(el);
            } else if (token.type === 'italic') {
                const el = document.createElement('em');
                el.textContent = token.text;
                container.appendChild(el);
            } else if (token.type === 'code') {
                const el = document.createElement('code');
                el.textContent = token.text;
                container.appendChild(el);
            } else if (token.type === 'link') {
                const el = document.createElement('a');
                el.href = token.url;
                el.target = '_blank';
                el.rel = 'noopener noreferrer';
                el.textContent = token.text;
                container.appendChild(el);
            } else if (token.type === 'newline') {
                container.appendChild(document.createElement('br'));
            } else {
                container.appendChild(document.createTextNode(token.text));
            }
        }
    }

    /**
     * Tokenize text into typed segments for safe DOM rendering.
     * All output uses textContent which auto-escapes HTML.
     * Matches bold (**text**), italic (*text*), code (`text`), URLs, and newlines.
     */
    tokenize(text) {
        const tokens = [];
        // Combined regex for all inline formatting and URLs
        const pattern = /(\*\*(.*?)\*\*)|(\*(.*?)\*)|(`(.*?)`)|(\n)|(https?:\/\/[^\s]+)/g;
        let lastIndex = 0;
        let match;
        while ((match = pattern.exec(text)) !== null) {
            // Add any plain text before this match
            if (match.index > lastIndex) {
                tokens.push({ type: 'text', text: (text.slice(lastIndex, match.index)) });
            }
            if (match[1]) {
                // Bold: **text**
                tokens.push({ type: 'bold', text: (match[2]) });
            } else if (match[3]) {
                // Italic: *text*
                tokens.push({ type: 'italic', text: (match[4]) });
            } else if (match[5]) {
                // Code: `text`
                tokens.push({ type: 'code', text: (match[6]) });
            } else if (match[7]) {
                // Newline
                tokens.push({ type: 'newline' });
            } else if (match[0].match(/^https?:\/\//)) {
                // URL - only allow http/https schemes
                const url = (match[0]);
                tokens.push({ type: 'link', text: url, url: url });
            }
            lastIndex = match.index + match[0].length;
        }
        // Add remaining plain text
        if (lastIndex < text.length) {
            tokens.push({ type: 'text', text: (text.slice(lastIndex)) });
        }
        return tokens;
    }



    clearMessages() {
        while (this.messagesContainer.firstChild) {
            this.messagesContainer.removeChild(this.messagesContainer.firstChild);
        }
        this.addMessage('Chat cleared. How can I help you with your JIRA tasks today?', 'system');
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    showTypingIndicator() {
        const indicator = document.createElement('div');
        indicator.className = 'message assistant typing';
        indicator.id = 'typing-indicator';

        const header = document.createElement('div');
        header.className = 'message-header';
        header.textContent = 'JAX Assistant';

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        const loadingSpan = document.createElement('span');
        loadingSpan.className = 'loading';
        loadingSpan.appendChild(document.createElement('span'));

        const thinkingSpan = document.createElement('span');
        thinkingSpan.textContent = 'Thinking';

        contentDiv.appendChild(loadingSpan);
        contentDiv.appendChild(thinkingSpan);
        indicator.appendChild(header);
        indicator.appendChild(contentDiv);

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