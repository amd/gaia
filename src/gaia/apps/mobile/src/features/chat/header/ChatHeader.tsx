// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isPlaceholderTitle } from '@webui/utils/sessionTitle';

import { useSession } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { useT } from '@/i18n/useT';
import { Text } from '@/ui';

export interface ChatHeaderProps {
    sessionId: string;
}

/** The chat's header: its title, or "New chat" until GAIA names it. */
export function ChatHeader({ sessionId }: ChatHeaderProps) {
    const t = useT();
    const session = useSession(sessionId);
    const title = session.data?.title;
    const titleNode = session.data ? (
        <Text role="title" weight="semibold" numberOfLines={1} testID="chat-title">
            {isPlaceholderTitle(title) ? t('sidebar.newChat') : title}
        </Text>
    ) : undefined;
    return <ScreenHeader titleNode={titleNode} />;
}
