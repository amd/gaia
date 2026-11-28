// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SkillInfo } from '../types';
import { findSkillCommand } from './skillCommand';

/**
 * A remark plugin for a sent user message: the skill command that ran is
 * wrapped in a `gaia-skill` element carrying the skill's name and description.
 * Only the first installed skill named is marked, since only that one runs.
 * Code keeps its text in `value`, not in children, so it is never marked.
 */

interface MdNode {
    type: string;
    value?: string;
    children?: MdNode[];
    data?: { hName: string; hProperties: Record<string, string> };
}

function markFirst(node: MdNode, skills: ReadonlyMap<string, SkillInfo>): boolean {
    const children = node.children;
    if (!children) return false;
    for (let i = 0; i < children.length; i++) {
        const child = children[i];
        if (child.type === 'text' && child.value) {
            const word = findSkillCommand(child.value, skills);
            if (!word) continue;
            const skill = skills.get(word.name)!;
            const text = child.value;
            const parts: MdNode[] = [];
            if (word.start > 0) parts.push({ type: 'text', value: text.slice(0, word.start) });
            parts.push({
                type: 'skillCommand',
                data: { hName: 'gaia-skill', hProperties: { name: skill.name, description: skill.description ?? '' } },
                children: [{ type: 'text', value: text.slice(word.start, word.end) }],
            });
            if (word.end < text.length) parts.push({ type: 'text', value: text.slice(word.end) });
            children.splice(i, 1, ...parts);
            return true;
        }
        if (markFirst(child, skills)) return true;
    }
    return false;
}

export function remarkSkillCommand(options: { skills: ReadonlyMap<string, SkillInfo> }) {
    return (tree: MdNode) => {
        markFirst(tree, options.skills);
    };
}
