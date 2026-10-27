// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Eye, EyeOff } from 'lucide-react';

/** The chat header's Private switch. The label says what a click will do, so it carries no aria-pressed. */
export function PrivateChatButton({ isPrivate, onToggle }: { isPrivate: boolean; onToggle: () => void }) {
    return (
        <button
            className="btn-icon-sm task-header-private"
            onClick={onToggle}
            title={isPrivate
                ? "Turn off private chat. This chat isn't saved to memory."
                : "Turn on private chat. It won't be saved to memory."}
            aria-label={isPrivate ? 'Turn off private chat' : 'Turn on private chat'}
            data-private={isPrivate ? 'true' : undefined}
        >
            {isPrivate ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
    );
}
