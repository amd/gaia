// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Custom Agents settings section — export/import custom agent bundles.
 *
 * - Export: calls `POST /api/agents/export`, downloads a zip of all
 *   non-builtin agents from `~/.gaia/agents/`. Shows a credentials warning
 *   before kicking off the download, since agent source files may contain
 *   API keys or tokens.
 *
 * - Import: reads `bundle.json` from the selected zip (best-effort) so the
 *   trust modal can list which agent IDs will be installed. Bundles execute
 *   third-party Python code, so we require explicit confirmation before
 *   upload.
 *
 * Both endpoints require the `X-Gaia-UI: 1` header (CSRF guard in the
 * backend — see `src/gaia/ui/routers/agents.py`).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Download, Upload, CheckCircle2, AlertCircle } from 'lucide-react';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';
import type { AgentInfo } from '../types';

// Same base-URL logic as services/api.ts — export/import hit the REST
// backend directly (not the apiFetch wrapper) because they deal with
// binary zip payloads, not JSON.
const API_BASE = window.location.protocol === 'file:'
    ? 'http://localhost:4200/api'
    : '/api';

type Status =
    | { kind: 'idle' }
    | { kind: 'working'; message: string }
    | { kind: 'success'; message: string }
    | { kind: 'error'; message: string };

export function CustomAgentsSection() {
    const { agents, setAgents } = useChatStore();

    const [status, setStatus] = useState<Status>({ kind: 'idle' });
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const statusClearRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const errorBannerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        return () => {
            if (statusClearRef.current) clearTimeout(statusClearRef.current);
        };
    }, []);

    useEffect(() => {
        if (status.kind === 'error') {
            errorBannerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }, [status]);

    const flashStatus = useCallback((s: Status, clearAfterMs = 5000) => {
        setStatus(s);
        if (statusClearRef.current) clearTimeout(statusClearRef.current);
        if (s.kind === 'success' || s.kind === 'error') {
            statusClearRef.current = setTimeout(() => setStatus({ kind: 'idle' }), clearAfterMs);
        }
    }, []);

    const refreshAgents = useCallback(async () => {
        try {
            const data = await api.listAgents();
            setAgents(data.agents || []);
        } catch (err) {
            log.api.warn('Failed to refresh agents after import', err);
        }
    }, [setAgents]);

    // Custom agents = anything not built-in. The backend marks built-in
    // agents with source === "builtin"; anything else (user-created,
    // imported bundles) belongs in this list.
    const customAgents: AgentInfo[] = agents.filter((a) => a.source !== 'builtin');

    // ── Export ───────────────────────────────────────────────────────
    const handleExport = useCallback(async () => {
        const proceed = window.confirm(
            'Exported bundle contains your agent source files as-is. ' +
            'Any API keys, tokens, or credentials in agent.py will be ' +
            'included in the bundle. Review before sharing.\n\nContinue?'
        );
        if (!proceed) return;

        flashStatus({ kind: 'working', message: 'Exporting…' });
        try {
            const res = await fetch(`${API_BASE}/agents/export`, {
                method: 'POST',
                headers: { 'X-Gaia-UI': '1' },
            });
            if (!res.ok) {
                const text = await res.text().catch(() => '');
                let detail = text;
                try { detail = JSON.parse(text).detail || text; } catch { /* not JSON */ }
                throw new Error(detail || `Export failed (HTTP ${res.status})`);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'gaia-agents-export.zip';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            flashStatus({ kind: 'success', message: 'Export downloaded.' });
        } catch (err) {
            console.error('Agent export failed:', err);
            const message = err instanceof Error ? err.message : String(err);
            log.api.error('Agent export failed', err);
            flashStatus({ kind: 'error', message: `Export failed: ${message}` });
        }
    }, [flashStatus]);

    // ── Import ───────────────────────────────────────────────────────
    const openFilePicker = useCallback(() => {
        fileInputRef.current?.click();
    }, []);

    const handleFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        // Reset the input so selecting the same file twice re-triggers onChange.
        e.target.value = '';
        if (!file) return;

        // Best-effort pre-read of bundle.json to show agent IDs in the
        // trust modal. If this fails for any reason, we still show the
        // security confirmation — just without the agent ID list.
        // Skip pre-read for oversized files — the server will validate them.
        let agentIds: string[] | null = null;
        if (file.size <= 100 * 1024 * 1024) {
            try {
                agentIds = await readBundleAgentIds(file);
            } catch (err) {
                log.api.warn('Could not pre-read bundle.json from zip', err);
            }
        }

        const idsLine = agentIds && agentIds.length > 0
            ? `Agents to install: ${agentIds.join(', ')}`
            : 'Agents to install: (unable to read bundle contents — contents will be validated by the server)';

        const proceed = window.confirm(
            'Importing this bundle will run third-party Python code on ' +
            'your machine when the agent is selected. Only import bundles ' +
            'from sources you trust.\n\n' +
            idsLine + '\n\nImport?'
        );
        if (!proceed) return;

        flashStatus({ kind: 'working', message: 'Uploading…' });
        try {
            const form = new FormData();
            form.append('bundle', file, file.name);
            const res = await fetch(`${API_BASE}/agents/import`, {
                method: 'POST',
                headers: { 'X-Gaia-UI': '1' },
                body: form,
            });
            if (!res.ok) {
                const text = await res.text().catch(() => '');
                let detail = text;
                try { detail = JSON.parse(text).detail || text; } catch { /* not JSON */ }
                throw new Error(detail || `Import failed (HTTP ${res.status})`);
            }
            const data: { imported: string[]; overwritten: string[]; errors: Array<{ id: string; error: string }>; requires_restart?: boolean } =
                await res.json();
            // imported = all successfully placed agents (new + overwritten).
            // overwritten = subset that replaced an existing agent.
            // Show: "Installed N agent(s): X, Y (replaced: Z)" without duplication.
            const allInstalled = data.imported || [];
            const overwroteIds = data.overwritten || [];
            await refreshAgents();
            let summary: string;
            if (allInstalled.length === 0) {
                summary = 'No agents imported';
            } else {
                summary = `Installed ${allInstalled.length} agent(s): ${allInstalled.join(', ')}`;
                if (overwroteIds.length > 0) summary += ` (replaced: ${overwroteIds.join(', ')})`;
            }
            if (data.requires_restart) {
                summary += ' — restart required for replaced agents to take full effect';
            }
            if (data.errors && data.errors.length > 0) {
                const errSummary = data.errors.map((e) => `${e.id}: ${e.error}`).join('; ');
                flashStatus({
                    kind: 'error',
                    message: `${summary}. Errors: ${errSummary}`,
                });
            } else {
                flashStatus({
                    kind: 'success',
                    message: summary,
                });
            }
        } catch (err) {
            console.error('Agent import failed:', err);
            const message = err instanceof Error ? err.message : String(err);
            log.api.error('Agent import failed', err);
            flashStatus({ kind: 'error', message: `Import failed: ${message}` });
        }
    }, [flashStatus, refreshAgents]);

    const isWorking = status.kind === 'working';

    return (
        <section className="settings-section">
            <h4>Custom Agents</h4>

            {customAgents.length === 0 ? (
                <p className="danger-warning" style={{ marginBottom: 12 }}>
                    No custom agents installed yet. Import a bundle below to add one.
                </p>
            ) : (
                <div className="status-grid" style={{ marginBottom: 12 }}>
                    {customAgents.map((agent) => (
                        <div key={agent.id} className="status-row">
                            <span className="status-label">{agent.name}</span>
                            <div className="status-value-wrap">
                                <span className="status-value ok">{agent.id}</span>
                                {agent.source && agent.source !== 'builtin' && (
                                    <span className="status-hint"><code>{agent.source}</code></span>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <div className="setting-actions" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button
                    className="btn-model-save"
                    onClick={handleExport}
                    disabled={isWorking || customAgents.length === 0}
                    title={customAgents.length === 0 ? 'No custom agents to export' : undefined}
                >
                    {isWorking && status.message.startsWith('Export') ? (
                        <><Loader2 size={13} className="btn-spinner" /> Exporting…</>
                    ) : (
                        <><Download size={13} style={{ verticalAlign: -2, marginRight: 6 }} />Export All</>
                    )}
                </button>

                <button
                    className="btn-model-save"
                    onClick={openFilePicker}
                    disabled={isWorking}
                >
                    {isWorking && status.message.startsWith('Upload') ? (
                        <><Loader2 size={13} className="btn-spinner" /> Uploading…</>
                    ) : (
                        <><Upload size={13} style={{ verticalAlign: -2, marginRight: 6 }} />Import</>
                    )}
                </button>

                <input
                    ref={fileInputRef}
                    type="file"
                    accept=".zip,application/zip"
                    style={{ display: 'none' }}
                    onChange={handleFileSelected}
                />
            </div>

            {status.kind !== 'idle' && (
                <div
                    ref={errorBannerRef}
                    className="danger-warning"
                    style={{
                        marginTop: 12,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        color: status.kind === 'error' ? 'var(--accent-gold)' : 'var(--text-secondary)',
                    }}
                    role="status"
                >
                    {status.kind === 'working' && <Loader2 size={14} className="btn-spinner" />}
                    {status.kind === 'success' && <CheckCircle2 size={14} />}
                    {status.kind === 'error' && <AlertCircle size={14} />}
                    <span>{status.message}</span>
                </div>
            )}
        </section>
    );
}

// ── Zip pre-read helper ──────────────────────────────────────────────────

/**
 * Best-effort extraction of `agent_ids` from `bundle.json` inside a zip file.
 *
 * Parses only enough of the ZIP structure to locate the `bundle.json` entry
 * (via the End-of-Central-Directory record and a single central-directory
 * walk), then inflates it using the browser's built-in `DecompressionStream`.
 *
 * Returns null if the file is not a zip, has no bundle.json, or cannot be
 * decoded. Callers should treat a null return as "unknown" and still show
 * the security confirmation.
 */
async function readBundleAgentIds(file: File): Promise<string[] | null> {
    const buf = await file.arrayBuffer();
    const view = new DataView(buf);
    const u8 = new Uint8Array(buf);

    // 1. Locate End-of-Central-Directory record (EOCD).
    //    Signature: 0x06054b50. The EOCD is near the end of the file, with
    //    up to 65535 bytes of comment after it. Scan backwards.
    const maxScan = Math.min(buf.byteLength, 65557);
    const scanStart = buf.byteLength - maxScan;
    let eocdOffset = -1;
    for (let i = buf.byteLength - 22; i >= scanStart; i--) {
        if (view.getUint32(i, true) === 0x06054b50) {
            eocdOffset = i;
            break;
        }
    }
    if (eocdOffset < 0) return null;

    const cdEntries = view.getUint16(eocdOffset + 10, true);
    const cdOffset = view.getUint32(eocdOffset + 16, true);

    // 2. Walk central directory looking for bundle.json.
    //    Central file header signature: 0x02014b50.
    let pos = cdOffset;
    for (let i = 0; i < cdEntries; i++) {
        if (pos + 46 > buf.byteLength) return null;
        if (view.getUint32(pos, true) !== 0x02014b50) return null;

        const compressionMethod = view.getUint16(pos + 10, true);
        const compressedSize = view.getUint32(pos + 20, true);
        const nameLen = view.getUint16(pos + 28, true);
        const extraLen = view.getUint16(pos + 30, true);
        const commentLen = view.getUint16(pos + 32, true);
        const localHeaderOffset = view.getUint32(pos + 42, true);

        const name = new TextDecoder('utf-8').decode(u8.subarray(pos + 46, pos + 46 + nameLen));

        if (name === 'bundle.json') {
            // 3. Seek to the local file header and skip its variable-length
            //    name+extra fields to find the actual data offset.
            if (localHeaderOffset + 30 > buf.byteLength) return null;
            if (view.getUint32(localHeaderOffset, true) !== 0x04034b50) return null;
            const localNameLen = view.getUint16(localHeaderOffset + 26, true);
            const localExtraLen = view.getUint16(localHeaderOffset + 28, true);
            const dataStart = localHeaderOffset + 30 + localNameLen + localExtraLen;
            const dataEnd = dataStart + compressedSize;
            if (dataEnd > buf.byteLength) return null;
            const compressed = u8.subarray(dataStart, dataEnd);

            let jsonText: string;
            if (compressionMethod === 0) {
                // Stored (no compression).
                jsonText = new TextDecoder('utf-8').decode(compressed);
            } else if (compressionMethod === 8) {
                // Deflate. Use the browser's DecompressionStream.
                const ds = new DecompressionStream('deflate-raw');
                const stream = new Blob([compressed]).stream().pipeThrough(ds);
                const inflated = await new Response(stream).arrayBuffer();
                jsonText = new TextDecoder('utf-8').decode(inflated);
            } else {
                // Unsupported compression method.
                return null;
            }

            const parsed = JSON.parse(jsonText);
            const ids = parsed?.agent_ids;
            if (Array.isArray(ids) && ids.every((x) => typeof x === 'string')) {
                return ids as string[];
            }
            return null;
        }

        pos += 46 + nameLen + extraLen + commentLen;
    }

    return null;
}
