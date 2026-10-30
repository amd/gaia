// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

interface SwitchProps {
    checked: boolean;
    onChange: (checked: boolean) => void;
    /** Accessible name. */
    label: string;
    /** Visible state text beside the switch. */
    text?: string;
    disabled?: boolean;
}

/** An on/off switch that reads as one to a screen reader. */
export function Switch({ checked, onChange, label, text, disabled }: SwitchProps) {
    return (
        <span className="sp-switch-wrap">
            <button
                type="button"
                role="switch"
                aria-checked={checked}
                aria-label={label}
                className={`sp-switch${checked ? ' is-on' : ''}`}
                onClick={() => onChange(!checked)}
                disabled={disabled}
            >
                <span className="sp-switch-knob" aria-hidden="true" />
            </button>
            {text && <span className="sp-state-text">{text}</span>}
        </span>
    );
}
