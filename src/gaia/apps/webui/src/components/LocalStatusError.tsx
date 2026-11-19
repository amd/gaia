// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { MarkedText, markLtr } from './MarkedText';
import { useT } from '../i18n/useT';
import type { LocalModelStatus } from '../types';

/** The model status view's `error`; a server not answering is worded in the interface language. */
export function LocalStatusError({ status }: { status: Pick<LocalModelStatus, 'error' | 'error_cause' | 'base_url' | 'owner'> }) {
    const t = useT();
    if (status.error_cause === 'model_server_down' && status.base_url) {
        const key = status.owner === 'gaia' ? 'modelStatus.serverDown.gaia' : 'modelStatus.serverDown.notOurs';
        return <MarkedText text={t(key, { url: markLtr(status.base_url) })} />;
    }
    return <>{status.error}</>;
}
