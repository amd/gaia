// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import { Row } from '@/ui';

type Register = (content: ReactNode) => void;

// Two contexts, so a step that registers its actions never re-renders from its own registration.
const Setter = createContext<Register | undefined>(undefined);
const Content = createContext<ReactNode>(null);

/** Owns the bar at the bottom of setup; the screen docks `<SetupFooterBar />` in its footer. */
export function SetupFooterProvider({ children }: { children: ReactNode }) {
    const [content, setContent] = useState<ReactNode>(null);
    return (
        <Setter.Provider value={setContent}>
            <Content.Provider value={content}>{children}</Content.Provider>
        </Setter.Provider>
    );
}

/** The bar itself: Back at the start and the step's main action at the end; empty until a step fills it. */
export function SetupFooterBar() {
    const content = useContext(Content);
    if (content == null) return null;
    return (
        <Row gap="md" justify="between">
            {content}
        </Row>
    );
}

/**
 * A step's actions, in the same place on every step. Outside setup nothing receives them,
 * so they render in place, as the web UI's `SetupFooter` does for Settings.
 */
export function SetupFooter({ children }: { children: ReactNode }) {
    const register = useContext(Setter);
    useEffect(() => {
        if (!register) return undefined;
        register(children);
        return () => register(null);
    }, [register, children]);
    if (!register) return <Row gap="md" justify="between">{children}</Row>;
    return null;
}

const discard: Register = () => undefined;

/** While `muted`, the actions of the step inside go nowhere: for a step kept mounted under another screen. */
export function SetupFooterMuted({ muted, children }: { muted: boolean; children: ReactNode }) {
    const register = useContext(Setter);
    return <Setter.Provider value={muted ? discard : register}>{children}</Setter.Provider>;
}
