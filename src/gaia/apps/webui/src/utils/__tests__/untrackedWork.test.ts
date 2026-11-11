// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import { describeUntracked, groupUntracked, untrackedWarning } from '../untrackedWork';
import type { UntrackedWork } from '../../types/changes';

const py = (detail: string): UntrackedWork => ({ kind: 'run_python', detail, paths: [] });
const sh = (detail: string): UntrackedWork => ({ kind: 'shell', detail, paths: [] });

describe('groupUntracked', () => {
    it('merges identical entries and keeps the order they ran in', () => {
        const groups = groupUntracked([sh('npm test'), py('count errors'), py('count errors'), sh('npm test')]);
        expect(groups.map((g) => g.title)).toEqual(['Command: npm test ×2', 'Python: count errors ×2']);
        expect(groups.map((g) => g.count)).toEqual([2, 2]);
    });

    it('titles a kind with different entries by its count', () => {
        const [group] = groupUntracked([py('a'), py('b'), py('b')]);
        expect(group.title).toBe('Python snippets ×3');
        expect(group.entries).toEqual([
            { label: 'a', detail: 'a', count: 1 },
            { label: 'b', detail: 'b', count: 2 },
        ]);
    });

    it('names an entry without a description by its kind', () => {
        expect(groupUntracked([py('')])[0].title).toBe('Python snippet');
        expect(groupUntracked([{ kind: 'something_new', detail: 'x', paths: [] }])[0].title).toBe('something_new: x');
    });

    it('cuts a long entry to one line and keeps the full text', () => {
        const long = `python -c "${'x'.repeat(300)}"`;
        const [entry] = groupUntracked([sh(long)])[0].entries;
        expect(entry.label).toHaveLength(120);
        expect(entry.label.endsWith('…')).toBe(true);
        expect(entry.detail).toBe(long);
    });
});

describe('untrackedWarning', () => {
    it('counts each kind', () => {
        expect(describeUntracked([py('a'), sh('b'), py('c')])).toBe('2 Python snippets and 1 command');
        expect(describeUntracked([py('a'), sh('b'), { kind: 'mcp', detail: 'x', paths: [] }]))
            .toBe('1 Python snippet, 1 command and 1 connected tool call');
    });

    it('says what undo cannot restore, in the singular for one', () => {
        expect(untrackedWarning([py('a')])).toBe(
            "GAIA ran 1 Python snippet it can't track. If it changed any files, Undo won't restore them.",
        );
        expect(untrackedWarning([py('a'), py('a')], { also: true })).toBe(
            "GAIA also ran 2 Python snippets it can't track. If they changed any files, Undo won't restore them.",
        );
    });
});
