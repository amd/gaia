// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { MAX_ITEMS, isScalar, isOptionalString, scalarText, truncationLabel } from './primitiveShared';
import type { Scalar } from './primitiveShared';

interface TablePayload {
    title?: string;
    columns: string[];
    rows: Scalar[][];
}

function isTablePayload(value: unknown): value is TablePayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        isOptionalString(v.title) &&
        Array.isArray(v.columns) &&
        v.columns.every((c) => typeof c === 'string') &&
        Array.isArray(v.rows) &&
        v.rows.every((r) => Array.isArray(r) && r.every(isScalar))
    );
}

export function TableCard({ data }: { data: unknown }) {
    if (!isTablePayload(data)) {
        return <UnsupportedCard variant="invalid" render="table" data={data} />;
    }
    const rowsTruncated = data.rows.length > MAX_ITEMS;
    const rows = rowsTruncated ? data.rows.slice(0, MAX_ITEMS) : data.rows;
    const columnsTruncated = data.columns.length > MAX_ITEMS;
    const columns = columnsTruncated ? data.columns.slice(0, MAX_ITEMS) : data.columns;
    return (
        <div className="render-table-card">
            {data.title && <div className="render-table-card__title">{data.title}</div>}
            <div className="render-table">
                <table>
                    <thead>
                        <tr>
                            {columns.map((col, i) => (
                                <th key={i}>{col}</th>
                            ))}
                            {columnsTruncated && <th>{truncationLabel(data.columns.length)}</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, ri) => (
                            <tr key={ri}>
                                {row.slice(0, columns.length).map((cell, ci) => (
                                    <td key={ci}>{scalarText(cell)}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {rowsTruncated && (
                <div className="render-truncation">{truncationLabel(data.rows.length)}</div>
            )}
        </div>
    );
}
