// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { MAX_ITEMS, isTablePayload, scalarText, type Scalar } from '@webui/utils/renderCards.core';

import { useDirection, useT } from '@/i18n/useT';
import { contentDir, Divider, HScroll, Ltr, Row, Stack, Surface, Text } from '@/ui';

import { TruncationNote } from './TruncationNote';
import { UnsupportedCard } from './UnsupportedCard';

const isNumberColumn = (rows: Scalar[][], c: number) => {
    const cells = rows.map((r) => r[c]).filter((v) => v !== null && v !== undefined);
    return cells.length > 0 && cells.every((v) => typeof v === 'number');
};

export function TableCard({ data }: { data: unknown }) {
    const t = useT();
    const uiDir = useDirection();
    if (!isTablePayload(data)) return <UnsupportedCard variant="invalid" render="table" data={data} />;
    const columns = data.columns.slice(0, MAX_ITEMS);
    const rows = data.rows.slice(0, MAX_ITEMS);
    const dir = contentDir(columns.join(' '), uiDir);
    const sizes = { rows: data.rows.length, columns: data.columns.length };
    const announce = data.title ? t.bare('mobileApp.render.tableTitled', { title: data.title, ...sizes }) : t.bare('mobileApp.render.table', sizes);
    const headers = data.columns.length > MAX_ITEMS ? [...columns, null] : columns;
    return (
        <Surface tone="card" radius="md" border padding="md" testID="card-table" accessibilityLabel={data.title ? undefined : announce}>
            <Stack gap="sm">
                {data.title ? (
                    <Text role="ui" weight="semibold" dir="auto" accessibilityRole="header" accessibilityLabel={announce}>
                        {data.title}
                    </Text>
                ) : null}
                <HScroll dir={dir} hint testID="card-table-scroll">
                    <Row dir={dir} align="stretch">
                        {headers.map((header, c) => {
                            const numeric = header !== null && isNumberColumn(rows, c);
                            return (
                                <Stack key={c}>
                                    {[header ?? t('render.moreTruncated', { n: data.columns.length - MAX_ITEMS }), ...rows.map((r) => r[c])].map((cell, r) => {
                                        const text = r === 0 ? (cell as string) : scalarText((cell ?? null) as Scalar);
                                        return (
                                            <Stack key={r}>
                                                {r > 0 ? <Divider /> : null}
                                                <Stack paddingX="sm" paddingY="sm" align={numeric ? 'end' : 'start'}>
                                                    {numeric && r > 0 ? (
                                                        <Text role="label">
                                                            <Ltr>{text}</Ltr>
                                                        </Text>
                                                    ) : (
                                                        <Text role="label" weight={r === 0 ? 'semibold' : undefined} tone={r === 0 ? 'secondary' : 'primary'} dir="auto">
                                                            {text === '' ? ' ' : text}
                                                        </Text>
                                                    )}
                                                </Stack>
                                            </Stack>
                                        );
                                    })}
                                </Stack>
                            );
                        })}
                    </Row>
                </HScroll>
                {data.rows.length > MAX_ITEMS ? <TruncationNote total={data.rows.length} /> : null}
            </Stack>
        </Surface>
    );
}
