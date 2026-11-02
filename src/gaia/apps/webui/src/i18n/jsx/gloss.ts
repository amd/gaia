// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { jsxs } from 'react/jsx-runtime';
import type { ElementType, ReactNode } from 'react';
import { glossParts } from '../registry';

/** Hosts whose content is text only, or where an HTML element would not render. */
const TEXT_ONLY_HOSTS = new Set([
    'option', 'optgroup', 'title', 'textarea', 'style', 'script',
    'text', 'tspan', 'textPath', 'desc',
]);

/**
 * One wrapper per string keeps a flex parent seeing a single item, as it did the
 * text. Neither tag is a span, so the app's `.x span` rules can't restyle a label.
 */
function glossText(text: string, key?: string): ReactNode {
    const parts = glossParts(text);
    if (!parts) return text;
    const children = parts.map(({ text: part, gloss }, i) =>
        gloss ? jsxs('small', { className: 'i18n-gloss', lang: 'en', children: [part] }, i) : part);
    return jsxs('i18n-label' as ElementType, { children }, key);
}

function glossNode(node: unknown, key?: string): unknown {
    if (typeof node === 'string') return glossText(node, key);
    if (!Array.isArray(node)) return node;
    let copy: unknown[] | undefined;
    node.forEach((child, i) => {
        const next = glossNode(child, `i18n-${i}`);
        if (next === child) return;
        copy ??= [...node];
        copy[i] = next;
    });
    return copy ?? node;
}

/** `props` with any bracketed label among a host element's text children split out. */
export function withGloss<P>(type: ElementType, props: P): P {
    if (typeof type !== 'string' || TEXT_ONLY_HOSTS.has(type)) return props;
    const { children } = props as { children?: unknown };
    const next = glossNode(children);
    return next === children ? props : { ...props, children: next };
}
