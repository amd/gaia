// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Copying text, through the Clipboard API or the older textarea route. */

function copyWithTextarea(text: string): boolean {
    if (typeof document.execCommand !== 'function') return false;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    try {
        textarea.select();
        return document.execCommand('copy');
    } catch {
        return false;
    } finally {
        textarea.remove();
        previous?.focus();
    }
}

/**
 * Puts *text* on the clipboard. Tries the Clipboard API, then the textarea
 * route (plain-HTTP pages have no Clipboard API). Rejects when both fail, with
 * the Clipboard API's reason when it gave one.
 */
export async function copyText(text: string): Promise<void> {
    let refusal: unknown = null;
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return;
        } catch (err) {
            refusal = err;
        }
    }
    if (copyWithTextarea(text)) return;
    throw refusal ?? new Error('Clipboard is not available here');
}
