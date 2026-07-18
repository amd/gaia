// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { ArrowLeft, ArrowRight } from 'lucide-react';
import { useOnboarding, ONBOARDING_STEPS } from './useOnboarding';
import { WelcomeStep } from './WelcomeStep';
import { PreflightStep } from './PreflightStep';
import { ModelDownloadStep } from './ModelDownloadStep';
import { ConnectorStep } from './ConnectorStep';
import { DoneStep } from './DoneStep';
import * as api from '../../services/api';
import { log } from '../../utils/logger';
import { DEFAULT_MODEL_NAME } from '../../utils/constants';
import type { PreflightReport } from '../../types';
import './OnboardingWizard.css';

interface OnboardingWizardProps {
    /** Called once the ``initialized`` marker is written (wizard should unmount). */
    onComplete: () => void;
}

/**
 * First-run onboarding wizard (#1726, #1727): hardware pre-flight → in-app
 * model download → optional connector → done. Writes the ``initialized`` marker
 * on finish/skip so it never re-triggers.
 */
export function OnboardingWizard({ onComplete }: OnboardingWizardProps) {
    const nav = useOnboarding();
    const [report, setReport] = useState<PreflightReport | null>(null);
    const [finishing, setFinishing] = useState(false);
    const [finishError, setFinishError] = useState<string | null>(null);

    const recommendedModel = report?.recommended_model || DEFAULT_MODEL_NAME;

    const finish = useCallback(async (skipped: boolean) => {
        setFinishing(true);
        setFinishError(null);
        try {
            await api.completeOnboarding(skipped);
            log.system.info(`Onboarding complete (skipped=${skipped})`);
            onComplete();
        } catch (err) {
            // No silent fallback — if we can't persist the marker, say so and
            // let the user retry rather than dropping them into a half-set-up
            // app that re-shows the wizard next launch.
            const message = err instanceof Error ? err.message : 'Failed to finish setup.';
            log.system.error('Onboarding: failed to write completion marker', err);
            setFinishError(message);
            setFinishing(false);
        }
    }, [onComplete]);

    const renderStep = () => {
        switch (nav.step) {
            case 'welcome':
                return <WelcomeStep />;
            case 'preflight':
                return <PreflightStep onGuardChange={nav.setGuard} onReport={setReport} />;
            case 'download':
                return <ModelDownloadStep modelName={recommendedModel} onGuardChange={nav.setGuard} />;
            case 'connector':
                return <ConnectorStep />;
            case 'done':
                return <DoneStep />;
        }
    };

    // Footer actions differ per step; keep the logic here so steps stay purely
    // presentational and the guard rules live in one place.
    const renderFooter = () => {
        if (nav.step === 'welcome') {
            return (
                <>
                    <button className="onboarding-btn link" onClick={() => finish(true)} disabled={finishing}>
                        Skip — I know what I'm doing
                    </button>
                    <div className="spacer" />
                    <button className="onboarding-btn primary" onClick={nav.next}>
                        Get started <ArrowRight size={15} />
                    </button>
                </>
            );
        }

        if (nav.step === 'done') {
            return (
                <>
                    <div className="spacer" />
                    <button className="onboarding-btn primary" onClick={() => finish(false)} disabled={finishing}>
                        {finishing ? 'Finishing…' : 'Start using GAIA'}
                    </button>
                </>
            );
        }

        // Middle steps: Back + Next, with an optional "later" escape on the
        // optional/guarded steps so a guard never traps the user.
        const showSkipLater = nav.step === 'download' || nav.step === 'connector';
        return (
            <>
                <button className="onboarding-btn secondary" onClick={nav.back} disabled={nav.isFirst}>
                    <ArrowLeft size={15} /> Back
                </button>
                {showSkipLater && (
                    <button className="onboarding-btn link" onClick={nav.skip} data-testid="onboarding-skip-later">
                        Set up later
                    </button>
                )}
                <div className="spacer" />
                <button
                    className="onboarding-btn primary"
                    onClick={nav.next}
                    disabled={nav.guarded}
                    data-testid="onboarding-next"
                >
                    Continue <ArrowRight size={15} />
                </button>
            </>
        );
    };

    return (
        <div className="onboarding-overlay" role="dialog" aria-modal="true" aria-label="GAIA first-run setup">
            <div className="onboarding-card">
                <div className="onboarding-progress" aria-hidden="true">
                    {ONBOARDING_STEPS.map((s, i) => (
                        <span
                            key={s}
                            className={`dot ${i === nav.index ? 'active' : ''} ${i < nav.index ? 'done' : ''}`}
                        />
                    ))}
                </div>

                {renderStep()}

                {finishError && (
                    <div className="onboarding-banner error" role="alert" style={{ margin: '0 28px' }}>
                        {finishError}
                    </div>
                )}

                <div className="onboarding-footer">
                    {renderFooter()}
                </div>
            </div>
        </div>
    );
}
