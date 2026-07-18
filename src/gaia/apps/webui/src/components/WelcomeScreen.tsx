// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useEffect, useRef, useMemo } from 'react';
import { Lock, Zap, FileText, DollarSign, Terminal } from 'lucide-react';
import { useChatStore } from '../stores/chatStore';
import { AgentHubGrid } from './AgentHubGrid';
import { shouldShowFirstRunTip, shouldShowNoModelTip } from '../utils/setupHints';
import './WelcomeScreen.css';

interface WelcomeScreenProps {
    onNewTask: () => void;
    onSendPrompt: (prompt: string) => void;
    onCreateAgent?: () => void;
}

const TITLE_TEXT = 'GAIA';
const SUBTITLE_TEXT = 'Your private AI assistant, running 100% locally on AMD Ryzen AI';
const TITLE_SPEED = 65; // ms per character
const TITLE_SUBTITLE_PAUSE = 350; // ms pause between title and subtitle

/**
 * Generate a randomized "hacker typing" delay for the next character.
 * Simulates organic keystroke rhythm with bursts, pauses, and stutters.
 */
function hackerDelay(char: string, prevChar: string): number {
    // Pause after punctuation — thinking moment
    if (prevChar === ',' || prevChar === '.') return 60 + Math.random() * 50;
    // Brief pause after spaces — word boundary
    if (prevChar === ' ') return 15 + Math.random() * 25;
    // Fast burst for common bigrams / mid-word flow
    if (Math.random() < 0.35) return 8 + Math.random() * 12;
    // Occasional micro-stutter — hesitation
    if (Math.random() < 0.06) return 45 + Math.random() * 35;
    // Normal speed with jitter
    return 18 + Math.random() * 22;
}

const DEFAULT_SUGGESTIONS = [
    'Scan my Downloads and tell me what I should clean up',
    'Index a folder of documents so I can chat about them',
    'What have I been working on lately? Show my recent files',
    'What hardware is in my PC? Tell me about my CPU and GPU',
];

