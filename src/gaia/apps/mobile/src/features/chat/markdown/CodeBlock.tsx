// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Clipboard from 'expo-clipboard';
import { useEffect, useRef, useState } from 'react';

import { CODE_COLLAPSE_LINES, CODE_PREVIEW_LINES, countLines, lineCount } from '@webui/utils/longContent';
import { useT } from '@/i18n/useT';
import { HScroll, Icon, Pressable, Row, Stack, Surface, Text } from '@/ui';
import { Check, ChevronDown, ChevronUp, Copy } from '@/ui/icons';

const COPIED_MS = 2000;

/** A fenced code block: always left to right, scrolls sideways, and a long one opens collapsed to a preview. */
export function CodeBlock({ lang, code, streaming }: { lang: string; code: string; streaming: boolean }) {
    const t = useT();
    const [copied, setCopied] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const lines = countLines(code);
    // While streaming, cap as soon as the preview fills so the block never grows and then shrinks.
    const collapsible = lines > (streaming ? CODE_PREVIEW_LINES : CODE_COLLAPSE_LINES);
    const shown = collapsible && !expanded ? code.split('\n').slice(0, CODE_PREVIEW_LINES).join('\n') : code;

    useEffect(
        () => () => {
            if (timer.current) clearTimeout(timer.current);
        },
        [],
    );

    const copy = () => {
        Clipboard.setStringAsync(code).then(
            () => {
                setCopied(true);
                if (timer.current) clearTimeout(timer.current);
                timer.current = setTimeout(() => setCopied(false), COPIED_MS);
            },
            (error: unknown) => console.error('Copying the code block to the clipboard failed', error),
        );
    };

    return (
        <Surface tone="recessed" radius="md" border>
            <Stack dir="ltr">
                <Row dir="ltr" gap="sm" justify="between">
                    <Stack paddingX="md" flex={1}>
                        <Text role="caption" tone="muted" mono numberOfLines={1}>
                            {lang || t('code.lang')}
                        </Text>
                    </Stack>
                    <Pressable
                        onPress={copy}
                        accessibilityLabel={copied ? t('msg.copiedAria') : t('code.copyAria')}
                    >
                        <Stack paddingX="md">
                            <Row gap="xs" dir="ltr">
                                <Icon icon={copied ? Check : Copy} size="sm" tone="secondary" />
                                <Text role="caption" tone="secondary">
                                    {copied ? t('code.copied') : t('code.copy')}
                                </Text>
                            </Row>
                        </Stack>
                    </Pressable>
                </Row>
                <HScroll dir="ltr">
                    <Stack paddingX="md" paddingY="sm">
                        <Text role="label" mono selectable dir="ltr">
                            {shown}
                        </Text>
                    </Stack>
                </HScroll>
                {collapsible && (
                    <Pressable
                        onPress={() => setExpanded((v) => !v)}
                        accessibilityState={{ expanded }}
                        accessibilityLabel={expanded ? t('code.collapse') : t('code.expand', { size: lineCount(t, lines) })}
                    >
                        <Stack paddingX="md">
                            <Row gap="xs" dir="ltr">
                                <Icon icon={expanded ? ChevronUp : ChevronDown} size="sm" tone="secondary" />
                                <Text role="caption" tone="secondary">
                                    {expanded ? t('code.collapse') : t('code.expand', { size: lineCount(t, lines) })}
                                </Text>
                            </Row>
                        </Stack>
                    </Pressable>
                )}
            </Stack>
        </Surface>
    );
}
