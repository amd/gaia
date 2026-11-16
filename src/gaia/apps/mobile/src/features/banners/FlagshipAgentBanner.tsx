// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useFlagshipAgent } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Notice } from '@/ui';

/** Shown above a chat while the PC has no GAIA agent: says why chat can't start, and offers the install. */
export function FlagshipAgentBanner() {
    const t = useT();
    const { missing, noBuild, state, install } = useFlagshipAgent();
    if (!missing) return null;
    if (noBuild) return <Notice tone="warning" message={t('agentNotice.noBuild')} testID="flagship-notice" />;
    if (state.kind === 'installing') {
        return (
            <Notice
                tone="warning"
                message={t('agentNotice.installing', { percent: Math.round(state.percent) })}
                testID="flagship-notice"
            />
        );
    }
    const failed = state.kind === 'failed';
    return (
        <Notice
            tone="warning"
            message={failed ? t('agentNotice.failed', { error: describeError(state.error, t) }) : t('agentNotice.missing')}
            action={{ label: failed ? t('agentNotice.retry') : t('agentNotice.install'), onPress: () => void install() }}
            testID="flagship-notice"
        />
    );
}
