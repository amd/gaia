// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Rewrite the maths delimiters models write into the `$$` form remark-math
 * reads with single-dollar maths off. Single dollars stay off because "$5 and
 * $10" is money; a `$...$` span counts as maths only when it holds TeX
 * (a backslash, `^`, `_` or a brace). Code, fenced or inline, is left alone.
 */

const FENCE_RE = /^\s*(```|~~~)/;
const CODE_SPAN_RE = /(`+)[\s\S]*?\1/g;
const TEX_HINT_RE = /[\\^_{}]/;

const dollars = (tex: string) => `$$${tex}$$`;

function convertProse(text: string): string {
    return text
        .replace(/\\\((.+?)\\\)/g, (_m, tex: string) => dollars(tex))
        .replace(/\\\[(.+?)\\\]/g, (_m, tex: string) => dollars(tex))
        .replace(/(^|[^\\$])\$(?![\s$])([^$\n]*?[^\s$\\])\$(?![\d$])/g, (m, lead: string, tex: string) =>
            (TEX_HINT_RE.test(tex) ? lead + dollars(tex) : m));
}

function convertLine(line: string): string {
    const whole = /^\s*(?:\\\[(.+)\\\]|\$\$(.+)\$\$)\s*$/.exec(line);
    if (whole) return `$$\n${(whole[1] ?? whole[2]).trim()}\n$$`;
    if (/^\s*\\[[\]]\s*$/.test(line)) return '$$';
    let out = '';
    let at = 0;
    for (const span of line.matchAll(CODE_SPAN_RE)) {
        out += convertProse(line.slice(at, span.index)) + span[0];
        at = span.index + span[0].length;
    }
    return out + convertProse(line.slice(at));
}

export function normalizeMathDelimiters(text: string): string {
    if (!text.includes('$') && !text.includes('\\')) return text;
    let inFence = false;
    return text.split('\n').map((line) => {
        if (FENCE_RE.test(line)) {
            inFence = !inFence;
            return line;
        }
        return inFence ? line : convertLine(line);
    }).join('\n');
}
