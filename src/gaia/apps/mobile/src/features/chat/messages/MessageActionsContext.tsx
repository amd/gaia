// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext } from 'react';

/** What a bubble needs from the chat's live turn; changes only when a turn starts or ends. */
export interface MessageActionsValue {
    send(text: string): boolean;
    busy: boolean;
    /** A chat made by a retired agent: Edit, Resend and Retry with another model are withheld. */
    readOnly?: boolean;
}

const IDLE: MessageActionsValue = { send: () => false, busy: false };

export const MessageActionsContext = createContext<MessageActionsValue>(IDLE);

export function useMessageActionsContext(): MessageActionsValue {
    return useContext(MessageActionsContext);
}
