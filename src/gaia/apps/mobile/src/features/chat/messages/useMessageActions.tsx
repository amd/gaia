// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Clipboard from 'expo-clipboard';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import type { Message } from '@/api/client.types';
import { describeError, useT } from '@/i18n/useT';
import { ActionSheet, Notice, type SheetAction } from '@/ui';
import { Copy } from '@/ui/icons';

import { formatFullTimestamp } from './time';

const COPIED_MS = 2000;

export interface MessageActions {
    copy(): void;
    copied: boolean;
    /** The sheet, closed until opened; render once beside the bubble. */
    sheet: ReactNode;
    openSheet(): void;
    /** Spread onto the Pressable that long-press opens the sheet from. */
    a11y: {
        accessibilityActions: readonly { name: string; label: string }[];
        onAccessibilityAction: (event: { nativeEvent: { actionName: string } }) => void;
    };
    /** Shown when copying failed. */
    notice: ReactNode;
}

/** A message's long-press sheet: when it was sent, and Copy. */
export function useMessageActions(message: Message, copyText: string): MessageActions {
    const t = useT();
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);
    const [failure, setFailure] = useState<string | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(
        () => () => {
            if (timer.current) clearTimeout(timer.current);
        },
        [],
    );

    const copy = useCallback(() => {
        Clipboard.setStringAsync(copyText).then(
            () => {
                setFailure(null);
                setCopied(true);
                if (timer.current) clearTimeout(timer.current);
                timer.current = setTimeout(() => setCopied(false), COPIED_MS);
            },
            (error: unknown) => setFailure(describeError(error, t)),
        );
    }, [copyText, t]);

    const openSheet = () => setOpen(true);
    const actions: SheetAction[] = copyText ? [{ label: t('msg.copy'), icon: Copy, onPress: copy }] : [];

    // Each language half formats its own date, so the bracketed English isn't an Arabic date.
    const title = Number.isNaN(new Date(message.created_at).getTime())
        ? undefined
        : t.compose((say, code) =>
              say(message.role === 'user' ? 'msg.sent' : 'msg.replied', { time: formatFullTimestamp(message.created_at, t, code) }),
          );

    return {
        copy,
        copied,
        openSheet,
        a11y: {
            accessibilityActions: [{ name: 'activate', label: t('msg.actions') }],
            onAccessibilityAction: (event) => {
                if (event.nativeEvent.actionName === 'activate') openSheet();
            },
        },
        sheet: <ActionSheet open={open} onClose={() => setOpen(false)} actions={actions} title={title} />,
        notice: failure ? <Notice tone="danger" message={failure} /> : null,
    };
}
