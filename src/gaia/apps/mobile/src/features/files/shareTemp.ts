// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Directory, File, Paths } from 'expo-file-system';

export const SHARE_IMAGE_PREFIX = 'gaia-image-';
const OWN_FOLDERS = ['pc-files', 'pc-audio'];

/** Deletes what the app left in its own cache names after a crash or a kill; nothing else in the cache is touched. */
export function sweepTempFiles(): void {
    for (const name of OWN_FOLDERS) {
        const folder = new Directory(Paths.cache, name);
        if (folder.exists) folder.delete();
    }
    for (const entry of new Directory(Paths.cache).list()) {
        if (entry instanceof File && entry.name.startsWith(SHARE_IMAGE_PREFIX)) entry.delete();
    }
}
