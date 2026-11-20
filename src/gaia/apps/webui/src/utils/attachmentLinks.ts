// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

export interface UploadedLink {
    name: string;
    serverUrl: string;
    isImage: boolean;
    isMedia?: boolean;
}

/** The message text with a markdown link for each uploaded file, which is how the agent finds them. */
export function composeWithLinks(text: string, files: UploadedLink[], mediaPrompt: string): string {
    const links = files
        .map((a) => {
            // A bracket in a file name ends the link early, and the agent never sees the file.
            const label = a.name.replace(/[[\]]/g, ' ').trim();
            return a.isImage ? `![${label}](${a.serverUrl})` : `[${label}](${a.serverUrl})`;
        })
        .join('\n');
    if (!links) return text;
    // A recording sent with no words still has an obvious request.
    const ask = text || (files.some((a) => a.isMedia) ? mediaPrompt : '');
    return ask ? `${ask}\n\n${links}` : links;
}
