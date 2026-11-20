// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Paired phones and tablets, as the PC sees them: shared by Settings, the sidebar and the event stream. */

import { create } from 'zustand';
import { ApiError } from '../services/api';
import { getDeviceStatus, listDevices, type DeviceAccessStatus, type PairedDevice } from '../services/devicesApi';
import { log } from '../utils/logger';
import { getT } from '../i18n/useT';
import { useNotificationStore } from './notificationStore';

interface DeviceState {
    /** null until the first read; false when pairing is off or this isn't the PC, which hides every trace of it. */
    available: boolean | null;
    status: DeviceAccessStatus | null;
    devices: PairedDevice[];
    loadError: string | null;
    refresh: () => Promise<void>;
    setStatus: (status: DeviceAccessStatus) => void;
}

export const useDeviceStore = create<DeviceState>((set) => ({
    available: null,
    status: null,
    devices: [],
    loadError: null,
    refresh: async () => {
        try {
            const [status, devices] = await Promise.all([getDeviceStatus(), listDevices()]);
            set({ available: true, status, devices, loadError: null });
        } catch (err) {
            // 403: device_pairing is off in ~/.gaia/config.json, or the caller isn't the PC.
            if (err instanceof ApiError && err.httpStatus === 403) {
                set({ available: false, status: null, devices: [], loadError: null });
                return;
            }
            log.api.error('Reading paired devices failed', err);
            set({ loadError: err instanceof ApiError ? err.detail || err.message : String(err) });
        }
    },
    setStatus: (status) => set({ status }),
}));

/** What `/api/sessions/events` sends the PC about devices. */
export type DeviceEvent =
    | { type: 'device_paired'; device: PairedDevice | null; actions?: string[] }
    | { type: 'security_alert'; kind: string; address: string; at: string };

/** A device event from the PC's event stream: a Security notice, and fresh state. False for any other event. */
export function handleDeviceEvent(data: { type?: string }): boolean {
    if (data.type !== 'device_paired' && data.type !== 'security_alert') return false;
    const event = data as DeviceEvent;
    const store = useDeviceStore.getState();
    const notify = useNotificationStore.getState().addNotification;
    const t = getT();
    const base = { type: 'security_alert' as const, agentId: 'devices', agentName: 'GAIA', timestamp: Date.now(), read: false, dismissed: false, priority: 'high' as const };
    if (event.type === 'device_paired') {
        void store.refresh();
        const device = event.device;
        if (!device) {
            log.api.error('A device_paired event carried no device');
            return true;
        }
        const platform = t(`settings.devices.platform.${device.platform}`);
        notify({
            ...base,
            id: `device-paired-${device.id}`,
            title: t('notifications.devices.pairedTitle'),
            message: t('notifications.devices.pairedBody', { name: device.name, platform }),
            deviceId: device.id,
            actions: event.actions ?? ['review', 'deny'],
        });
        return true;
    }
    void store.refresh().then(() => {
        const failures = useDeviceStore.getState().status?.failed_redemptions ?? [];
        const n = Math.max(1, failures.filter((f) => f.address === event.address).length);
        notify({
            ...base,
            id: `security-alert-${event.kind}-${event.address}-${event.at}`,
            title: t('notifications.devices.failedTitle'),
            message: t.plural('notifications.devices.failedBody', n),
        });
    });
    return true;
}
