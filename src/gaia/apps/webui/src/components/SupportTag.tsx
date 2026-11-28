// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '../i18n/useT';
import type { ProviderModel } from '../types';
import './setup/setup.css';

/** Setup's Verified / Untested tag. */
export function SupportTag({ support }: { support: ProviderModel['support'] }) {
    const t = useT();
    if (support === 'verified') return <span className="mc-chip is-verified">{t('setup.label.verified')}</span>;
    if (support === 'untested') return <span className="mc-chip is-untested">{t('setup.label.untested')}</span>;
    return null;
}
