// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useT } from '@/i18n/useT';
import { Notice } from '@/ui';

import { errorText, refusedOnPhone } from './modelText';

/** A failed action: the PC's 403 says the change is made there, anything else is shown in the PC's own words. */
export function LocalError({ error, testID }: { error: unknown; testID?: string }) {
    const t = useT();
    return refusedOnPhone(error)
        ? <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID={testID ?? 'local-pc-only'} />
        : <Notice tone="danger" message={errorText(error, t)} />;
}
