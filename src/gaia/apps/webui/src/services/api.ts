// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for GAIA Agent UI backend. */

import type { Session, Message, Document, SystemStatus, StreamEvent, TunnelStatus, BrowseResponse, IndexFolderResponse } from '../types';
import { log } from '../utils/logger';

const API_BASE = '/api';

// -- Helpers -------------------------------------------------------------------

/** Fetch wrapper with logging, timing, and error handling. */
async function apiFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${API_BASE}${path}`;
    const t = log.api.time();

    log.api.info(`${method} ${url}`, body !== undefined ? { body } : '');

    const init: RequestInit = {
        method,
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
    };

    let res: Response;
    try {
        res = await fetch(url, init);
    } catch (err) {
        log.api.error(`${method} ${url} - network error`, err);
        throw err;
    }

    if (!res.ok) {
        const errorText = await res.text().catch(() => 'unable to read response body');
        log.api.error(`${method} ${url} - HTTP ${res.status} ${res.statusText}`, { errorText });
        throw new Error(`API ${res.status}: ${errorText}`);
    }

    // Some endpoints (DELETE) may not return JSON
    const contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
        log.api.timed(`${method} ${url} -> ${res.status} (no body)`, t);
        return undefined as T;
    }

    const data = await res.json();
    log.api.timed(`${method} ${url} -> ${res.status}`, t, data);
    return data;
}

// -- System --------------------------------------------------------------------

export async function getSystemStatus(): Promise<SystemStatus> {
    return apiFetch<SystemStatus>('GET', '/system/status');
}

export async function getHealth(): Promise<{ status: string; stats: Record<string, number> }> {
    return apiFetch('GET', '/health');
}

// -- Sessions ------------------------------------------------------------------

export async function listSessions(): Promise<{ sessions: Session[]; total: number }> {
    return apiFetch('GET', '/sessions');
}

export async function createSession(data: Partial<Session> = {}): Promise<Session> {
    return apiFetch('POST', '/sessions', data);
}

export async function getSession(id: string): Promise<Session> {
    return apiFetch('GET', `/sessions/${id}`);
}

export async function updateSession(id: string, data: { title?: string; system_prompt?: string }): Promise<Session> {
    return apiFetch('PUT', `/sessions/${id}`, data);
}

export async function deleteSession(id: string): Promise<void> {
    return apiFetch('DELETE', `/sessions/${id}`);
}

export async function getMessages(sessionId: string): Promise<{ messages: Message[]; total: number }> {
    return apiFetch('GET', `/sessions/${sessionId}/messages`);
}

export async function exportSession(sessionId: string): Promise<{ content: string }> {
    return apiFetch('GET', `/sessions/${sessionId}/export?format=markdown`);
}

export async function deleteMessage(sessionId: string, messageId: number): Promise<void> {
    return apiFetch('DELETE', `/sessions/${sessionId}/messages/${messageId}`);
}

export async function deleteMessagesFrom(sessionId: string, messageId: number): Promise<{ deleted: boolean; count: number }> {
    return apiFetch('DELETE', `/sessions/${sessionId}/messages/${messageId}/and-below`);
}

// -- Chat (Streaming with Agent Events) ----------------------------------------

/**
 * Callbacks for agent streaming events.
 *
 * The stream can produce both text chunks (for the response)
 * and agent activity events (steps, tool calls, thinking).
 */
export interface StreamCallbacks {
    /** Text chunk for the response content. */
    onChunk: (event: StreamEvent) => void;
    /** Agent activity event (step, tool, thinking, plan, etc.). */
    onAgentEvent: (event: StreamEvent) => void;
    /** Stream complete with final response. */
    onDone: (event: StreamEvent) => void;
    /** Error occurred. */
    onError: (error: Error) => void;
}

/** Agent event types that represent activity rather than content. */
const AGENT_EVENT_TYPES = new Set([
    'status', 'step', 'thinking', 'plan',
    'tool_start', 'tool_end', 'tool_result', 'tool_args', 'agent_error',
]);

export function sendMessageStream(
    sessionId: string,
    message: string,
    onChunkOrCallbacks: ((event: StreamEvent) => void) | StreamCallbacks,
    onDone?: (event: StreamEvent) => void,
    onError?: (error: Error) => void,
): AbortController {
    // Support both old 3-arg style and new callbacks style
    let callbacks: StreamCallbacks;
    if (typeof onChunkOrCallbacks === 'function') {
        callbacks = {
            onChunk: onChunkOrCallbacks,
            onAgentEvent: () => {},  // no-op if using old API
            onDone: onDone || (() => {}),
            onError: onError || ((err) => { log.stream.error('Unhandled stream error', err); }),
        };
    } else {
        callbacks = onChunkOrCallbacks;
    }

    const controller = new AbortController();
    const t = log.stream.time();
    let chunkCount = 0;
    let totalChars = 0;
    let agentEventCount = 0;

    log.stream.info(`Starting SSE stream for session=${sessionId}`, { messageLength: message.length });

    fetch(`${API_BASE}/chat/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session_id: sessionId,
            message,
            stream: true,
        }),
        signal: controller.signal,
    })
        .then(async (res) => {
            log.stream.info(`SSE connection opened -> HTTP ${res.status}`);

            if (!res.ok) {
                const errText = await res.text().catch(() => '');
                log.stream.error(`SSE connection failed: HTTP ${res.status}`, errText);
                callbacks.onError(new Error(`HTTP ${res.status}: ${errText}`));
                return;
            }

            const reader = res.body?.getReader();
            if (!reader) {
                log.stream.error('No response body reader available');
                callbacks.onError(new Error('No response body'));
                return;
            }

            const decoder = new TextDecoder();
            let buffer = '';
            let doneReceived = false;

            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) {
                        log.stream.debug('SSE reader done (stream ended)');
                        break;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const raw = line.slice(6).trim();
                            if (!raw) continue;
                            try {
                                const event: StreamEvent = JSON.parse(raw);

                                if (event.type === 'chunk') {
                                    chunkCount++;
                                    totalChars += (event.content || '').length;
                                    if (chunkCount <= 3 || chunkCount % 50 === 0) {
                                        log.stream.debug(`Chunk #${chunkCount} (+${(event.content || '').length} chars)`);
                                    }
                                    callbacks.onChunk(event);
                                } else if (event.type === 'answer') {
                                    // Agent final answer - treat as content
                                    callbacks.onChunk(event);
                                } else if (event.type === 'done') {
                                    doneReceived = true;
                                    log.stream.timed(`Stream complete: ${chunkCount} chunks, ${totalChars} chars, ${agentEventCount} agent events`, t);
                                    callbacks.onDone(event);
                                } else if (event.type === 'error') {
                                    log.stream.error(`Stream error event:`, event.content);
                                    callbacks.onError(new Error(event.content || 'Unknown error'));
                                } else if (AGENT_EVENT_TYPES.has(event.type)) {
                                    agentEventCount++;
                                    log.stream.debug(`Agent event: ${event.type}`, event);
                                    callbacks.onAgentEvent(event);
                                } else {
                                    log.stream.warn(`Unknown SSE event type: ${event.type}`, event);
                                }
                            } catch (parseErr) {
                                log.stream.warn(`Malformed SSE data, skipping`, { raw: raw.slice(0, 100) });
                            }
                        }
                    }
                }
            } finally {
                // Release the reader to free the underlying connection
                reader.releaseLock();
            }

            // Only signal completion if no explicit done event was received during the stream
            if (!doneReceived) {
                log.stream.timed(`SSE connection closed without done event: ${chunkCount} chunks, ${agentEventCount} agent events`, t);
                callbacks.onDone({ type: 'done' });
            }
        })
        .catch((err) => {
            if (err.name === 'AbortError') {
                log.stream.warn(`Stream aborted by user after ${chunkCount} chunks`);
            } else {
                log.stream.error(`Stream fetch error`, err);
                callbacks.onError(err);
            }
        });

    return controller;
}

