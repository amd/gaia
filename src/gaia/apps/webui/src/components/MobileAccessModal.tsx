// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useEffect, useRef, useCallback } from 'react';
import { X, Smartphone, Copy, Check, ExternalLink, RotateCw } from 'lucide-react';
import type { TunnelStatus } from '../types';
import * as api from '../services/api';
import { useEscapeToClose } from '../hooks/useEscapeToClose';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { log } from '../utils/logger';
import { useT } from '../i18n/useT';
import './MobileAccessModal.css';

// QRCode loaded lazily via dynamic import
let QRCodeLib: any = null;

interface MobileAccessModalProps {
    isOpen: boolean;
    onClose: () => void;
    /** Explicitly stop the tunnel (shown only while the tunnel is active). */
    onStop?: () => void;
    error?: string | null;
    /** Set when ngrok is not installed: this OS's install commands (may be empty). */
    setupCommands?: string[] | null;
    /** Try starting the tunnel again, e.g. after installing ngrok. */
    onRetry?: () => void;
}

const NGROK_DOWNLOAD_URL = 'https://ngrok.com/download';

export function MobileAccessModal({ isOpen, onClose, onStop, error, setupCommands, onRetry }: MobileAccessModalProps) {
    const [status, setStatus] = useState<TunnelStatus | null>(null);
    const [copied, setCopied] = useState(false);
    const [copiedCommand, setCopiedCommand] = useState<string | null>(null);
    const t = useT();
    const needsSetup = Array.isArray(setupCommands);
    // Nothing to poll for or scan while the tunnel cannot start.
    const halted = !!error || needsSetup;
    const [qrError, setQrError] = useState<string | null>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    useEscapeToClose(isOpen, onClose);
    const dialogRef = useRef<HTMLDivElement>(null);
    useFocusTrap(dialogRef, isOpen);

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
        if (!isOpen || halted) return;

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
    }, [isOpen, halted, fetchStatus]);

    // Generate QR code when URL is available
    useEffect(() => {
        if (!status?.active || !status.url || !status.token || !canvasRef.current) return;

        const mobileUrl = `${status.url}/?token=${status.token}`;
        log.system.info(`Generating QR code for: ${mobileUrl}`);

        // Load QRCode dynamically if not loaded
        const generateQR = async () => {
            setQrError(null);
            if (!QRCodeLib) {
                try {
                    const mod = await import('qrcode');
                    QRCodeLib = mod.default || mod;
                } catch {
                    log.system.error('QR code library not available - install with: npm install qrcode');
                    setQrError('QR code unavailable on this build. Use the link below instead.');
                    return;
                }
            }

            // toCanvas() paints a raster, so it needs literals, not var().
            // getComputedStyle resolves them for the theme in force — copying
            // the hexes here is how the two surfaces drift apart.
            const css = getComputedStyle(document.documentElement);
            const ink = css.getPropertyValue('--text-primary').trim();
            const paper = css.getPropertyValue('--bg-primary').trim();
            if (!ink || !paper) {
                log.system.error(
                    'QR code skipped: --text-primary/--bg-primary did not resolve. ' +
                        'styles/index.css must be loaded before this modal renders.',
                );
                setQrError('QR code unavailable — the theme did not load. Use the link below instead.');
                return;
            }

            try {
                await QRCodeLib.toCanvas(canvasRef.current, mobileUrl, {
                    width: 200,
                    margin: 2,
                    // A QR scanner needs a hard two-tone raster: ink on paper.
                    color: { dark: ink, light: paper },
                });
            } catch (err) {
                log.system.error('QR code generation failed', err);
                setQrError('QR code could not be drawn. Use the link below instead.');
            }
        };

        generateQR();
    }, [status?.active, status?.url, status?.token]);

    // Copy URL to clipboard
    const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const copyUrl = useCallback(() => {
        if (!status?.url || !status?.token) return;
        const mobileUrl = `${status.url}/?token=${status.token}`;
        navigator.clipboard.writeText(mobileUrl).then(() => {
            setCopied(true);
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
            copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
        });
    }, [status]);

    const copyCommand = useCallback((command: string) => {
        navigator.clipboard.writeText(command).then(() => {
            setCopiedCommand(command);
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
            copyTimerRef.current = setTimeout(() => setCopiedCommand(null), 2000);
        }).catch((err) => log.system.error('Could not copy the ngrok install command', err));
    }, []);

    // Clean up copy timer on unmount
    useEffect(() => {
        return () => {
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        };
    }, []);

    if (!isOpen) return null;

    const mobileUrl = status?.active && status?.url && status?.token
        ? `${status.url}/?token=${status.token}`
        : '';

    return (
        <div ref={dialogRef} className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Mobile access">
            <div className="modal-panel mobile-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                    <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <Smartphone size={18} />
                        Mobile access
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
                            needsSetup ? 'setup' :
                            error ? 'error' :
                            'starting'
                        }`} />
                        <span>
                            {status?.active
                                ? 'Tunnel active'
                                : needsSetup
                                    ? t('mobile.ngrokRequired')
                                    : error
                                        ? "Couldn't start mobile access"
                                        : 'Starting tunnel...'}
                        </span>
                        {!status?.active && !halted && <span className="tunnel-spinner" />}
                    </div>

                    {/* Missing prerequisite: a setup step, not a failure. */}
                    {needsSetup && (
                        <div className="tunnel-setup">
                            <div className="tunnel-setup-title">{t('mobile.setupTitle')}</div>
                            <p>
                                {t('mobile.setupIntro')}{' '}
                                {setupCommands.length > 0 ? t('mobile.setupInstall') : t('mobile.setupDownload')}
                            </p>
                            {setupCommands.map((command) => (
                                <div className="tunnel-url-row tunnel-setup-command" key={command}>
                                    <input type="text" readOnly dir="ltr" value={command} aria-label={t('mobile.installCommand')} />
                                    <button
                                        className={`copy-url-btn ${copiedCommand === command ? 'copied' : ''}`}
                                        onClick={() => copyCommand(command)}
                                        title={t('mobile.copyCommand')}
                                    >
                                        {copiedCommand === command ? <Check size={13} /> : <Copy size={13} />}
                                        {copiedCommand === command ? t('mobile.copied') : t('mobile.copy')}
                                    </button>
                                </div>
                            ))}
                            <a href={NGROK_DOWNLOAD_URL} target="_blank" rel="noopener noreferrer" className="tunnel-setup-link">
                                {t('mobile.downloadPage')} <ExternalLink size={11} />
                            </a>
                        </div>
                    )}

                    {/* Error message */}
                    {error && !needsSetup && (
                        <div className="tunnel-error">
                            {error}
                        </div>
                    )}

                    {/* QR Code: nothing to scan, and no steps to follow, once it failed. */}
                    {!halted && (
                        <div className="qr-code-area">
                            {status?.active ? (
                                <>
                                    {/* Stays mounted while errored so a later retry still has its ref. */}
                                    <canvas ref={canvasRef} hidden={!!qrError} />
                                    {qrError && <div className="qr-placeholder error">{qrError}</div>}
                                </>
                            ) : (
                                <div className="qr-placeholder">
                                    <div className="placeholder-spinner" />
                                    <span>Connecting...</span>
                                </div>
                            )}
                        </div>
                    )}

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
                            <label>Tunnel password</label>
                            <div className="tunnel-password-value">{status.publicIp}</div>
                            <span className="tunnel-password-note">
                                Enter this when the tunnel page asks for a password
                            </span>
                        </div>
                    )}

                    {/* Instructions */}
                    {!halted && (
                        <div className="mobile-instructions">
                            <ol>
                                <li>
                                    {qrError
                                        ? 'Open the mobile URL above on your phone'
                                        : "Scan the QR code with your phone's camera"}
                                </li>
                                {status?.url && !status.url.includes('ngrok-free.app') && (
                                    <li>Enter the tunnel password shown above when prompted</li>
                                )}
                                <li>Chat with GAIA from your mobile device</li>
                                <li>Your data stays secure via encrypted tunnel</li>
                            </ol>
                        </div>
                    )}

                    {status?.active && !error && (
                        <div className="mobile-install-hint">
                            <div className="mobile-install-hint-title">{t('mobileAccess.installTitle')}</div>
                            <p>{t('mobileAccess.installSteps')}</p>
                            <p>{t('mobileAccess.linkLifetime')}</p>
                        </div>
                    )}

                    {/* Actions */}
                    <div className="mobile-access-actions">
                        {status?.active && onStop && (
                            <button
                                className="btn-danger"
                                onClick={onStop}
                                title="Close the tunnel and revoke the mobile URL"
                            >
                                Stop tunnel
                            </button>
                        )}
                        {needsSetup && onRetry && (
                            <button className="btn-primary" onClick={onRetry}>
                                <RotateCw size={13} /> {t('mobile.retry')}
                            </button>
                        )}
                        <button className="btn-secondary" onClick={onClose}>Close</button>
                    </div>
                </div>
            </div>
        </div>
    );
}
