// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { DevSettings } from 'react-native';

import { useT } from '@/i18n/useT';
import { useDirectionNotice } from '@/state/direction';
import { Notice } from '@/ui';

/** Tells the person the layout flips after a restart; only a development build can reload itself. */
export function DirectionNotice() {
    const t = useT();
    const pending = useDirectionNotice((s) => s.pending);
    const dismiss = useDirectionNotice((s) => s.dismiss);
    if (!pending) return null;
    const direction = t(pending === 'rtl' ? 'settings.layoutRtl' : 'settings.layoutLtr');
    return (
        <Notice
            tone="info"
            message={t('settings.layoutRestart', { direction })}
            action={__DEV__ ? { label: t('modelStatus.restart'), onPress: () => DevSettings.reload() } : undefined}
            onDismiss={dismiss}
        />
    );
}
