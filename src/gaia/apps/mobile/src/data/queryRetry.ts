// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError, GaiaConfigError, GaiaPinMismatchError } from '@/api/errors';

const MAX_RETRIES = 2;

/** The app's default query retry: a refusal (4xx, pin mismatch, bad config) is final, so its error shows at once; network and 5xx failures retry twice. */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
    if (error instanceof GaiaConfigError || error instanceof GaiaPinMismatchError) return false;
    if (error instanceof GaiaApiError && error.status >= 400 && error.status < 500) return false;
    return failureCount < MAX_RETRIES;
}
