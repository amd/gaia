// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { PhrasingContent } from 'mdast';
import { Fragment, useEffect, useState, type ReactNode } from 'react';
import { AccessibilityInfo } from 'react-native';

import { isolate } from '@webui/i18n/registry';
import {
    docForToken,
    findCitations,
    findPages,
    type CitationIndex,
    type FoundPage,
    type LibraryDoc,
} from '@webui/utils/citations';
import { safeUrlTransform } from '@webui/utils/markdown';
import { WIN_PATH_ONLY_RE, findFilePaths } from '@webui/utils/windowsPaths';
import type { Translate } from '@/i18n/useT';
import { isFileLink, openPcFile } from '@/features/files/openPcFile';
import { openLinkFromPress } from '@/platform/openLink';
import { Icon, Text } from '@/ui';
import { FileText } from '@/ui/icons';

export interface InlineContext {
    index: CitationIndex;
    t: Translate;
    onOpenCitation?: (doc: LibraryDoc, page?: number) => void;
    /** The chat whose folders a file link may read; without one, a PC file link isn't offered. */
    sessionId?: string | null;
}

const OPENABLE_URL = /^(https?|mailto):/i;
const SCHEME_URL = /^\s*[a-z][a-z0-9+.-]*:/i;
const CARET_BLINK_MS = 530;

/** A path names a file on the PC: a link to the app's preview when a chat is in scope, else plain text; left to right, in mono. */
function pathText(path: string, ctx: InlineContext, key: string): ReactNode {
    const { sessionId, t } = ctx;
    if (!sessionId) {
        return (
            <Text key={key} mono dir="ltr">
                {isolate(path, 'ltr')}
            </Text>
        );
    }
    return (
        <Text
            key={key}
            mono
            dir="ltr"
            tone="accent"
            accessibilityRole="link"
            accessibilityLabel={t('activity.openPath', { path })}
            onPress={() => openPcFile(path, sessionId)}
        >
            {isolate(path, 'ltr')}
        </Text>
    );
}

function withPaths(text: string, ctx: InlineContext, keyBase: string): ReactNode[] {
    const out: ReactNode[] = [];
    let at = 0;
    for (const { start, path } of findFilePaths(text)) {
        if (start > at) out.push(text.slice(at, start));
        out.push(pathText(path, ctx, `${keyBase}p${start}`));
        at = start + path.length;
    }
    if (out.length === 0) return [text];
    if (at < text.length) out.push(text.slice(at));
    return out;
}

function citation(doc: LibraryDoc, page: number | undefined, ctx: InlineContext, key: string): ReactNode {
    const { onOpenCitation, t } = ctx;
    const name = isolate(doc.filename, 'ltr');
    if (!onOpenCitation) return <Fragment key={key}>{name}</Fragment>;
    return (
        <Text
            key={key}
            tone="accent"
            accessibilityRole="link"
            accessibilityLabel={page ? t('citation.openPage', { page }) : t('citation.open', { name: doc.filename })}
            onPress={() => onOpenCitation(doc, page)}
        >
            <Icon icon={FileText} size="sm" tone="accent" /> {name}
        </Text>
    );
}

function pageLink(doc: LibraryDoc, page: number, label: string, ctx: InlineContext, key: string): ReactNode {
    const { onOpenCitation, t } = ctx;
    if (!onOpenCitation) return <Fragment key={key}>{label}</Fragment>;
    return (
        <Text
            key={key}
            tone="accent"
            accessibilityRole="link"
            accessibilityLabel={t('citation.openPage', { page })}
            onPress={() => onOpenCitation(doc, page)}
        >
            {label}
        </Text>
    );
}

/** Page numbers cited at the start of `text`, before it names another document. */
function leadingPages(index: CitationIndex, text: string): FoundPage[] {
    const next = findCitations(index, text)[0];
    return findPages(next ? text.slice(0, next.start) : text);
}

/** A text run with its cited documents and pages linked and any Windows path shown as it is. */
function citeText(text: string, ctx: InlineContext, carried: LibraryDoc | null, keyBase: string): ReactNode[] {
    const paths = findFilePaths(text);
    const citations = findCitations(ctx.index, text).filter(
        (c) => !paths.some((p) => p.start < c.start && c.start < p.start + p.path.length),
    );
    const parts: { start: number; end: number; node: ReactNode }[] = [];
    const addPages = (doc: LibraryDoc, pages: FoundPage[], offset: number) => {
        for (const pg of pages) {
            const start = offset + pg.start;
            const end = offset + pg.end;
            parts.push({ start, end, node: pageLink(doc, pg.page, text.slice(start, end), ctx, `${keyBase}g${start}`) });
        }
    };
    if (carried) addPages(carried, findPages(text.slice(0, citations[0]?.start ?? text.length)), 0);
    citations.forEach((c, i) => {
        const pages = findPages(text.slice(c.end, citations[i + 1]?.start ?? text.length));
        parts.push({ start: c.start, end: c.end, node: citation(c.doc, pages[0]?.page, ctx, `${keyBase}c${c.start}`) });
        addPages(c.doc, pages, c.end);
    });
    if (parts.length === 0) return withPaths(text, ctx, keyBase);
    parts.sort((a, b) => a.start - b.start);
    const out: ReactNode[] = [];
    let at = 0;
    for (const part of parts) {
        if (part.start > at) out.push(...withPaths(text.slice(at, part.start), ctx, `${keyBase}a${at}`));
        out.push(part.node);
        at = part.end;
    }
    if (at < text.length) out.push(...withPaths(text.slice(at), ctx, `${keyBase}a${at}`));
    return out;
}