// -- Documents -----------------------------------------------------------------

export async function listDocuments(): Promise<{ documents: Document[]; total: number; total_size_bytes: number; total_chunks: number }> {
    return apiFetch('GET', '/documents');
}

export async function uploadDocumentByPath(filepath: string): Promise<Document> {
    return apiFetch('POST', '/documents/upload-path', { filepath });
}

export async function deleteDocument(id: string): Promise<void> {
    return apiFetch('DELETE', `/documents/${id}`);
}

export async function getDocumentStatus(id: string): Promise<{
    id: string;
    indexing_status: string;
    chunk_count: number;
    is_active: boolean;
}> {
    return apiFetch('GET', `/documents/${id}/status`);
}

export async function cancelIndexing(id: string): Promise<{ cancelled: boolean; id: string }> {
    return apiFetch('POST', `/documents/${id}/cancel`);
}

export async function attachDocument(sessionId: string, documentId: string): Promise<void> {
    return apiFetch('POST', `/sessions/${sessionId}/documents`, { document_id: documentId });
}

export async function detachDocument(sessionId: string, documentId: string): Promise<void> {
    return apiFetch('DELETE', `/sessions/${sessionId}/documents/${documentId}`);
}

// -- File Browser -----------------------------------------------------------------

export async function browseFiles(path?: string): Promise<BrowseResponse> {
    const params = path ? `?path=${encodeURIComponent(path)}` : '';
    return apiFetch('GET', `/files/browse${params}`);
}

