import React from 'react';
import './ProgressStrip.css';

interface Props {
    label?: string;
    detail?: string;
    latencyMs?: number;
    onCancel?: () => void;
    active?: boolean;
}

export default function ProgressStrip({ label, detail, latencyMs, onCancel, active }: Props) {
    return (
        <div className={`progress-strip ${active ? 'active' : 'idle'}`} role="region" aria-live="polite">
            <div className="progress-main">
                <div className="progress-label">{label || 'Working'}</div>
                {detail && <div className="progress-detail">{detail}</div>}
            </div>
            <div className="progress-meta">
                {typeof latencyMs === 'number' && <div className="progress-latency">{latencyMs} ms</div>}
                {onCancel && (
                    <button className="btn-icon progress-cancel" onClick={onCancel} aria-label="Cancel">
                        Cancel
                    </button>
                )}
            </div>
        </div>
    );
}
