// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useRef, useState } from 'react';
import { AlertTriangle, Check, Loader2, X } from 'lucide-react';
import * as api from '../../services/api';
import type { CustomModelReport } from '../../types';

/** Paste a Hugging Face link, see what GAIA checked, and add the model to the list. */
export function AddCustomModel({ onAdded }: { onAdded: (displayName: string) => void }) {
    const [url, setUrl] = useState('');
    // A quantization's first file: unique even when two variants share a quant name.
    const [file, setFile] = useState('');
    const [report, setReport] = useState<CustomModelReport | null>(null);
    const [busy, setBusy] = useState<'check' | 'add' | null>(null);
    const [error, setError] = useState<string | null>(null);
    // The link the newest check was for; an older answer is dropped.
    const asked = useRef('');

    const check = useCallback(async (nextFile: string) => {
        const link = url.trim();
        asked.current = link;
        setBusy('check');
        setError(null);
        try {
            const r = await api.checkCustomModel(link, nextFile);
            if (asked.current !== link) return;
            setReport(r);
            setFile(r.file ?? nextFile);
        } catch (err) {
            if (asked.current !== link) return;
            setReport(null);
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(null);
        }
    }, [url]);

    const add = useCallback(async () => {
        setBusy('add');
        setError(null);
        try {
            const { report: added } = await api.addCustomModel(url.trim(), file);
            setUrl('');
            setFile('');
            setReport(null);
            onAdded(added.display_name ?? added.model_id ?? 'The model');
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(null);
        }
    }, [onAdded, file, url]);

    return (
        <form
            className="settings-field custom-model-add"
            onSubmit={(e) => { e.preventDefault(); void check(''); }}
        >
            <label className="settings-label" htmlFor="custom-model-url">Add a model from Hugging Face</label>
            <span className="settings-hint">
                Paste the link to a GGUF model. GAIA checks that it is a chat model your model server can run and
                that it fits this PC before adding it. It downloads when you choose Download.
            </span>
            <div className="settings-row">
                <input
                    id="custom-model-url"
                    className="settings-input"
                    type="url"
                    inputMode="url"
                    spellCheck={false}
                    autoComplete="off"
                    value={url}
                    readOnly={busy !== null}
                    onChange={(e) => { setUrl(e.target.value); setReport(null); setFile(''); asked.current = ''; }}
                    placeholder="https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF"
                />
                <button type="submit" className="btn-secondary" disabled={busy !== null || !url.trim()}>
                    {busy === 'check' ? <Loader2 size={13} className="spin" /> : null} Check
                </button>
            </div>
            {report && (() => {
                const list = (
                    <ul className="custom-model-checks" aria-label="Checks">
                        {report.checks.map((c) => (
                            <li key={c.id} className={c.ok ? 'is-ok' : 'is-failed'}>
                                {c.ok ? <Check size={13} aria-label="Passed" /> : <X size={13} aria-label="Failed" />}
                                <span><strong>{c.label}</strong> — {c.detail}</span>
                            </li>
                        ))}
                    </ul>
                );
                // Everything passed: one line, the details a click away.
                return report.ok ? (
                    <details className="custom-model-checks-summary">
                        <summary>
                            <Check size={13} aria-hidden="true" /> All {report.checks.length} checks passed
                        </summary>
                        {list}
                    </details>
                ) : list;
            })()}
            {report && report.warnings.length > 0 && (
                <ul className="custom-model-checks" aria-label="Warnings">
                    {report.warnings.map((w) => (
                        <li key={w} className="is-warning">
                            <AlertTriangle size={13} aria-label="Warning" /> <span>{w}</span>
                        </li>
                    ))}
                </ul>
            )}
            {report && report.variants.length > 1 && (
                <div className="settings-row">
                    <label className="settings-hint" htmlFor="custom-model-quant">Quantization</label>
                    <select
                        id="custom-model-quant"
                        className="settings-input"
                        value={file}
                        onChange={(e) => void check(e.target.value)}
                        disabled={busy !== null}
                    >
                        {report.variants.map((v) => (
                            <option key={v.file} value={v.file}>
                                {v.quant} · {v.size_gb} GB{v.fits ? '' : ' · too large for this PC'}
                            </option>
                        ))}
                    </select>
                </div>
            )}
            {report?.ok && (
                <div className="settings-row">
                    <button type="button" className="btn-primary" onClick={() => void add()} disabled={busy !== null}>
                        {busy === 'add' ? <Loader2 size={13} className="spin" /> : null} Add {report.display_name}
                    </button>
                    <span className="settings-hint">{report.size_gb} GB download</span>
                </div>
            )}
            {error && <p className="settings-error" role="alert">{error}</p>}
        </form>
    );
}
