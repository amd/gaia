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

        // Handle different content types.
        //
        // For 'error' / 'system' messages we MUST NOT pass through
        // formatMessage + sanitizeInto: those flows include arbitrary
        // exception strings (`Error: ${error.message}`) which CodeQL
        // correctly flags as xss-through-exception / xss-through-dom
        // sinks. Errors / system banners use textContent directly.
        //
        // For user/assistant messages we hand the sanitizer a live target
        // DOM node — it parses, strips dangerous elements/attrs, and
        // appends the sanitized children. We never route the sanitized
        // HTML back through ``innerHTML = str``.
        //
        // Non-string payloads render as JSON via textContent — there is no
        // caller-supplied-DOM-node branch, so nothing caller-controlled is
        // ever appended to the document unsanitized.
        if (typeof content === 'string') {
            if (type === 'error' || type === 'system') {
                contentEl.textContent = content;
            } else {
                this.sanitizeInto(contentEl, this.formatMessage(content));
            }
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
        // HTML-escape FIRST so any <, >, &, ", ' in user input become
        // entities and can't introduce tags. Then apply the markdown-like
        // replacements on the escaped string — our regexes only produce a
        // small fixed set of tags (strong/em/code/br/a), all of which were
        // absent from the escaped source.
        //
        // This means ``html`` passed to sanitizeInto() is derived entirely
        // from our own tag templates plus escaped user text — no untrusted
        // HTML ever reaches the DOMParser sink, which is also what CodeQL
        // (xss-through-dom / xss-through-exception) wants to see.
        const esc = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');

        return esc
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>')
            .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank">$1</a>');
    }

    sanitizeInto(targetEl, html) {
        // URL-bearing attributes where an unsafe scheme could execute script.
        const URL_ATTRS = new Set(['href', 'src', 'xlink:href', 'action', 'formaction']);
        // Schemes that can execute JS in at least one browser. Explicit list
        // (not a regex) so a future reviewer can audit what is blocked.
        const DANGEROUS_SCHEMES = ['javascript:', 'data:', 'vbscript:'];

        // Parse via DOMParser rather than assigning to ``innerHTML``.
        // ``parseFromString`` with the ``text/html`` MIME produces a
        // disconnected document whose <script> tags are never executed (per
        // the HTML parsing spec), and avoids the ``innerHTML =`` sink that
        // CodeQL flagged as xss-through-dom / xss-through-exception.
        const parsed = new DOMParser().parseFromString(html, 'text/html');

        // Remove dangerous elements from the parsed body
        parsed.body
            .querySelectorAll('script,iframe,object,embed,form,input,textarea,link,style,meta,base')
            .forEach(el => el.remove());

        // Remove event handlers and unsafe URL schemes on any URL-bearing attribute
        parsed.body.querySelectorAll('*').forEach(el => {
            [...el.attributes].forEach(attr => {
                const name = attr.name.toLowerCase();
                const value = attr.value.trimStart().toLowerCase();
                const isUnsafeUrl = URL_ATTRS.has(name)
                    && DANGEROUS_SCHEMES.some(s => value.startsWith(s));
                if (name.startsWith('on') || isUnsafeUrl) {
                    el.removeAttribute(attr.name);
                }
            });
        });

        // Move the sanitized child nodes into the target element. No HTML
        // string ever crosses back through an innerHTML assignment.
        targetEl.textContent = '';
        while (parsed.body.firstChild) {
            targetEl.appendChild(parsed.body.firstChild);
        }
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