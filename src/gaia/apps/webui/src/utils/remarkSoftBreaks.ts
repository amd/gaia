// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A remark plugin that turns each newline inside running text into a line
 * break. CommonMark reads a single newline as a space, which joins a poem, an
 * address or "one item per line" output into one paragraph. Hard breaks
 * (two trailing spaces) are already `break` nodes and are left alone; code
 * keeps its own newlines because its text lives in `value`, not in children.
 */

interface MdNode {
    type: string;
    value?: string;
    children?: MdNode[];
}

function splitSoftBreaks(node: MdNode): void {
    if (!node.children) return;
    const out: MdNode[] = [];
    for (const child of node.children) {
        if (child.type === 'text' && child.value?.includes('\n')) {
            child.value.split('\n').forEach((part, i) => {
                if (i > 0) out.push({ type: 'break' });
                if (part) out.push({ type: 'text', value: part });
            });
        } else {
            splitSoftBreaks(child);
            out.push(child);
        }
    }
    node.children = out;
}

export function remarkSoftBreaks() {
    return (tree: MdNode) => {
        splitSoftBreaks(tree);
    };
}
