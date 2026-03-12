// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { AlertTriangle, Lightbulb, ExternalLink, X, AlertCircle, Bug } from 'lucide-react';
import './UnsupportedFeature.css';

const GITHUB_ISSUES_URL = 'https://github.com/amd/gaia/issues';
const GITHUB_NEW_ISSUE_URL = 'https://github.com/amd/gaia/issues/new';
const GITHUB_FEATURE_REQUEST_URL = `${GITHUB_NEW_ISSUE_URL}?template=feature_request.md`;
const GITHUB_BUG_REPORT_URL = `${GITHUB_NEW_ISSUE_URL}?template=bug_report.md`;

// ── Unsupported file type categories ─────────────────────────────────────

interface FileTypeCategory {
    label: string;
    extensions: Set<string>;
    message: string;
    alternatives: string[];
    featureTitle: string;
}

const UNSUPPORTED_FILE_CATEGORIES: FileTypeCategory[] = [
    {
        label: 'Image',
        extensions: new Set(['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico', '.heic', '.heif']),
        message: 'Image files cannot be indexed for text search.',
        alternatives: [
            'Index PDFs that contain images — text is extracted automatically',
            'Paste image descriptions or OCR text directly into the chat',
            'Use GAIA\'s VLM agent for image analysis: gaia vlm',
        ],
        featureTitle: 'Support image file indexing and OCR',
    },
    {
        label: 'Video',
        extensions: new Set(['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v']),
        message: 'Video files are not supported for indexing.',
        alternatives: [
            'Extract subtitles or transcripts from videos and index those',
            'Use GAIA\'s voice/talk mode for audio: gaia talk',
        ],
        featureTitle: 'Support video file indexing',
    },
    {
        label: 'Audio',
        extensions: new Set(['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus']),
        message: 'Audio files are not supported for indexing.',
        alternatives: [
            'Use GAIA\'s voice mode for speech interaction: gaia talk',
            'Transcribe audio to text first, then index the transcript',
        ],
        featureTitle: 'Support audio file transcription and indexing',
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

/** Set of all supported extensions for document indexing. */
export const SUPPORTED_EXTENSIONS = new Set([
    '.pdf', '.txt', '.md', '.csv', '.json', '.doc', '.docx',
    '.ppt', '.pptx', '.xls', '.xlsx', '.html', '.htm', '.xml', '.svg',
    '.yaml', '.yml', '.py', '.js', '.ts', '.java', '.c', '.cpp',
    '.h', '.rs', '.go', '.rb', '.sh', '.bat', '.ps1', '.log',
    '.cfg', '.ini', '.toml',
]);

/** Check if a file extension is supported for indexing. */
export function isExtensionSupported(extension: string): boolean {
    const ext = extension.toLowerCase().startsWith('.') ? extension.toLowerCase() : `.${extension.toLowerCase()}`;
    return SUPPORTED_EXTENSIONS.has(ext);
}

// ── Build a GitHub issue URL ─────────────────────────────────────────────

/** Gather environment info for pre-filling GitHub issues. */
function getEnvironmentInfo(): string {
    const version = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown';
    const ua = navigator.userAgent;
    const platform = navigator.platform || 'unknown';
    return `**Environment:**\n- GAIA Version: ${version}\n- Platform: ${platform}\n- User Agent: ${ua}\n- Interface: Agent UI (Web)`;
}

/** Create a GitHub feature request URL with pre-filled title and body. */
export function featureRequestUrl(title: string, context?: string): string {
    const body = [
        '## Feature Description',
        '',
        context || `I would like GAIA to support: **${title}**`,
        '',
        '## Use Case',
        '',
        '_Describe how you would use this feature:_',
        '',
        '## Current Workaround',
        '',
        '_Is there a workaround you are currently using?_',
        '',
        '---',
        getEnvironmentInfo(),
        '',
        '_Submitted from GAIA Agent UI_',
    ].join('\n');

    const params = new URLSearchParams({
        title: `[Feature] ${title}`,
        body,
        labels: 'enhancement,chat-ui',
    });
    return `${GITHUB_NEW_ISSUE_URL}?${params.toString()}`;
}

/** Create a GitHub bug report URL with pre-filled title and body. */
export function bugReportUrl(title: string, errorDetail?: string): string {
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
        getEnvironmentInfo(),
        '',
        '_Submitted from GAIA Agent UI_',
    ].join('\n');

    const params = new URLSearchParams({
        title: `[Bug] ${title}`,
        body,
        labels: 'bug,chat-ui',
    });
    return `${GITHUB_NEW_ISSUE_URL}?${params.toString()}`;
}

// ── Upload Error Toast ───────────────────────────────────────────────────

interface UploadErrorToastProps {
    /** The filename that failed. */
    filename: string;
    /** The error message from the backend. */
    error: string;
    /** Called when the user dismisses the toast. */
    onDismiss: () => void;
    /** Auto-dismiss timeout in ms (default: 15000). 0 = no auto-dismiss. */
    timeout?: number;
}

/**
 * A dismissible error toast for document upload failures.
 * Parses the backend error to show helpful context and a GitHub link.
 */
export function UploadErrorToast({ filename, error, onDismiss, timeout = 15000 }: UploadErrorToastProps) {
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (timeout > 0) {
            timerRef.current = setTimeout(onDismiss, timeout);
        }
        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [onDismiss, timeout]);

    // Extract extension from filename
    const ext = filename.includes('.') ? '.' + filename.split('.').pop()?.toLowerCase() : '';
    const category = ext ? getUnsupportedCategory(ext) : null;

    // Determine if it's a file type issue or a general error
    const isFileTypeError = error.toLowerCase().includes('unsupported file type') ||
        error.toLowerCase().includes('not supported for indexing') ||
        category !== null;

    const isConnectionError = error.toLowerCase().includes('fetch') ||
        error.toLowerCase().includes('network') ||
        error.toLowerCase().includes('connect');

    let title: string;
    let detail: string;
    let issueUrl: string;
    let Icon = AlertCircle;

    if (isFileTypeError && category) {
        title = `${category.label} files not supported`;
        detail = category.message;
        issueUrl = featureRequestUrl(category.featureTitle);
        Icon = AlertTriangle;
    } else if (isFileTypeError) {
        title = `File type not supported: ${ext}`;
        detail = 'This file format cannot be indexed. Supported: PDF, TXT, MD, CSV, JSON, Office docs, code files, and more.';
        issueUrl = featureRequestUrl(`Support ${ext} file indexing`);
        Icon = AlertTriangle;
    } else if (isConnectionError) {
        title = 'Connection error';
        detail = 'Could not reach the GAIA server. Make sure the backend is running.';
        issueUrl = bugReportUrl(`Upload connection error for ${filename}`);
        Icon = AlertCircle;
    } else {
        title = `Failed to index "${filename}"`;
        detail = error || 'An unexpected error occurred during indexing.';
        issueUrl = bugReportUrl(`Indexing error: ${error.slice(0, 80)}`);
        Icon = Bug;
    }

    return (
        <div className="upload-error-toast" role="alert">
            <Icon size={16} />
            <div className="upload-error-content">
                <div className="upload-error-title">{title}</div>
                <div className="upload-error-detail">
                    {detail}
                    {' '}
                    {isFileTypeError ? (
                        <a href={issueUrl} target="_blank" rel="noopener noreferrer">
                            Request this feature →
                        </a>
                    ) : (
                        <a href={issueUrl} target="_blank" rel="noopener noreferrer">
                            Report this issue →
                        </a>
                    )}
                </div>
            </div>
            <button className="upload-error-dismiss" onClick={onDismiss} aria-label="Dismiss">
                <X size={14} />
            </button>
        </div>
    );
}

