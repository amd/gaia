// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Sharing from 'expo-sharing';

import { GaiaError } from '@/api/errors';

/** Opens the phone's share sheet (Save to Files, Mail, AirDrop...) for a file already on the phone. */
export async function shareFile(uri: string, options?: { mimeType?: string }): Promise<void> {
    if (!(await Sharing.isAvailableAsync())) throw new GaiaError('mobileApp.share.unavailable');
    await Sharing.shareAsync(uri, options);
}
