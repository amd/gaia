// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useNotificationStore } from '../stores/notificationStore';
import { buildTrayActivity, type TrayActivityInput } from '../utils/trayActivity';
import { log } from '../utils/logger';
import type { TrayCommand } from '../types/agent';

interface TrayBridgeActions {
    openChat: (sessionId: string) => void;
    newChat: () => void;
    refreshStatus: () => void;
}

/**
 * Keep the desktop tray in step with this window, and run its menu actions.
 * A no-op outside Electron. Sends only when what the tray shows changes.
 */
export function useTrayBridge(actions: TrayBridgeActions): void {
    const actionsRef = useRef(actions);
    actionsRef.current = actions;

    useEffect(() => {
        const tray = window.gaiaAPI?.tray;
        if (!tray) return;

        let last = '';
        let lastInput: TrayActivityInput | null = null;
        const push = () => {
            const chat = useChatStore.getState();
            const input: TrayActivityInput = {
                backendConnected: chat.backendConnected,
                systemStatus: chat.systemStatus,
                sessions: chat.sessions,
                runningSessionIds: chat.runningSessionIds,
                awaitingBySession: chat.awaitingBySession,
                currentSessionId: chat.currentSessionId,
                isStreaming: chat.isStreaming,
                awaitingUser: chat.awaitingUser,
                liveStatus: chat.liveStatus,
                livePhase: chat.livePhase,
                notifications: useNotificationStore.getState().notifications,
            };
            // Streamed text changes the store every frame; skip when nothing the tray reads moved.
            const prev = lastInput;
            lastInput = input;
            if (prev && (Object.keys(input) as Array<keyof TrayActivityInput>).every((k) => input[k] === prev[k])) return;
            const activity = buildTrayActivity(input);
            const key = JSON.stringify(activity);
            if (key === last) return;
            last = key;
            tray.setActivity(activity).catch((err) => {
                last = '';
                log.system.error('The tray could not take the app state', err);
            });
        };

        push();
        const unsubChat = useChatStore.subscribe(push);
        const unsubNotifications = useNotificationStore.subscribe(push);
        const unsubCommands = tray.onCommand((command: TrayCommand) => {
            switch (command.type) {
                case 'open-chat':
                    actionsRef.current.openChat(command.sessionId);
                    break;
                case 'new-chat':
                    actionsRef.current.newChat();
                    break;
                case 'open-settings':
                    useChatStore.getState().openSettings(command.section);
                    break;
                case 'refresh-status':
                    actionsRef.current.refreshStatus();
                    break;
                default:
                    log.system.error('Unknown tray command', command);
            }
        });
        return () => {
            unsubChat();
            unsubNotifications();
            unsubCommands();
        };
    }, []);
}
