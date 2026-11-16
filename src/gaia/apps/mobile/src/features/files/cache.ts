// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import * as Crypto from 'expo-crypto';
import { Directory, File, Paths } from 'expo-file-system';

const CACHE_FOLDER = 'pc-files';

export interface CachedFile {
    uri: string;
    remove(): void;
}

/** The file's extension, for the viewer that opens the cached copy; nothing else of its name is kept. */
export function extensionOf(name: string): string {
    return /\.[A-Za-z0-9]{1,8}$/.exec(name)?.[0].toLowerCase() ?? '';
}

/** Writes a PC file's bytes under the cache, named by a hash of where it came from, never by that path. */
export async function cachePcFile(origin: string, name: string, bytes: Uint8Array): Promise<CachedFile> {
    const hash = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, origin);
    const folder = new Directory(Paths.cache, CACHE_FOLDER);
    folder.create({ idempotent: true, intermediates: true });
    const file = new File(folder, `${hash}${extensionOf(name)}`);
    file.create({ overwrite: true });
    file.write(bytes);
    return {
        uri: file.uri,
        remove: () => {
            if (file.exists) file.delete();
        },
    };
}
