// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** localhost, *.localhost, 127.0.0.0/8 and ::1: local dev servers, which speak plain http. */
export function isLoopbackHost(hostname: string): boolean {
    const host = hostname.toLowerCase().replace(/\.$/, '');
    return host === 'localhost'
        || host.endsWith('.localhost')
        || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host)
        || host === '[::1]';
}

/** `name:` that starts a URL, as opposed to `host:port`. */
const SCHEME = /^[a-z][a-z0-9+.-]*:(?!\d)/i;

/**
 * Turn what was typed into the address bar into a URL to load, or null when
 * it isn't a web address. A bare host gets https://, or http:// when it is
 * this machine. An explicit scheme passes through untouched, so main's
 * policy can refuse file: and the like with its own message.
 */
export function normalizeAddress(input: string): string | null {
    const text = input.trim();
    if (!text || /\s/.test(text)) return null;
    if (SCHEME.test(text)) return text;
    let url: URL;
    try {
        url = new URL(`http://${text}`);
    } catch {
        return null;
    }
    if (isLoopbackHost(url.hostname)) return url.href;
    const looksLikeHost = url.hostname.includes('.') || url.port !== '';
    if (!looksLikeHost) return null;
    url.protocol = 'https:';
    return url.href;
}
