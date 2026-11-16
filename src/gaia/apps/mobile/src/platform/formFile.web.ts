// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A picked file (a `blob:` or `data:` URL in the browser) as a Blob for FormData. */
export async function formFile(uri: string, _name: string, _type: string | null): Promise<Blob> {
    return (await globalThis.fetch(uri)).blob();
}
