// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isolate } from '@webui/i18n/registry';
import { isLongUserMessage, sizeLabel, USER_COLLAPSE_LINES } from '@webui/utils/longContent';
import type { Message } from '@/api/client.types';
import { useDirection, useT } from '@/i18n/useT';
import { contentDir, Pressable, Stack, Surface, Text } from '@/ui';

import { useMessageActions, type MessageContext, type StartOpen } from './useMessageActions';

export interface UserBubbleProps {
    message: Message;
    /** Called after "Show less", so the list can bring the bubble back into view. */
    onCollapse?: () => void;
    context?: MessageContext;
    startOpen?: StartOpen;
}

export function UserBubble({ message, onCollapse, context, startOpen }: UserBubbleProps) {
    const t = useT();
    const uiDir = useDirection();
    const actions = useMessageActions(message, message.content, context, startOpen);
    const long = isLongUserMessage(message.content);
    const [expanded, setExpanded] = useState(false);
    const collapsed = long && !expanded;

    const toggle = () => {
        setExpanded(!expanded);
        if (expanded) onCollapse?.();
    };

    return (
        <Stack paddingX="lg" paddingY="xs">
            <Stack self="end" maxWidth="85%">
                <Surface tone="userMsg" radius="lg" padding="md">
                    <Stack gap="sm">
                        {actions.editor ?? (
                            <Pressable onLongPress={actions.openSheet} accessibilityRole="text" minSize={0} {...actions.a11y}>
                                <Text
                                    role="body"
                                    dir={contentDir(message.content, uiDir)}
                                    numberOfLines={collapsed ? USER_COLLAPSE_LINES : undefined}
                                >
                                    {message.content}
                                </Text>
                            </Pressable>
                        )}
                        {long && !actions.editor ? (
                            <Pressable
                                onPress={toggle}
                                accessibilityState={{ expanded }}
                                minSize={48}
                                accessibilityLabel={
                                    expanded
                                        ? t('msg.showLess')
                                        : t.hint(t('msg.showAll'), sizeLabel(t, message.content))
                                }
                            >
                                <Text role="label" tone="accent" weight="medium">
                                    {expanded
                                        ? t('msg.showLess')
                                        : t.hint(t('msg.showAll'), isolate(sizeLabel(t, message.content), 'ltr'))}
                                </Text>
                            </Pressable>
                        ) : null}
                    </Stack>
                </Surface>
            </Stack>
            {actions.notice}
            {actions.sheet}
        </Stack>
    );
}
