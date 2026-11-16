// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey, Params } from '@webui/i18n/registry';

// Every error carries a catalog key and params; `describeError` (i18n) renders it for the person.
export class GaiaError extends Error {
    readonly messageKey: MessageKey;
    readonly params: Params;

    constructor(messageKey: MessageKey, params: Params = {}, options?: { cause?: unknown }) {
        super(`${messageKey} ${JSON.stringify(params)}`, options);
        this.name = new.target.name;
        this.messageKey = messageKey;
        this.params = params;
    }
}

export class GaiaConfigError extends GaiaError {}

export class GaiaApiError extends GaiaError {
    readonly status: number;
    readonly detail: string;
    /** The error's `detail` when GAIA sent an object, such as a 409 asking to be confirmed. */
    readonly body: Record<string, unknown> | null;

    constructor(
        status: number,
        detail: string,
        messageKey: MessageKey = 'mobileApp.error.http',
        params?: Params,
        body: Record<string, unknown> | null = null,
    ) {
        super(messageKey, params ?? { status, detail });
        this.status = status;
        this.detail = detail;
        this.body = body;
    }
}

export class GaiaAuthError extends GaiaApiError {
    constructor(detail: string) {
        super(401, detail, 'mobileApp.error.auth', { detail });
    }
}

export class GaiaNetworkError extends GaiaError {
    constructor(url: string, cause: unknown) {
        const reason = cause instanceof Error ? cause.message : String(cause);
        super('mobileApp.error.network', { url, reason }, { cause });
    }
}

/** A paired PC's certificate doesn't match the pin this phone paired with: a hard failure, never "trust anyway". */
export class GaiaPinMismatchError extends GaiaError {
    constructor(options?: { cause?: unknown }) {
        super('mobileApp.pairing.pinMismatch', {}, options);
    }
}

/** A file upload to a paired PC this app can't send: the app build predates uploads, or the file isn't on disk. */
export class GaiaUploadUnsupportedError extends GaiaError {
    readonly code = 'unsupported_body';

    constructor() {
        super('mobileApp.pairing.uploadUnsupported', {});
    }
}

/** A production build has no pinned transport, and the app won't send a request to the PC without it. */
export class GaiaTransportMissingError extends GaiaError {
    constructor() {
        super('mobileApp.pairing.transportMissing', {});
    }
}

export class GaiaStreamError extends GaiaError {}

export type StreamEndpoint = 'send' | 'attach';

/** GAIA never started answering: no response within the limit. */
export class GaiaStreamTimeoutError extends GaiaStreamError {
    readonly endpoint: StreamEndpoint;
    readonly timeoutMs: number;

    constructor(endpoint: StreamEndpoint, timeoutMs: number) {
        const seconds = Math.round(timeoutMs / 1000);
        super(endpoint === 'send' ? 'mobileApp.error.sendTimeout' : 'mobileApp.error.attachTimeout', { seconds });
        this.endpoint = endpoint;
        this.timeoutMs = timeoutMs;
    }
}

/** The stream went silent past its idle limit; the turn may still be running on the PC. */
export class GaiaStreamStalledError extends GaiaStreamError {
    readonly idleMs: number;

    constructor(idleMs: number) {
        super('mobileApp.error.streamStalled', { seconds: Math.round(idleMs / 1000) });
        this.idleMs = idleMs;
    }
}

/** The connection broke after the turn started; the turn may still be running on the PC. */
export class GaiaStreamDroppedError extends GaiaStreamError {
    constructor(cause: unknown) {
        super('mobileApp.error.streamBroke', {}, { cause });
    }
}

/** The last re-attach never reached GAIA, so nothing there is still answering. */
export class GaiaUnreachableError extends GaiaError {
    constructor(cause: unknown) {
        super('chatError.gaiaStopped', {}, { cause });
    }
}

/** The turn itself failed: GAIA sent an `error` event, with its own words. */
export class GaiaTurnError extends GaiaError {
    readonly detail: string;

    constructor(detail: string) {
        super('mobileApp.error.turnFailed', { detail });
        this.detail = detail;
    }
}

/** A failure this layer has no name for; the original error is its cause. */
export function asGaiaError(err: unknown): GaiaError {
    if (err instanceof GaiaError) return err;
    const reason = err instanceof Error ? err.message : String(err);
    return new GaiaError('mobileApp.error.unexpected', { reason }, { cause: err });
}

// FastAPI's `detail` is a string, an object, or a list of validation errors.
export function parseErrorDetail(errorText: string): string {
    try {
        const parsed: unknown = JSON.parse(errorText).detail;
        if (Array.isArray(parsed)) {
            const joined = parsed
                .map((e: { msg?: unknown }) => (typeof e?.msg === 'string' ? e.msg : JSON.stringify(e)))
                .join('; ');
            return joined || errorText;
        }
        if (parsed && typeof parsed === 'object') {
            const message = (parsed as { message?: unknown }).message;
            return typeof message === 'string' ? message : JSON.stringify(parsed);
        }
        if (typeof parsed === 'string' && parsed) return parsed;
        return errorText;
    } catch {
        // Not JSON (proxy HTML, empty body): the raw text is the useful part.
        return errorText;
    }
}

/** `detail` when GAIA sent an object (a 409 asking to be confirmed), else null. */
export function parseErrorBody(errorText: string): Record<string, unknown> | null {
    try {
        const parsed: unknown = JSON.parse(errorText).detail;
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
    } catch {
        return null;
    }
}
