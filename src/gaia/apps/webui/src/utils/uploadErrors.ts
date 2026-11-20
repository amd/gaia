// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Translate } from '../i18n/translator';

const MODERN_OFFICE: Record<string, string> = { '.doc': '.docx', '.xls': '.xlsx', '.ppt': '.pptx' };

/**
 * The upload refusal in the chosen language. `reason` and `body` are the backend's `detail.status` and
 * `detail` object; a refusal this build doesn't know shows `fallback`, the server's own words.
 */
export function uploadRefusalText(reason: string | null, body: Record<string, unknown> | null, fallback: string, t: Translate): string {
    const { extension, size_mb: size, max_mb: max } = body ?? {};
    switch (reason) {
        case 'no_extension': return t('composer.upload.noExtension');
        case 'unsupported_type': {
            const modern = MODERN_OFFICE[String(extension)];
            if (modern) return t('composer.upload.legacyOffice', { ext: String(extension), modern });
            return t('composer.upload.unsupported', { ext: String(extension) });
        }
        case 'empty_file': return t('composer.upload.empty');
        case 'too_large': return t('composer.upload.tooLarge', { size: String(size), max: String(max) });
        default: return fallback;
    }
}
