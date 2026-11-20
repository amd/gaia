// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The API's error types and error-body parsing, free of fetch and the DOM so any client can share them. */

/**
 * Pull a human-readable message out of an error response body.
 *
 * FastAPI's `detail` is a plain string for `HTTPException`, but a *list of
 * error objects* for request-validation failures (422) — interpolating one of
 * those into a message renders "[object Object]" in the UI.
 */
export function parseErrorDetail(errorText: string): string {
    try {
        const parsed = JSON.parse(errorText).detail;
        if (Array.isArray(parsed)) {
            return parsed.map((e) => e?.msg ?? JSON.stringify(e)).join('; ') || errorText;
        }
        if (parsed && typeof parsed === 'object' && typeof parsed.message === 'string') return parsed.message;
        if (parsed && typeof parsed !== 'string') return JSON.stringify(parsed);
        return parsed || errorText;
    } catch {
        // Body isn't JSON (proxy HTML, empty response) — the raw text is the useful thing.
        return errorText;
    }
}

/** `detail.status` from a structured error body, e.g. `needs_key`; null otherwise. */
export function parseErrorStatus(errorText: string): string | null {
    try {
        const status = JSON.parse(errorText).detail?.status;
        return typeof status === 'string' ? status : null;
    } catch {
        return null;
    }
}

/** Request fields a 422 names, from the last part of each error's `loc` (e.g. `retention_days`). */
export function parseErrorFields(errorText: string): string[] {
    try {
        const parsed = JSON.parse(errorText).detail;
        if (!Array.isArray(parsed)) return [];
        return parsed
            .map((e) => (Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : undefined))
            .filter((f): f is string => typeof f === 'string');
    } catch {
        return [];
    }
}

/** `detail` when the backend sent an object (e.g. a 409 asking to confirm), else null. */
export function parseErrorBody(errorText: string): Record<string, unknown> | null {
    try {
        const parsed = JSON.parse(errorText).detail;
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch {
        return null;
    }
}

/** A non-2xx answer: the message to show, the backend's own words, and its machine-readable reason. */
export class ApiError extends Error {
    readonly httpStatus: number;
    readonly detail: string;
    readonly reason: string | null;
    readonly fields: string[];
    /** The structured `detail` object, when the backend sent one. */
    readonly body: Record<string, unknown> | null;

    constructor(message: string, httpStatus: number, detail: string, reason: string | null, fields: string[] = [], body: Record<string, unknown> | null = null) {
        super(message);
        this.name = 'ApiError';
        this.httpStatus = httpStatus;
        this.detail = detail;
        this.reason = reason;
        this.fields = fields;
        this.body = body;
    }
}

/** Starlette's bare body or the UI server's catch-all ("Internal server error") — no cause. */
export function isBareInternalError(detail: string): boolean {
    return detail.trim().toLowerCase() === 'internal server error';
}

/**
 * False when a 500 carries no real cause: a bare "Internal Server Error",
 * a proxy HTML page, or a raw JSON body with an empty or missing `detail`.
 */
function hasUsefulServerDetail(detail: string): boolean {
    const text = detail.trim();
    return text !== '' && !isBareInternalError(text) && !/^[<{[]/.test(text);
}

export function getFriendlyError(status: number, detail: string): string {
    switch (status) {
        case 403: return detail || 'Access denied.';
        // The backend's detail first: a canned "moved or deleted" hid a malformed path (#728).
        case 404: return detail || 'The requested item was not found.';
        case 413: return detail || 'File too large to process.';
        case 500: return hasUsefulServerDetail(detail) ? detail : 'Server error. Please try again.';
        // GAIA's own 502 says what the model server did; a proxy's carries no sentence.
        case 502: return hasUsefulServerDetail(detail) && !/^(502\s+)?bad gateway\.?$/i.test(detail.trim())
            ? detail : 'Service unavailable. Is the backend running?';
        case 503: return detail || 'Service unavailable.';
        default: return detail || `Request failed (HTTP ${status})`;
    }
}

/** The stream went silent past STREAM_IDLE_MS; the run may still be going server-side. */
export class StreamStalledError extends Error {
    constructor(readonly idleMs: number) {
        super(`The connection to GAIA went silent for ${Math.round(idleMs / 1000)} s.`);
        this.name = 'StreamStalledError';
    }
}

/** The connection broke after the run started (network switch, proxy reset, restart). */
export class StreamDroppedError extends Error {
    /** `unreachable`: the request never reached GAIA, so nothing there is still answering. */
    constructor(readonly cause: unknown, readonly unreachable = false) {
        super(`The connection to GAIA was cut: ${cause instanceof Error ? cause.message : String(cause)}`);
        this.name = 'StreamDroppedError';
    }
}

/** The backend never started answering the request. */
export class StreamTimeoutError extends Error {
    constructor(readonly endpoint: 'send' | 'attach', readonly timeoutMs: number) {
        super(`GAIA did not start answering within ${Math.round(timeoutMs / 1000)} s.`);
        this.name = 'StreamTimeoutError';
    }
}