export async function indexFolder(folderPath: string, recursive: boolean = true): Promise<IndexFolderResponse> {
    return apiFetch('POST', '/documents/index-folder', { folder_path: folderPath, recursive });
}

export async function openFileOrFolder(path: string, reveal: boolean = true): Promise<{ status: string; path: string }> {
    return apiFetch('POST', '/files/open', { path, reveal });
}

/** Upload a file (image/document) to the server. */
export async function uploadFile(file: File): Promise<{
    filename: string;
    original_name: string;
    url: string;
    size: number;
    content_type: string;
    is_image: boolean;
}> {
    const url = `${API_BASE}/files/upload`;
    const t = log.api.time();
    log.api.info(`POST ${url}`, { fileName: file.name, size: file.size });

    const formData = new FormData();
    formData.append('file', file);

    const res = await fetch(url, {
        method: 'POST',
        body: formData,
    });

    if (!res.ok) {
        const errorText = await res.text().catch(() => 'Upload failed');
        log.api.error(`POST ${url} - HTTP ${res.status}`, { errorText });
        throw new Error(`Upload failed: ${errorText}`);
    }

    const data = await res.json();
    log.api.timed(`POST ${url} -> ${res.status}`, t, data);
    return data;
}

// -- File Search & Preview ----------------------------------------------------------

export async function searchFiles(query: string, fileTypes?: string, maxResults?: number): Promise<{
    results: Array<{ name: string; path: string; size: number; size_display: string; extension: string; modified: string; directory: string }>;
    total: number;
    query: string;
}> {
    const params = new URLSearchParams({ query });
    if (fileTypes) params.set('file_types', fileTypes);
    if (maxResults) params.set('max_results', String(maxResults));
    return apiFetch('GET', `/files/search?${params}`);
}

export async function previewFile(path: string, lines?: number): Promise<{
    path: string;
    name: string;
    size: number;
    size_display: string;
    extension: string;
    modified: string;
    is_text: boolean;
    preview_lines: string[];
    total_lines: number | null;
    columns: string[] | null;
    row_count: number | null;
}> {
    const params = new URLSearchParams({ path });
    if (lines) params.set('lines', String(lines));
    return apiFetch('GET', `/files/preview?${params}`);
}

// -- Mobile Access / Tunnel -------------------------------------------------------

export async function startTunnel(): Promise<TunnelStatus> {
    return apiFetch('POST', '/tunnel/start');
}

export async function stopTunnel(): Promise<{ active: boolean }> {
    return apiFetch('POST', '/tunnel/stop');
}

export async function getTunnelStatus(): Promise<TunnelStatus> {
    return apiFetch('GET', '/tunnel/status');
}
