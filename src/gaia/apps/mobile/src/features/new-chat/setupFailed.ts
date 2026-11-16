// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { SystemStatus } from '@/api/client.types';

// First-run setup's error, moot once a local model has loaded after all.
export function setupError(status: SystemStatus | undefined): string | null {
    return status?.model_loaded ? null : (status?.setup_error ?? null);
}
