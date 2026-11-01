// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Copy, share and print for a guide's text. Each throws when the platform refuses. */

export async function copyGuide(text: string): Promise<void> {
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard is not available here');
    await navigator.clipboard.writeText(text);
}

export function canShare(): boolean {
    return typeof navigator !== 'undefined' && typeof navigator.share === 'function';
}

/** Resolves false when the person dismissed the share sheet. */
export async function shareGuide(title: string, text: string): Promise<boolean> {
    try {
        await navigator.share({ title, text });
        return true;
    } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return false;
        throw err;
    }
}

function escapeHtml(text: string): string {
    return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

/** Prints through a hidden iframe so the chat around the card stays off the page. */
export function printGuide(title: string, text: string): void {
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.tabIndex = -1;
    frame.style.position = 'fixed';
    frame.style.width = '0';
    frame.style.height = '0';
    frame.style.border = '0';
    document.body.appendChild(frame);
    const win = frame.contentWindow;
    if (!win) {
        frame.remove();
        throw new Error('Could not open a print frame');
    }
    win.document.open();
    win.document.write(
        `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
        '<style>body{font:14px/1.6 system-ui,sans-serif;margin:32px;}pre{white-space:pre-wrap;font:inherit;}</style>' +
        `</head><body><pre>${escapeHtml(text)}</pre></body></html>`,
    );
    win.document.close();
    const cleanup = () => frame.remove();
    win.addEventListener('afterprint', cleanup, { once: true });
    win.focus();
    win.print();
    // Some engines never fire afterprint for an iframe.
    setTimeout(cleanup, 60_000);
}
