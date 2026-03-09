// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useEffect, useRef, useCallback } from 'react';
import { X, Smartphone, Copy, Check } from 'lucide-react';
import type { TunnelStatus } from '../types';
import * as api from '../services/api';
import { log } from '../utils/logger';
import './MobileAccessModal.css';

// QRCode loaded lazily via dynamic import
let QRCodeLib: any = null;

interface MobileAccessModalProps {
    isOpen: boolean;
    onClose: () => void;
    error?: string | null;
}

export function MobileAccessModal({ isOpen, onClose, error }: MobileAccessModalProps) {
    const [status, setStatus] = useState<TunnelStatus | null>(null);
    const [copied, setCopied] = useState(false);
    const canvasRef = useRef<HTMLCanvasElement>(null);

    // Fetch current tunnel status using centralized API client
    const fetchStatus = useCallback(async () => {
        try {
            const data = await api.getTunnelStatus();
            setStatus(data);
            return data;
        } catch (err) {
            log.system.error('Failed to fetch tunnel status', err);
            return null;
        }
    }, []);

    // Poll for tunnel status when modal is open
    useEffect(() => {
        if (!isOpen || error) return;

        // Fetch immediately
        fetchStatus();

        // Poll every 2s until tunnel is active
        const interval = setInterval(async () => {
            const data = await fetchStatus();
            if (data?.active && data?.url) {
                clearInterval(interval);
            }
        }, 2000);

        return () => clearInterval(interval);
    }, [isOpen, error, fetchStatus]);

    // Generate QR code when URL is available
    useEffect(() => {
        if (!status?.active || !status.url || !status.token || !canvasRef.current) return;

        const mobileUrl = `${status.url}/?token=${status.token}`;
        log.system.info(`Generating QR code for: ${mobileUrl}`);

        // Load QRCode dynamically if not loaded
        const generateQR = async () => {
            if (!QRCodeLib) {
                try {
                    const mod = await import('qrcode');
                    QRCodeLib = mod.default || mod;
                } catch {
                    log.system.error('QR code library not available - install with: npm install qrcode');
                    return;
                }
            }

            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

            try {
                await QRCodeLib.toCanvas(canvasRef.current, mobileUrl, {
                    width: 200,
                    margin: 2,
                    color: {
                        dark: isDark ? '#e6edf3' : '#111827',
                        light: isDark ? '#0d0d0d' : '#ffffff',
                    },
                });
            } catch (err) {
                log.system.error('QR code generation failed', err);
            }
        };

        generateQR();
    }, [status?.active, status?.url, status?.token]);

    // Copy URL to clipboard
    const copyUrl = useCallback(() => {
        if (!status?.url || !status?.token) return;
        const mobileUrl = `${status.url}/?token=${status.token}`;
        navigator.clipboard.writeText(mobileUrl).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        });
    }, [status]);

    if (!isOpen) return null;

    const mobileUrl = status?.active && status?.url && status?.token
        ? `${status.url}/?token=${status.token}`
        : '';

    return (
        <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Mobile Access">
            <div className="modal-panel mobile-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <Smartphone size={18} />
                        Mobile Access
                    </h3>
                    <button className="btn-icon" onClick={onClose} aria-label="Close">
                        <X size={18} />
                    </button>
                </div>

                <div className="modal-body mobile-modal-body">
                    {/* Status indicator */}
                    <div className="tunnel-status">
                        <span className={`status-dot ${
                            status?.active ? 'active' :
                            error ? 'error' :
                            'starting'
                        }`} />
                        <span>
                            {status?.active
                                ? 'Tunnel active'
                                : error
                                    ? 'Connection failed'
                                    : 'Starting tunnel...'}
                        </span>
                        {!status?.active && !error && <span className="tunnel-spinner" />}
                    </div>

                    {/* Error message */}
                    {error && (
                        <div className="tunnel-error">
                            {error}
                        </div>
                    )}

                    {/* QR Code */}
                    <div className="qr-code-area">
                        {status?.active ? (
                            <canvas ref={canvasRef} />
                        ) : error ? (
                            <div className="qr-placeholder error">
                                Failed to connect
                            </div>
                        ) : (
                            <div className="qr-placeholder">
                                <div className="placeholder-spinner" />
                                <span>Connecting...</span>
                            </div>
                        )}
                    </div>

                    {/* URL display */}
                    {status?.active && mobileUrl && (
                        <div className="tunnel-url-area">
                            <label>Mobile URL</label>
                            <div className="tunnel-url-row">
                                <input type="text" readOnly value={mobileUrl} />
                                <button
                                    className={`copy-url-btn ${copied ? 'copied' : ''}`}
                                    onClick={copyUrl}
                                    title="Copy URL"
                                >
                                    {copied ? <Check size={13} /> : <Copy size={13} />}
                                    {copied ? 'Copied' : 'Copy'}
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Tunnel password hint (for random ngrok URLs with interstitial) */}
                    {status?.active && status?.publicIp && status?.url &&
                     !status.url.includes('ngrok-free.app') && (
                        <div className="tunnel-password-hint">
                            <label>Tunnel Password</label>
                            <div className="tunnel-password-value">{status.publicIp}</div>
                            <span className="tunnel-password-note">
                                Enter this when the tunnel page asks for a password
                            </span>
                        </div>
                    )}

                    {/* Instructions */}
                    <div className="mobile-instructions">
                        <ol>
                            <li>Scan the QR code with your phone&apos;s camera</li>
                            {status?.url && !status.url.includes('ngrok-free.app') && (
                                <li>Enter the tunnel password shown above when prompted</li>
                            )}
                            <li>Chat with GAIA from your mobile device</li>
                            <li>Your data stays secure via encrypted tunnel</li>
                        </ol>
                    </div>

                    {/* Actions */}
                    <div className="mobile-access-actions">
                        <button className="btn-secondary" onClick={onClose}>Close</button>
                    </div>
                </div>
            </div>
        </div>
    );
}
