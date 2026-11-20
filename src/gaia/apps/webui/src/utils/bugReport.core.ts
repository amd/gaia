// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The prefilled GitHub bug report link. Pure, so the phone app can import it. */

export const GITHUB_NEW_ISSUE_URL = 'https://github.com/amd/gaia/issues/new';

/** A GitHub bug report URL with a pre-filled title and body; `environmentInfo` is the Markdown block naming the app and device. */
export function buildBugReportUrl(title: string, errorDetail: string | undefined, environmentInfo: string): string {
    const body = [
        '## Bug Description',
        '',
        errorDetail || title,
        '',
        '## Steps to Reproduce',
        '',
        '1. Open GAIA Agent UI',
        '2. _Describe what you did:_',
        '3. ',
        '',
        '## Expected Behavior',
        '',
        '_What did you expect to happen?_',
        '',
        '## Actual Behavior',
        '',
        '_What happened instead?_',
        '',
        '---',
        environmentInfo,
        '',
        '_Submitted from GAIA Agent UI_',
    ].join('\n');

    const params = new URLSearchParams({
        title: `[Bug] ${title}`,
        body,
        labels: 'bug,agent-ui',
    });
    return `${GITHUB_NEW_ISSUE_URL}?${params.toString()}`;
}
