// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for GAIA Chat UI backend. */

import type { Session, Message, Document, SystemStatus, StreamEvent } from '../types';

const API_BASE = '/api';

// ── System ─────────────────────────────────────────────────────────────────

export async function getSystemStatus(): Promise<SystemStatus> {
    const res = await fetch(`${API_BASE}/system/status`);
    return res.json();
}

export async function getHealth(): Promise<{ status: string; stats: Record<string, number> }> {
    const res = await fetch(`${API_BASE}/health`);
    return res.json();
}

// ── Sessions ───────────────────────────────────────────────────────────────

export async function listSessions(): Promise<{ sessions: Session[]; total: number }> {
    const res = await fetch(`${API_BASE}/sessions`);
    return res.json();
}

export async function createSession(data: Partial<Session> = {}): Promise<Session> {
    const res = await fetch(`${API_BASE}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    return res.json();
}

export async function getSession(id: string): Promise<Session> {
    const res = await fetch(`${API_BASE}/sessions/${id}`);
    return res.json();
}

export async function updateSession(id: string, data: { title?: string; system_prompt?: string }): Promise<Session> {
    const res = await fetch(`${API_BASE}/sessions/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    return res.json();
}

export async function deleteSession(id: string): Promise<void> {
    await fetch(`${API_BASE}/sessions/${id}`, { method: 'DELETE' });
}

export async function getMessages(sessionId: string): Promise<{ messages: Message[]; total: number }> {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/messages`);
    return res.json();
}

export async function exportSession(sessionId: string): Promise<{ content: string }> {
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/export?format=markdown`);
    return res.json();
}

// ── Chat (Streaming) ──────────────────────────────────────────────────────

export function sendMessageStream(
    sessionId: string,
    message: string,
    onChunk: (event: StreamEvent) => void,
    onDone: (event: StreamEvent) => void,
    onError: (error: Error) => void,
): AbortController {
    const controller = new AbortController();

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
            const reader = res.body?.getReader();
            if (!reader) {
                onError(new Error('No response body'));
                return;
            }

            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const raw = line.slice(6).trim();
                        if (!raw) continue;
                        try {
                            const event: StreamEvent = JSON.parse(raw);
                            if (event.type === 'chunk') onChunk(event);
                            else if (event.type === 'done') onDone(event);
                            else if (event.type === 'error') onError(new Error(event.content || 'Unknown error'));
                        } catch {
                            // skip malformed
                        }
                    }
                }
            }

            // If no explicit done event was sent, signal completion
            onDone({ type: 'done' });
        })
        .catch((err) => {
            if (err.name !== 'AbortError') {
                onError(err);
            }
        });

    return controller;
}

// ── Documents ──────────────────────────────────────────────────────────────

export async function listDocuments(): Promise<{ documents: Document[]; total: number; total_size_bytes: number; total_chunks: number }> {
    const res = await fetch(`${API_BASE}/documents`);
    return res.json();
}

export async function uploadDocumentByPath(filepath: string): Promise<Document> {
    const res = await fetch(`${API_BASE}/documents/upload-path`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filepath }),
    });
    return res.json();
}

export async function deleteDocument(id: string): Promise<void> {
    await fetch(`${API_BASE}/documents/${id}`, { method: 'DELETE' });
}

export async function attachDocument(sessionId: string, documentId: string): Promise<void> {
    await fetch(`${API_BASE}/sessions/${sessionId}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_id: documentId }),
    });
}

export async function detachDocument(sessionId: string, documentId: string): Promise<void> {
    await fetch(`${API_BASE}/sessions/${sessionId}/documents/${documentId}`, { method: 'DELETE' });
}
