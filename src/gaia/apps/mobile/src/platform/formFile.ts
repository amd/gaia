// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { File } from 'expo-file-system';

/**
 * A file on this phone as a FormData value that keeps its own name and type. The pinned transport sends it from disk
 * by its URI; any other fetch reads it with `bytes()` when it sends the form.
 */
export async function formFile(uri: string, name: string, type: string | null): Promise<Blob> {
    const file = { uri, name, type: type ?? '', bytes: () => new File(uri).bytes() };
    return file as unknown as Blob;
}
