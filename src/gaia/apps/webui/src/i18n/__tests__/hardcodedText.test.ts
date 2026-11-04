// @vitest-environment node
// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The RTL screenshots only cover screens that have a scene, so a whole screen could
// ship in English (#6590, #6591, #6585). This counts English written straight into
// JSX in every component and fails when a file gains any.

import { readdirSync, readFileSync, writeFileSync } from 'fs';
import { join, relative, resolve, sep } from 'path';
import { parseAst } from 'vite';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../..');
const BASELINE = resolve(__dirname, 'hardcodedText.baseline.json');
/** Attributes a person reads or hears. */
const TEXT_ATTRS = new Set(['title', 'placeholder', 'aria-label', 'alt', 'label', 'aria-description']);
/** Two letters in a row is English prose; all-caps names and units (GAIA, NPU, MB) are not. */
const isProse = (s: string) => /[A-Za-z]{2,}/.test(s) && /[a-z]/.test(s);

function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
        const path = join(dir, d.name);
        if (d.isDirectory()) return d.name === '__tests__' ? [] : sources(path);
        return /\.tsx$/.test(d.name) && !/\.test\.tsx$/.test(d.name) ? [path] : [];
    });
}

type Node = { type?: string; [key: string]: unknown };

/** Each English string written straight into JSX: text, a quoted child, or a text attribute. */
function hardcodedText(code: string, file = 'x.tsx'): string[] {
    const found: string[] = [];
    const visit = (node: unknown, inJsx: boolean): void => {
        if (Array.isArray(node)) return node.forEach((n) => visit(n, inJsx));
        if (!node || typeof node !== 'object') return;
        const n = node as Node;
        if (n.type === 'JSXText' && isProse(String(n.value))) found.push(String(n.value).trim());
        if (n.type === 'JSXExpressionContainer' && inJsx) {
            const e = n.expression as Node;
            if (e?.type === 'Literal' && typeof e.value === 'string' && isProse(e.value)) found.push(e.value);
        }
        if (n.type === 'JSXAttribute') {
            const name = (n.name as Node)?.name;
            const v = n.value as Node | null;
            if (TEXT_ATTRS.has(String(name)) && v?.type === 'Literal' && isProse(String(v.value))) {
                found.push(`${String(name)}="${String(v.value)}"`);
            }
            return;
        }
        const childJsx = n.type === 'JSXElement' || n.type === 'JSXFragment';
        for (const [key, value] of Object.entries(n)) {
            if (key !== 'start' && key !== 'end') visit(value, childJsx && key === 'children');
        }
    };
    visit(parseAst(code, { lang: 'tsx' }, file), false);
    return found;
}

function scanAll(): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    for (const path of sources(SRC)) {
        const strings = hardcodedText(readFileSync(path, 'utf-8'), path);
        if (strings.length) out[relative(SRC, path).split(sep).join('/')] = strings;
    }
    return out;
}

describe('English written straight into JSX', () => {
    it('finds text, quoted children and text attributes, not translated ones', () => {
        const code = `const A = () => <div title="Delete chat" aria-label={t('x')} className="row">
            Saved {n} files {'Try again'} {t('ok')} GAIA <b>NPU</b></div>;`;
        expect(hardcodedText(code)).toEqual(['title="Delete chat"', 'Saved', 'files', 'Try again']);
    });

    it('does not grow in any file', () => {
        const found = scanAll();
        // UPDATE_I18N_BASELINE=1 npx vitest run hardcodedText rewrites the counts.
        if (process.env.UPDATE_I18N_BASELINE) {
            const counts = Object.fromEntries(Object.entries(found).sort().map(([f, s]) => [f, s.length]));
            writeFileSync(BASELINE, JSON.stringify(counts, null, 2) + '\n');
        }
        const baseline: Record<string, number> = JSON.parse(readFileSync(BASELINE, 'utf-8'));
        const grew = Object.entries(found)
            .filter(([file, strings]) => strings.length > (baseline[file] ?? 0))
            .map(([file, strings]) => `${file}: ${strings.length} (allowed ${baseline[file] ?? 0}): ${strings.join(' | ')}`);
        expect(grew, 'Put new user-facing text through useT (see .claude/skills/reviewing-agent-ui/SKILL.md)').toEqual([]);
        const shrank = Object.entries(baseline)
            .filter(([file, n]) => (found[file]?.length ?? 0) < n)
            .map(([file, n]) => `${file}: ${found[file]?.length ?? 0} (baseline ${n})`);
        expect(shrank, 'Fewer hard-coded strings: lower the baseline so they cannot come back').toEqual([]);
    });
});
