// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Mirrors VIDEO_EXTENSIONS and AUDIO_EXTENSIONS in src/gaia/audio/media.py.
// tests/unit/chat/ui/test_media_upload.py fails if the two drift.
export const VIDEO_EXTENSIONS = new Set([
    '.3g2', '.3gp', '.asf', '.avi', '.divx', '.f4v', '.flv', '.m2ts', '.m2v', '.m4v', '.mkv', '.mov',
    '.mp4', '.mpeg', '.mpg', '.mts', '.mxf', '.ogv', '.rm', '.rmvb', '.vob', '.webm', '.wmv',
]);

export const AUDIO_EXTENSIONS = new Set([
    '.aac', '.ac3', '.aif', '.aiff', '.amr', '.ape', '.au', '.caf', '.dts', '.eac3', '.flac', '.m4a',
    '.m4b', '.mka', '.mp2', '.mp3', '.oga', '.ogg', '.opus', '.spx', '.wav', '.wma', '.wv',
]);

function extensionOf(name: string): string {
    const dot = name.lastIndexOf('.');
    return dot < 0 ? '' : name.slice(dot).toLowerCase();
}

export function isVideoFile(name: string): boolean {
    return VIDEO_EXTENSIONS.has(extensionOf(name));
}

/** True for a recording GAIA can transcribe, whether it holds video or only audio. */
export function isMediaFile(name: string): boolean {
    const ext = extensionOf(name);
    return VIDEO_EXTENSIONS.has(ext) || AUDIO_EXTENSIONS.has(ext);
}
