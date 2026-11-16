// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useConnectionStore } from '@/state/connection';

import { ROUTE_ACCESS, type RouteAccessClass } from './routeAccess.gen';

/** What the device listener refuses a paired phone; ui/security.py decides it. */
const REFUSED_ON_DEVICE: ReadonlySet<RouteAccessClass> = new Set(['pc', 'not_on_listener']);

interface Template {
    method: string;
    pattern: RegExp;
    /** Literal characters, so `/conversations/search` wins over `/conversations/{session_id}` as in FastAPI. */
    literal: number;
    access: RouteAccessClass;
}

function escape(text: string): string {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const TEMPLATES: Template[] = Object.entries(ROUTE_ACCESS).map(([key, access]) => {
    const [method, template] = key.split(' ', 2) as [string, string];
    const parts = template.split(/(\{[^}]+\})/);
    const source = parts
        .map((part) => (part.startsWith('{') ? (part.endsWith(':path}') ? '.+' : '[^/]+') : escape(part)))
        .join('');
    const literal = parts.filter((part) => !part.startsWith('{')).join('').length;
    return { method, pattern: new RegExp(`^${source}$`), literal, access };
});

/** The class of the route a request would reach, or null for a path the backend has no route for. */
export function routeAccess(method: string, path: string): RouteAccessClass | null {
    const verb = method.toUpperCase();
    const bare = path.split(/[?#]/, 1)[0]!;
    let best: Template | null = null;
    for (const t of TEMPLATES) {
        if (t.method === verb && t.pattern.test(bare) && (!best || t.literal > best.literal)) best = t;
    }
    return best?.access ?? null;
}

/** True when the device listener refuses this request: a paired phone can't make it. */
export function isDeviceRefused(method: string, path: string): boolean {
    const access = routeAccess(method, path);
    return access !== null && REFUSED_ON_DEVICE.has(access);
}

/** True on a paired phone, whose requests reach the device listener. */
export function usePinned(): boolean {
    return useConnectionStore((s) => !!s.connection?.pinned);
}

/** True when this phone is paired and the device listener refuses the request. */
export function useDeviceRefused(method: string, path: string): boolean {
    return usePinned() && isDeviceRefused(method, path);
}