function inlineCode(value: string, ctx: InlineContext, key: string, page?: number): ReactNode {
    const doc = docForToken(ctx.index, value);
    if (doc) return citation(doc, page, ctx, key);
    if (WIN_PATH_ONLY_RE.test(value.trim())) return pathText(value.trim(), ctx, key);
    // No-break spaces keep a command on one line, so it never splits into reordered pieces.
    return (
        <Text key={key} code dir="ltr">
            {isolate(value.replace(/ /g, '\u00A0'), 'ltr')}
        </Text>
    );
}

function link(url: string, children: ReactNode, key: string, ctx: InlineContext): ReactNode {
    // A file:// URL or an absolute path names a file on the PC: the preview, or nothing where no chat is in scope.
    if (isFileLink(url)) {
        const { sessionId } = ctx;
        if (!sessionId) return <Fragment key={key}>{children}</Fragment>;
        return (
            <Text key={key} tone="accent" accessibilityRole="link" onPress={() => openPcFile(url, sessionId)}>
                {children}
            </Text>
        );
    }
    const safe = safeUrlTransform(url);
    // A scheme the policy refuses stays tappable, so the tap explains the refusal; a link with no scheme shows as plain text.
    if (!OPENABLE_URL.test(safe)) {
        if (!SCHEME_URL.test(url)) return <Fragment key={key}>{children}</Fragment>;
        return (
            <Text key={key} tone="accent" accessibilityRole="link" onPress={() => openLinkFromPress(url)}>
                {children}
            </Text>
        );
    }
    return (
        <Text key={key} tone="accent" accessibilityRole="link" onPress={() => openPcFile(safe, ctx.sessionId ?? null)}>
            {children}
        </Text>
    );
}

/** The text of a phrasing node, for choosing a block's direction. */
export function plainText(node: { value?: string; children?: unknown[] }): string {
    if (typeof node.value === 'string') return node.value;
    return (node.children ?? []).map((c) => plainText(c as { value?: string; children?: unknown[] })).join(' ');
}

export function renderInline(nodes: PhrasingContent[], ctx: InlineContext, keyBase = ''): ReactNode[] {
    const out: ReactNode[] = [];
    let carried: LibraryDoc | null = null;
    nodes.forEach((node, i) => {
        const key = `${keyBase}${i}`;
        const wasCarried = carried;
        carried = null;
        switch (node.type) {
            case 'text':
                out.push(<Fragment key={key}>{citeText(node.value, ctx, wasCarried, key)}</Fragment>);
                return;
            case 'inlineCode': {
                const doc = docForToken(ctx.index, node.value);
                const next = nodes[i + 1];
                const page = doc && next?.type === 'text' ? leadingPages(ctx.index, next.value)[0]?.page : undefined;
                out.push(inlineCode(node.value, ctx, key, page));
                if (doc) carried = doc;
                return;
            }
            case 'strong':
                out.push(
                    <Text key={key} weight="bold">
                        {renderInline(node.children, ctx, `${key}.`)}
                    </Text>,
                );
                return;
            case 'emphasis':
                out.push(
                    <Text key={key} italic>
                        {renderInline(node.children, ctx, `${key}.`)}
                    </Text>,
                );
                return;
            case 'delete':
                out.push(
                    <Text key={key} strike>
                        {renderInline(node.children, ctx, `${key}.`)}
                    </Text>,
                );
                return;
            case 'break':
                out.push('\n');
                return;
            case 'link':
                out.push(link(node.url, renderInline(node.children, ctx, `${key}.`), key, ctx));
                return;
            case 'image':
                out.push(link(node.url, node.alt || node.url, key, ctx));
                return;
            default:
                return;
        }
    });
    return out;
}

/** A caret at the end of a streaming answer; it blinks unless the person asked for less motion. */
export function Caret() {
    const [reduced, setReduced] = useState(true);
    const [on, setOn] = useState(true);
    useEffect(() => {
        let live = true;
        AccessibilityInfo.isReduceMotionEnabled().then(
            (value) => live && setReduced(value),
            () => live && setReduced(true),
        );
        return () => {
            live = false;
        };
    }, []);
    useEffect(() => {
        if (reduced) return;
        const timer = setInterval(() => setOn((v) => !v), CARET_BLINK_MS);
        return () => clearInterval(timer);
    }, [reduced]);
    return <Text tone={reduced || on ? 'accent' : 'muted'}>{'▍'}</Text>;
}
