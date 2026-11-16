// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment, type ReactNode } from 'react';
import { useWindowDimensions } from 'react-native';

import { useChatStream } from '@/data';
import { useT } from '@/i18n/useT';
import { Sheet, Stack, Text } from '@/ui';

import { DETAILS_HEADING, SECTIONS } from './sections';

export interface SessionPanelProps {
    sessionId: string;
    open: boolean;
    onClose: () => void;
    /** The sheet draws in the app's sheet host, outside the caller's providers; the dev gallery re-wraps the body here. */
    wrap?: (body: ReactNode) => ReactNode;
}

/** The chat's side panel as a sheet over the chat: what this chat has used and what its agent can do. */
export function SessionPanel({ sessionId, open, onClose, wrap }: SessionPanelProps) {
    const t = useT();
    return (
        <Sheet open={open} onClose={onClose} title={t('sp.title')}>
            {wrap ? wrap(<PanelBody sessionId={sessionId} close={onClose} />) : <PanelBody sessionId={sessionId} close={onClose} />}
        </Sheet>
    );
}

function PanelBody({ sessionId, close }: { sessionId: string; close: () => void }) {
    const t = useT();
    const { height } = useWindowDimensions();
    const live = useChatStream(sessionId).status !== 'idle';
    const firstDetails = SECTIONS.findIndex((s) => s.group === 'details');
    return (
        <Stack gap="md" paddingY="sm" minHeight={height * 0.6}>
            {SECTIONS.map((section, i) => (
                <Fragment key={section.id}>
                    {i === firstDetails ? (
                        <Text role="caption" weight="semibold" tone="muted" accessibilityRole="header">
                            {t(DETAILS_HEADING)}
                        </Text>
                    ) : null}
                    {section.render({ sessionId, live, close })}
                </Fragment>
            ))}
        </Stack>
    );
}
