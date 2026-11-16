// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SystemStatus } from '@webui/types';

import { isStarting } from '@/data';
import type { Translate } from '@/i18n/useT';

// The composer's disabled reason while GAIA boots; null once it is ready.
export function startingReason(status: SystemStatus | null | undefined, t: Translate): string | null {
    return isStarting(status) ? t('composer.starting') : null;
}

// The warm-up line stays hidden while GAIA is still starting.
export function showWarmUp(status: SystemStatus | null | undefined): boolean {
    return !isStarting(status);
}
