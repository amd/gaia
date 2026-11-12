// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A remark plugin that marks each table column whose body cells are all
 * numbers ("47.70", "$1,200", "-3%", "12 GB"), so it can line up on its end
 * edge. A column the markdown aligns itself keeps that alignment; any other
 * column stays at the start edge, so a column of prose reads normally.
 */

interface MdNode {
    type: string;
    value?: string;
    align?: (string | null)[] | null;
    children?: MdNode[];
    data?: { hProperties?: Record<string, unknown> };
}

const NUMBER_RE = /^[-+−]?[$€£¥]?\s?\d[\d,.\s]*\s?(?:%|[kKmMbB]|[KMGT]?B|[$€£¥])?$/;
const BLANK_RE = /^[-—–]?$/;

function textOf(node: MdNode): string {
    if (node.value !== undefined) return node.value;
    return (node.children ?? []).map(textOf).join('');
}

function markTable(table: MdNode): void {
    const rows = table.children ?? [];
    const body = rows.slice(1);
    if (body.length === 0) return;
    const columns = Math.max(...rows.map((r) => r.children?.length ?? 0));
    for (let col = 0; col < columns; col++) {
        if (table.align?.[col]) continue;
        const cells = body.map((r) => textOf(r.children?.[col] ?? { type: 'text', value: '' }).trim());
        const filled = cells.filter((c) => !BLANK_RE.test(c));
        if (filled.length === 0 || !filled.every((c) => NUMBER_RE.test(c))) continue;
        for (const row of rows) {
            const cell = row.children?.[col];
            if (cell) cell.data = { ...cell.data, hProperties: { ...cell.data?.hProperties, className: 'is-numeric' } };
        }
    }
}

function walk(node: MdNode): void {
    if (node.type === 'table') markTable(node);
    else node.children?.forEach(walk);
}

export function remarkNumericColumns() {
    return (tree: MdNode) => {
        walk(tree);
    };
}
