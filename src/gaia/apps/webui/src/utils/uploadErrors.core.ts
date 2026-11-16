// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Which files the library can take, and what to tell the person when an upload fails; no DOM. */

import type { Translate } from '../i18n/translator';
import { failureHint } from './documentLibrary.core';
import { serverFailureText } from './documentFailure';
import { AUDIO_EXTENSIONS, VIDEO_EXTENSIONS } from './mediaFiles';

export interface FileTypeCategory {
    label: string;
    extensions: Set<string>;
    message: string;
    alternatives: string[];
    featureTitle: string;
}

const UNSUPPORTED_FILE_CATEGORIES: FileTypeCategory[] = [
    {
        label: 'Image',
        extensions: new Set(['.ico', '.heic', '.heif']),
        message: 'This image format cannot be indexed.',
        alternatives: [
            'Convert it to PNG or JPEG — GAIA reads the text in those images',
        ],
        featureTitle: 'Support HEIC and ICO image indexing',
    },
    {
        label: 'Video',
        extensions: VIDEO_EXTENSIONS,
        message: 'Video files can\'t go in the document library.',
        alternatives: [
            'Attach the video to a chat, and GAIA will transcribe it',
        ],
        featureTitle: 'Support video files in the document library',
    },
    {
        label: 'Audio',
        extensions: AUDIO_EXTENSIONS,
        message: 'Audio files can\'t go in the document library.',
        alternatives: [
            'Attach the recording to a chat, and GAIA will transcribe it',
        ],
        featureTitle: 'Support audio files in the document library',
    },
    {
        label: 'Archive',
        extensions: new Set(['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz']),
        message: 'Archive files must be extracted before indexing.',
        alternatives: [
            'Extract the archive contents to a folder',
            'Then index the folder or individual files from the Document Library',
        ],
        featureTitle: 'Support automatic archive extraction for indexing',
    },
    {
        label: 'Executable',
        extensions: new Set(['.exe', '.msi', '.dll', '.so', '.app', '.dmg', '.bin', '.com']),
        message: 'Executable and binary files cannot be indexed.',
        alternatives: [
            'Index source code files (.py, .js, .ts, .java, etc.) instead',
            'Index documentation or README files from the project',
        ],
        featureTitle: 'Support binary file analysis',
    },
    {
        label: 'Database',
        extensions: new Set(['.sqlite', '.db', '.mdb', '.accdb', '.dbf']),
        message: 'Database files are not supported for direct indexing.',
        alternatives: [
            'Export data to CSV or JSON format, then index those files',
            'Use SQL queries to extract relevant data to a text file',
        ],
        featureTitle: 'Support database file indexing',
    },
    {
        label: 'Microsoft Office',
        extensions: new Set(['.doc', '.ppt', '.xls']),
        message:
            'Legacy Office formats (.doc, legacy PowerPoint .ppt, legacy Excel ' +
            '.xls) are not supported — GAIA reads only the modern XML-based ' +
            'formats. Modern Word (.docx), PowerPoint (.pptx), and Excel (.xlsx) ' +
            'are supported.',
        alternatives: [
            'Re-save as .docx — GAIA indexes modern Word documents directly',
            'Save modern PowerPoint as .pptx — GAIA indexes .pptx directly',
            'Re-save legacy .xls workbooks as .xlsx — GAIA supports modern Excel files',
        ],
        featureTitle: 'Support legacy Microsoft Office (doc, ppt, xls) indexing',
    },
];

/**
 * Look up the unsupported file category for a given extension.
 * Returns null if the extension is in the supported set.
 */
export function getUnsupportedCategory(extension: string): FileTypeCategory | null {
    const ext = extension.toLowerCase().startsWith('.') ? extension.toLowerCase() : `.${extension.toLowerCase()}`;
    for (const cat of UNSUPPORTED_FILE_CATEGORIES) {
        if (cat.extensions.has(ext)) return cat;
    }
    return null;
}

/**
 * Set of all supported extensions for document indexing.
 *
 * Keep this in sync with ``ALLOWED_EXTENSIONS`` in
 * ``src/gaia/ui/utils.py``. Only list extensions that have a real extractor
 * in ``src/gaia/rag/sdk.py::_extract_text_from_file`` — listing one without
 * a backend handler causes the RAG pipeline to index binary garbage.
 * .pptx and .docx ARE supported (python-pptx / python-docx ship with GAIA);
 * legacy .doc/.ppt/.xls are intentionally excluded. Raster images are read by
 * the vision model.
 */
export const SUPPORTED_EXTENSIONS = new Set([
    '.pdf', '.pptx', '.docx', '.epub',
    '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff',
    '.txt', '.md', '.mdx', '.markdown', '.rst', '.csv', '.json', '.xlsx',
    '.html', '.htm', '.xml', '.svg',
    '.yaml', '.yml', '.py', '.js', '.ts', '.java', '.c', '.cpp',
    '.h', '.rs', '.go', '.rb', '.sh', '.bat', '.ps1', '.log',
    '.cfg', '.ini', '.toml',
]);