// ── Inline Unsupported Feature Banner (for chat messages) ────────────────

interface UnsupportedFeatureBannerProps {
    /** Short title of the unsupported feature. */
    title: string;
    /** Description of what the user asked for. */
    description: string;
    /** Things the user can do instead. */
    alternatives?: string[];
    /** Pre-filled feature request title for GitHub. */
    featureTitle?: string;
}

/**
 * Rendered inside assistant messages when the agent detects an unsupported feature.
 * Shows what's available instead and links to GitHub for feature requests.
 */
export function UnsupportedFeatureBanner({ title, description, alternatives, featureTitle }: UnsupportedFeatureBannerProps) {
    const issueUrl = featureTitle
        ? featureRequestUrl(featureTitle)
        : `${GITHUB_ISSUES_URL}`;

    return (
        <div className="unsupported-banner">
            <div className="unsupported-banner-header">
                <AlertTriangle size={16} />
                <span>{title}</span>
            </div>
            <div className="unsupported-banner-body">
                <p>{description}</p>
                {alternatives && alternatives.length > 0 && (
                    <>
                        <p><strong>What you can do instead:</strong></p>
                        <ul className="unsupported-alternatives">
                            {alternatives.map((alt, i) => (
                                <li key={i}>{alt}</li>
                            ))}
                        </ul>
                    </>
                )}
            </div>
            <div className="unsupported-feature-request">
                <Lightbulb size={14} />
                <span>
                    Want this feature?{' '}
                    <a href={issueUrl} target="_blank" rel="noopener noreferrer">
                        Request it on GitHub
                        <ExternalLink size={10} style={{ marginLeft: 3, verticalAlign: 'middle' }} />
                    </a>
                </span>
            </div>
        </div>
    );
}

// ── Error Report Banner (for caught errors in chat) ──────────────────────

interface ErrorReportBannerProps {
    /** Short description of the error. */
    errorMessage: string;
    /** Optional additional context. */
    context?: string;
}

/**
 * Rendered when an error is caught to provide a helpful message
 * with a link to report the issue on GitHub.
 */
export function ErrorReportBanner({ errorMessage, context }: ErrorReportBannerProps) {
    const issueUrl = bugReportUrl(errorMessage.slice(0, 100));

    return (
        <div className="unsupported-banner" style={{ borderColor: 'rgba(239, 68, 68, 0.3)' }}>
            <div className="unsupported-banner-header">
                <Bug size={16} />
                <span>Something went wrong</span>
            </div>
            <div className="unsupported-banner-body">
                <p>{errorMessage}</p>
                {context && <p style={{ fontSize: '12px', opacity: 0.8 }}>{context}</p>}
            </div>
            <div className="unsupported-feature-request">
                <Bug size={14} />
                <span>
                    Unexpected error?{' '}
                    <a href={issueUrl} target="_blank" rel="noopener noreferrer">
                        Report it on GitHub
                        <ExternalLink size={10} style={{ marginLeft: 3, verticalAlign: 'middle' }} />
                    </a>
                </span>
            </div>
        </div>
    );
}
