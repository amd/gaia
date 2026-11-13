// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Eye, EyeOff } from 'lucide-react';
import { useT } from '../i18n/useT';

/** The chat header's Private switch. The label says what a click will do, so it carries no aria-pressed. */
export function PrivateChatButton({ isPrivate, onToggle }: { isPrivate: boolean; onToggle: () => void }) {
    const t = useT();
    return (
        <button
            className="btn-icon-sm task-header-private"
            onClick={onToggle}
            title={t(isPrivate ? 'chat.privateOffTitle' : 'composer.privateOnTitle')}
            aria-label={t(isPrivate ? 'composer.privateOff' : 'composer.privateOn')}
            data-private={isPrivate ? 'true' : undefined}
        >
            {isPrivate ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
    );
}