/** Check if a file extension is supported for indexing. */
export function isExtensionSupported(extension: string): boolean {
    const ext = extension.toLowerCase().startsWith('.') ? extension.toLowerCase() : `.${extension.toLowerCase()}`;
    return SUPPORTED_EXTENSIONS.has(ext);
}


/** The backend's message without its leading category label, e.g. "File not found:". */
function stripPrefix(message: string, prefix: string): string {
    if (!message.startsWith(prefix)) return message;
    const rest = message.slice(prefix.length).trim();
    // What follows the label is now the sentence's start.
    return rest.charAt(0).toUpperCase() + rest.slice(1);
}


/** The extension of a file name with its dot, lower-cased; empty when it has none. */
export function fileExtension(filename: string): string {
    return filename.includes('.') ? '.' + filename.split('.').pop()?.toLowerCase() : '';
}

/** Why the extension rules out an upload before it is sent, or null when the server should decide. */
export function unsupportedTypeMessage(filename: string): string | null {
    const ext = fileExtension(filename);
    if (!ext || isExtensionSupported(ext)) return null;
    return getUnsupportedCategory(ext)?.message ?? `The file type "${ext}" is not supported for indexing.`;
}

export interface UploadErrorInput {
    filename: string;
    /** The backend's message. */
    error: string;
    /** HTTP status of the failed request, when there was one. */
    status?: number;
    /** The backend's error kind (`detail.status`), e.g. `corrupted`. */
    kind?: string | null;
}

export interface UploadErrorView {
    title: string;
    detail: string;
    /** `warning` is the person's to fix; `alert` and `bug` are GAIA's or the connection's. */
    icon: 'warning' | 'alert' | 'bug';
    /** What the web UI links to: a feature request, a bug report, or nothing. */
    report: { kind: 'feature' | 'bug'; title: string } | null;
}

/** The title, help and report link for a failed upload, by what went wrong. */
export function describeUploadError(t: Translate, { filename, error, status, kind }: UploadErrorInput): UploadErrorView {
    const ext = fileExtension(filename);
    const category = ext ? getUnsupportedCategory(ext) : null;
    const isFileTypeError = error.toLowerCase().includes('unsupported file type') ||
        error.toLowerCase().includes('not supported for indexing') ||
        error.toLowerCase().includes('file type not allowed') ||
        category !== null;
    const isConnectionError = error.toLowerCase().includes('fetch') ||
        error.toLowerCase().includes('network') ||
        error.toLowerCase().includes('connect');
    const permanentHint = failureHint(t, kind);

    // A wrong path, a file outside the readable folders or a damaged file is the user's to fix, not a bug to report.
    if (permanentHint) {
        return { title: `"${filename}" can't be read`, detail: permanentHint, icon: 'warning', report: null };
    }
    const serverReason = serverFailureText(kind, t);
    if (serverReason) {
        // The file is fine; the model server didn't answer.
        return { title: t('docs.notAddedServer', { name: filename }), detail: serverReason, icon: 'warning', report: null };
    }
    if (kind === 'too_large') {
        return { title: t('docs.tooLarge', { name: filename }), detail: error, icon: 'warning', report: null };
    }
    if (status === 404) {
        return { title: t('docs.notFound', { name: filename }), detail: t('docs.notFoundDetail'), icon: 'warning', report: null };
    }
    if (status === 403) {
        return {
            title: `GAIA can't read files in that location`,
            detail: `${error} Or drop the file here instead (up to 20 MB) and GAIA keeps its own copy.`,
            icon: 'warning',
            report: null,
        };
    }
    if (isFileTypeError && category) {
        return {
            title: `${category.label} files not supported`,
            detail: category.message,
            icon: 'warning',
            report: { kind: 'feature', title: category.featureTitle },
        };
    }
    if (isFileTypeError && !ext) {
        return {
            title: `"${filename}" has no file extension`,
            detail: stripPrefix(error, 'File type not allowed:'),
            icon: 'warning',
            report: null,
        };
    }
    if (isFileTypeError) {
        return {
            title: `File type not supported: ${ext}`,
            detail: 'This file format cannot be indexed. Supported: PDF, DOCX, PPTX, XLSX, EPUB, images (PNG, JPG, WEBP, ...), TXT, MD, CSV, JSON, HTML, XML, YAML, and 30+ code file formats.',
            icon: 'warning',
            report: { kind: 'feature', title: `Support ${ext} file indexing` },
        };
    }
    if (isConnectionError) {
        return {
            title: 'Connection error',
            detail: 'Could not reach the GAIA server. Make sure the backend is running.',
            icon: 'alert',
            report: { kind: 'bug', title: `Upload connection error for ${filename}` },
        };
    }
    return {
        title: `Failed to index "${filename}"`,
        detail: error || 'An unexpected error occurred during indexing.',
        icon: 'bug',
        report: { kind: 'bug', title: `Indexing error: ${error.slice(0, 80)}` },
    };
}
