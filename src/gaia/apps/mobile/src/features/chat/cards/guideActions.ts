// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Clipboard from 'expo-clipboard';
import { Share } from 'react-native';

export async function copyText(text: string): Promise<void> {
    if (!(await Clipboard.setStringAsync(text))) throw new Error('The clipboard refused the text');
}

/** Resolves false when the person dismissed the share sheet. */
export async function shareText(title: string, text: string): Promise<boolean> {
    const result = await Share.share({ title, message: text });
    return result.action !== Share.dismissedAction;
}

export function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}
