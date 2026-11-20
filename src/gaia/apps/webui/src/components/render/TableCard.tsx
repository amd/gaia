// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { UnsupportedCard } from './UnsupportedCard';
import { MAX_ITEMS, isTablePayload, scalarText, truncationLabel } from '../../utils/renderCards.core';

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
