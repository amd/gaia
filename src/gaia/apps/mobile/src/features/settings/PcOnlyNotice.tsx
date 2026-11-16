// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError } from '@/api/errors';
import { useT } from '@/i18n/useT';
import { Notice } from '@/ui';

/** The PC refused the request (a 403): it is done on the PC, not an error on the phone. */
export function isPcRefusal(err: unknown): boolean {
    return err instanceof GaiaApiError && err.status === 403;
}

/** "Change this on your PC", in place of a control or section the PC won't serve this phone. */
export function PcOnlyNotice({ testID }: { testID: string }) {
    const t = useT();
    return <Notice tone="info" message={t('mobileApp.settings.pcOnly')} testID={testID} />;
}
