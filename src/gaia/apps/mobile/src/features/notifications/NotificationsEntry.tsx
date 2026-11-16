// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { selectUnreadCount, useNotificationStore } from '@/state/notifications';
import { Badge, Icon, ListRow } from '@/ui';
import { Bell } from '@/ui/icons';

/** The drawer's Notifications row, with the unread count capped at 99+ as the web's bell is. */
export function NotificationsEntry({ active, onOpen }: { active: boolean; onOpen: () => void }) {
    const t = useT();
    const unread = useNotificationStore(selectUnreadCount);
    return (
        <ListRow
            title={t('sidebar.notifications')}
            accessibilityLabel={unread > 0 ? t('sidebar.notificationsUnread', { n: unread }) : t('sidebar.notifications')}
            selected={active}
            leading={<Icon icon={Bell} size="sm" tone="secondary" />}
            trailing={unread > 0 ? <Badge shape="pill" label={unread > 99 ? '99+' : String(unread)} tone="accent" /> : undefined}
            onPress={onOpen}
            testID="drawer-notifications"
        />
    );
}