export function WelcomeScreen({ onNewTask, onSendPrompt, onCreateAgent }: WelcomeScreenProps) {
    const { systemStatus, agents, activeAgentId, setActiveAgentId } = useChatStore();

    const suggestions = useMemo(() => {
        const active = agents.find((a) => a.id === activeAgentId);
        if (active?.conversation_starters?.length) return active.conversation_starters;
        return DEFAULT_SUGGESTIONS;
    }, [agents, activeAgentId]);
    const [displayedText, setDisplayedText] = useState('');
    const [typingComplete, setTypingComplete] = useState(false);
    const [subtitleText, setSubtitleText] = useState('');
    const [subtitleComplete, setSubtitleComplete] = useState(false);
    const [phase, setPhase] = useState<'title' | 'subtitle' | 'done'>('title');
    const [showContent, setShowContent] = useState(false);

    // Determine if a setup hint should be shown to guide first-time users.
    // Gating lives in setupHints so it stays testable and consistent — the
    // "First time?" tip is suppressed once the live probe shows the system is
    // ready even if the initialized marker is missing (#2119).
    const isInitializing = systemStatus?.init_state === 'initializing';
    const notInitialized = shouldShowFirstRunTip(systemStatus);
    const noModel = shouldShowNoModelTip(systemStatus);

    // Title typing effect
    useEffect(() => {
        let charIndex = 0;
        const interval = setInterval(() => {
            charIndex++;
            if (charIndex <= TITLE_TEXT.length) {
                setDisplayedText(TITLE_TEXT.slice(0, charIndex));
            } else {
                clearInterval(interval);
                setTypingComplete(true);
            }
        }, TITLE_SPEED);

        return () => clearInterval(interval);
    }, []);

    // After title completes, pause then start subtitle with hacker-style timing
    useEffect(() => {
        if (!typingComplete) return;
        let cancelled = false;

        const pauseTimer = setTimeout(() => {
            if (cancelled) return;
            setPhase('subtitle');

            // Use recursive setTimeout for variable per-character delay
            let charIndex = 0;
            const typeNext = () => {
                if (cancelled) return;
                charIndex++;
                if (charIndex <= SUBTITLE_TEXT.length) {
                    setSubtitleText(SUBTITLE_TEXT.slice(0, charIndex));
                    const char = SUBTITLE_TEXT[charIndex - 1];
                    const prev = charIndex > 1 ? SUBTITLE_TEXT[charIndex - 2] : '';
                    const delay = hackerDelay(char, prev);
                    timerRef.current = setTimeout(typeNext, delay);
                } else {
                    setSubtitleComplete(true);
                    setPhase('done');
                }
            };
            typeNext();
        }, TITLE_SUBTITLE_PAUSE);

        const timerRef = { current: null as ReturnType<typeof setTimeout> | null };
        return () => {
            cancelled = true;
            clearTimeout(pauseTimer);
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [typingComplete]);

    // After subtitle completes, reveal remaining content
    useEffect(() => {
        if (!subtitleComplete) return;
        const timer = setTimeout(() => setShowContent(true), 200);
        return () => clearTimeout(timer);
    }, [subtitleComplete]);

    return (
        <main className="welcome">
            <div className={`welcome-inner ${showContent ? 'content-revealed' : ''}`}>
                <h1 className={`welcome-title${typingComplete ? ' typing-done' : ''}`}>
                    {displayedText.length >= 4 ? (
                        <><span className="gaia-glow">{displayedText.slice(0, 4)}</span><span>{displayedText.slice(4)}</span></>
                    ) : displayedText}
                    {phase === 'title' && (
                        <span className={`terminal-cursor${typingComplete ? ' blink' : ''}`} />
                    )}
                </h1>
                <p className="welcome-sub">
                    <span className="typewriter-text">
                        {subtitleText}
                        {(phase === 'subtitle' || phase === 'done') && (
                            <span className={`terminal-cursor terminal-cursor-sub${phase === 'done' ? ' blink' : ''}`} />
                        )}
                    </span>
                </p>
                <span className="welcome-version">v{__APP_VERSION__} <span className="beta-badge">BETA</span></span>

                <div className="features">
                    <Feature icon={<Lock size={22} />} title="Private" desc="Data stays on your device"
                        codeHint="> encrypt --local"
                        expandedDesc="All processing happens on-device. No cloud, no tracking, complete data privacy." />
                    <Feature icon={<Zap size={22} />} title="Fast" desc="NPU acceleration"
                        codeHint="> npu.accelerate()"
                        expandedDesc="Hardware-accelerated with AMD Ryzen AI NPU for real-time local inference." />
                    <Feature icon={<FileText size={22} />} title="Smart" desc="Document Q&A"
                        codeHint='> rag.query("...")'
                        expandedDesc="RAG-powered document Q&A — index files and chat with their contents." />
                    <Feature icon={<DollarSign size={22} />} title="Free" desc="No subscriptions"
                        codeHint="> license: MIT"
                        expandedDesc="No API keys, no subscriptions, no hidden costs. Fully open-source." />
                </div>

                {agents.length > 0 && (
                    <AgentHubGrid
                        agents={agents}
                        activeAgentId={activeAgentId}
                        onSelect={setActiveAgentId}
                        onStartChat={(id, prompt) => {
                            setActiveAgentId(id);
                            if (prompt) onSendPrompt(prompt);
                            else onNewTask();
                        }}
                        onCreateAgent={onCreateAgent}
                    />
                )}

                {/* First-run setup hints. Size hint is sourced from the
                    catalog (``default_model_size_gb``) so it tracks the
                    current default model — the previous hard-coded
                    "~25 GB" was a stale remnant from when the default was
                    Qwen3.5-35B. */}
                {notInitialized && (
                    <div className="welcome-setup-hint">
                        <Terminal size={14} />
                        <span>
                            <strong>First time?</strong> Run <code>gaia init --profile chat</code> in a terminal to
                            install Lemonade Server and download the required AI model
                            {systemStatus?.default_model_size_gb
                                ? ` (~${systemStatus.default_model_size_gb.toFixed(1)} GB).`
                                : '.'}
                        </span>
                    </div>
                )}
                {!notInitialized && noModel && (
                    <div className="welcome-setup-hint">
                        <Terminal size={14} />
                        <span>
                            No model loaded. Run <code>gaia init --profile chat</code> to download
                            {systemStatus?.default_model_size_gb
                                ? ` (~${systemStatus.default_model_size_gb.toFixed(1)} GB).`
                                : '.'}
                        </span>
                    </div>
                )}

                <div className="welcome-actions">
                    <button className="btn-primary start-btn" onClick={onNewTask} disabled={isInitializing}>
                        Start a New Task
                    </button>
                </div>

                <div className="suggestions">
                    <span className="suggestions-label">Try asking:</span>
                    <div className="suggestion-chips">
                        {suggestions.map((s) => (
                            <button key={s} className="chip" onClick={() => onSendPrompt(s)} disabled={isInitializing}>
                                {s}
                            </button>
                        ))}
                    </div>
                </div>

                <p className="welcome-copyright">© 2025–2026 Advanced Micro Devices, Inc. All rights reserved.</p>
            </div>
        </main>
    );
}

function Feature({ icon, title, desc, expandedDesc, codeHint }: {
    icon: React.ReactNode; title: string; desc: string; expandedDesc: string; codeHint: string;
}) {
    const [phase, setPhase] = useState<'idle' | 'erasing' | 'typing' | 'done'>('idle');
    const [eraseText, setEraseText] = useState(codeHint);
    const [hoverText, setHoverText] = useState('');
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const handleMouseEnter = () => {
        // Phase 1: erase the code hint character by character (fast, right-to-left)
        setPhase('erasing');
        setHoverText('');
        let remaining = codeHint.length;
        const eraseNext = () => {
            remaining--;
            if (remaining >= 0) {
                setEraseText(codeHint.slice(0, remaining));
                timerRef.current = setTimeout(eraseNext, 15 + Math.random() * 20);
            } else {
                // Phase 2: start typing the expanded description
                setPhase('typing');
                setEraseText('');
                let i = 0;
                const typeNext = () => {
                    i++;
                    if (i <= expandedDesc.length) {
                        setHoverText(expandedDesc.slice(0, i));
                        const char = expandedDesc[i - 1];
                        const prev = i > 1 ? expandedDesc[i - 2] : '';
                        timerRef.current = setTimeout(typeNext, hackerDelay(char, prev));
                    } else {
                        setPhase('done');
                    }
                };
                typeNext();
            }
        };
        eraseNext();
    };

    const handleMouseLeave = () => {
        if (timerRef.current) clearTimeout(timerRef.current);
        setPhase('idle');
        setEraseText(codeHint);
        setHoverText('');
    };

    // Cleanup on unmount
    useEffect(() => {
        return () => { if (timerRef.current) clearTimeout(timerRef.current); };
    }, []);

    const isActive = phase !== 'idle';

    return (
        <div className={`feature-card ${isActive ? 'feature-hovered' : ''}`}
             onMouseEnter={handleMouseEnter}
             onMouseLeave={handleMouseLeave}>
            <div className="feature-icon">{icon}</div>
            <h3>{title}</h3>
            <p>{desc}</p>
            <div className="feature-terminal">
                {phase === 'idle' && (
                    <span className="feature-code-hint">{codeHint}</span>
                )}
                {phase === 'erasing' && (
                    <span className="feature-inline"><span className="feature-code-hint feature-code-erasing">{eraseText}</span><span className="terminal-cursor terminal-cursor-sm" /></span>
                )}
                {(phase === 'typing' || phase === 'done') && (
                    <span className="feature-inline"><span className="feature-expanded-text">{hoverText}</span><span className={`terminal-cursor terminal-cursor-sm${phase === 'done' ? ' blink' : ''}`} /></span>
                )}
            </div>
        </div>
    );
}
