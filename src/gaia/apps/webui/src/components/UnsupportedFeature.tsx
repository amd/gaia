// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { AlertTriangle, X, AlertCircle, Bug } from 'lucide-react';
import { describeUploadError } from '../utils/uploadErrors.core';
import { GITHUB_NEW_ISSUE_URL, buildBugReportUrl } from '../utils/bugReport.core';
import { useT } from '../i18n/useT';
import './UnsupportedFeature.css';

export { getUnsupportedCategory, isExtensionSupported, SUPPORTED_EXTENSIONS } from '../utils/uploadErrors.core';

const GITHUB_FEATURE_REQUEST_URL = `${GITHUB_NEW_ISSUE_URL}?template=feature_request.md`;
const GITHUB_BUG_REPORT_URL = `${GITHUB_NEW_ISSUE_URL}?template=bug_report.md`;

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
        labels: 'enhancement,agent-ui',
    });
    return `${GITHUB_NEW_ISSUE_URL}?${params.toString()}`;
}

/** Create a GitHub bug report URL with pre-filled title and body. */
export function bugReportUrl(title: string, errorDetail?: string): string {
    return buildBugReportUrl(title, errorDetail, getEnvironmentInfo());
}


interface UploadErrorToastProps {
    /** The filename that failed. */
    filename: string;
    /** The error message from the backend. */
    error: string;
    /** HTTP status of the failed request, when there was one. */
    status?: number;
    /** The backend's error kind (``detail.status``), e.g. ``corrupted``. */
    kind?: string | null;
    /** Called when the user dismisses the toast. */
    onDismiss: () => void;
    /** Auto-dismiss timeout in ms (default: 15000). 0 = no auto-dismiss. */
    timeout?: number;
}

/**
 * A dismissible error toast for document upload failures.
 * Parses the backend error to show helpful context and a GitHub link.
 */
export function UploadErrorToast({ filename, error, status, kind, onDismiss, timeout = 15000 }: UploadErrorToastProps) {
    const t = useT();
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (timeout > 0) {
            timerRef.current = setTimeout(onDismiss, timeout);
        }
        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [onDismiss, timeout]);

    const { title, detail, icon, report } = describeUploadError(t, { filename, error, status, kind });
    const Icon = icon === 'warning' ? AlertTriangle : icon === 'alert' ? AlertCircle : Bug;
    const issueUrl = report === null ? null : report.kind === 'feature' ? featureRequestUrl(report.title) : bugReportUrl(report.title);

    return (
        <div className="upload-error-toast" role="alert">
            <Icon size={16} />
            <div className="upload-error-content">
                <div className="upload-error-title">{title}</div>
                <div className="upload-error-detail">
                    {detail}
                    {issueUrl && ' '}
                    {issueUrl === null ? null : report?.kind === 'feature' ? (
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
