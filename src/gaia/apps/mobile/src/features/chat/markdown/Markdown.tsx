// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { PhrasingContent, Root, RootContent } from 'mdast';
import { useMemo, type ReactNode } from 'react';

import { buildCitationIndex, type LibraryDoc } from '@webui/utils/citations';
import { isolate } from '@webui/i18n/registry';
import { useDirection, useT } from '@/i18n/useT';
import { contentDir, Divider, HScroll, Icon, Notice, Row, Stack, Surface, Text } from '@/ui';
import { Check, Square } from '@/ui/icons';

import { CodeBlock } from './CodeBlock';
import { Caret, plainText, renderInline, type InlineContext } from './inline';
import { parseAnswer, parseFormatting } from './parse';
import { useThrottledText } from './useStreamingText';

const STREAM_PARSE_MS = 80;
const NO_DOCS: readonly LibraryDoc[] = [];

export interface MarkdownProps {
    text: string;
    /** 'formatting' is for agent-supplied text: no links, images, headings, citations or path links. */
    variant?: 'answer' | 'formatting';
    streaming?: boolean;
    documents?: LibraryDoc[];
    /** Library notices the server sent with the answer, shown under it. */
    notes?: string[];
    /** Replaces opening the cited file in the app's preview. */
    onOpenCitation?: (doc: LibraryDoc, page?: number) => void;
}

interface RenderContext extends InlineContext {
    uiDir: 'ltr' | 'rtl';
    streaming: boolean;
}

type Dir = 'ltr' | 'rtl';

function inline(node: { children: PhrasingContent[] }, ctx: RenderContext, key: string, caret = false): ReactNode[] {
    const out = renderInline(node.children, ctx, `${key}.`);
    if (caret) out.push(<Caret key={`${key}.caret`} />);
    return out;
}

function renderTable(node: Extract<RootContent, { type: 'table' }>, ctx: RenderContext, key: string): ReactNode {
    const dir: Dir = contentDir(plainText(node.children[0] ?? { children: [] }), ctx.uiDir);
    const columns = Math.max(0, ...node.children.map((row) => row.children.length));
    // Column by column, so each column is as wide as its widest cell and cells never wrap.
    return (
        <Surface tone="card" radius="md" border key={key}>
            <HScroll dir={dir}>
                <Row dir={dir} align="stretch">
                    {Array.from({ length: columns }, (_, c) => (
                        <Stack key={c}>
                            {node.children.map((row, r) => {
                                const cell = row.children[c];
                                return (
                                    <Stack key={r}>
                                        {r > 0 && <Divider />}
                                        <Stack paddingX="md" paddingY="sm">
                                            <Text role="ui" weight={r === 0 ? 'semibold' : undefined} dir={dir}>
                                                {cell ? inline(cell, ctx, `${key}.${r}.${c}`) : ' '}
                                            </Text>
                                        </Stack>
                                    </Stack>
                                );
                            })}
                        </Stack>
                    ))}
                </Row>
            </HScroll>
        </Surface>
    );
}

function renderList(node: Extract<RootContent, { type: 'list' }>, ctx: RenderContext, key: string): ReactNode {
    const dir: Dir = contentDir(plainText(node), ctx.uiDir);
    const first = node.start ?? 1;
    return (
        <Stack key={key} gap="xs" dir={dir}>
            {node.children.map((item, i) => {
                const checked = item.checked;
                const marker =
                    checked === null || checked === undefined ? (
                        <Text role="prose">{node.ordered ? isolate(`${first + i}.`, 'ltr') : '•'}</Text>
                    ) : (
                        <Icon icon={checked ? Check : Square} size="sm" tone={checked ? 'accent' : 'muted'} />
                    );
                return (
                    <Row key={i} gap="sm" align="start" dir={dir}>
                        {marker}
                        <Stack flex={1} gap="xs">
                            {renderBlocks(item.children, ctx, `${key}.${i}`)}
                        </Stack>
                    </Row>
                );
            })}
        </Stack>
    );
}

function renderBlock(node: RootContent, ctx: RenderContext, key: string, caret = false): ReactNode {
    switch (node.type) {
        case 'paragraph':
            return (
                <Text key={key} role="prose" selectable dir={contentDir(plainText(node), ctx.uiDir)}>
                    {inline(node, ctx, key, caret)}
                </Text>
            );
        case 'heading': {
            const dir = contentDir(plainText(node), ctx.uiDir);
            const role = node.depth === 1 ? 'title' : node.depth === 2 ? 'body' : 'ui';
            return (
                <Text key={key} role={role} weight="semibold" selectable dir={dir} accessibilityRole="header">
                    {inline(node, ctx, key, caret)}
                </Text>
            );
        }
        case 'list':
            return renderList(node, ctx, key);
        case 'blockquote':
            return (
                <Row key={key} gap="md" align="stretch" dir={contentDir(plainText(node), ctx.uiDir)}>
                    <Divider vertical />
                    <Stack flex={1} gap="sm">
                        {renderBlocks(node.children, ctx, key)}
                    </Stack>
                </Row>
            );
        case 'thematicBreak':
            return <Divider key={key} />;
        case 'code':
            return <CodeBlock key={key} lang={node.lang ?? ''} code={node.value} streaming={ctx.streaming} />;
        case 'table':
            return renderTable(node, ctx, key);
        default:
            // Raw HTML, definitions and footnotes: nothing a phone should run or show.
            return null;
    }
}

function renderBlocks(nodes: RootContent[], ctx: RenderContext, keyBase: string, caretOnLast = false): ReactNode[] {
    return nodes.map((node, i) =>
        renderBlock(node, ctx, `${keyBase}b${i}`, caretOnLast && i === nodes.length - 1 && (node.type === 'paragraph' || node.type === 'heading')),
    );
}

function endsInline(tree: Root): boolean {
    const last = tree.children[tree.children.length - 1];
    return last?.type === 'paragraph' || last?.type === 'heading';
}

/** An assistant answer as native Markdown: the same parser and rules as the web UI's answer. */
export function Markdown({ text, variant = 'answer', streaming: streamingProp = false, documents = NO_DOCS as LibraryDoc[], notes, onOpenCitation }: MarkdownProps) {
    const formatting = variant === 'formatting';
    // The formatting variant ignores streaming, documents, notes and onOpenCitation.
    const streaming = streamingProp && !formatting;
    const t = useT();
    const uiDir = useDirection();
    const shown = useThrottledText(text, streaming, STREAM_PARSE_MS);
    const tree = useMemo(() => (formatting ? parseFormatting(shown) : parseAnswer(shown, streaming)), [shown, streaming, formatting]);
    const index = useMemo(() => buildCitationIndex(formatting ? NO_DOCS : documents), [documents, formatting]);
    const ctx: RenderContext = { index, t, onOpenCitation: formatting ? undefined : onOpenCitation, uiDir, streaming };
    const caretInline = streaming && endsInline(tree);

    return (
        <Stack gap="md">
            {renderBlocks(tree.children, ctx, 'm', caretInline)}
            {streaming && !caretInline && (
                <Text role="prose">
                    <Caret />
                </Text>
            )}
            {!formatting && notes && notes.length > 0 && (
                <Stack gap="sm">
                    {notes.map((note, i) => (
                        <Notice key={`${i}-${note}`} tone="info" role="note" message={note} />
                    ))}
                </Stack>
            )}
        </Stack>
    );
}
