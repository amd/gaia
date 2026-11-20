// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The chat-alert settings and how a saved copy is read back. Pure, so the web store and the phone app share it. */

/** The chat events that can alert the user. */
export type AlertEvent = 'needs_approval' | 'question' | 'finished';
/** banner: OS notification plus badge; badge: counted on the tray/dock badge only. */
export type AlertMode = 'banner' | 'badge' | 'off';

export interface AlertSettings {
  modes: Record<AlertEvent, AlertMode>;
  /** A short chime with each alert. */
  sound: boolean;
  /** Flash the taskbar button when an alert fires and GAIA isn't focused. */
  flash: boolean;
  /** Hold off idle sleep while a chat turn runs (desktop app). */
  keepAwake: boolean;
  /** On battery, stop holding off sleep below this percent; 0 never stops. */
  batteryFloor: number;
}

export const ALERT_SETTINGS_KEY = 'gaia_alert_settings';

export const DEFAULT_ALERT_SETTINGS: AlertSettings = {
  modes: { needs_approval: 'banner', question: 'banner', finished: 'banner' },
  sound: false,
  flash: true,
  keepAwake: true,
  batteryFloor: 20,
};

export const ALERT_MODES: readonly AlertMode[] = ['banner', 'badge', 'off'];

/** Saved settings over the defaults; a corrupt copy is reported and replaced. */
export function parseAlertSettings(raw: string | null): AlertSettings {
  if (!raw) return DEFAULT_ALERT_SETTINGS;
  let saved: Partial<AlertSettings>;
  try {
    saved = JSON.parse(raw);
  } catch (err) {
    console.warn('[alerts] Saved notification settings are corrupt; using defaults.', err);
    return DEFAULT_ALERT_SETTINGS;
  }
  const modes = { ...DEFAULT_ALERT_SETTINGS.modes };
  for (const event of Object.keys(modes) as AlertEvent[]) {
    const m = saved.modes?.[event];
    if (m !== undefined && ALERT_MODES.includes(m)) modes[event] = m;
    else if (m !== undefined) console.warn(`[alerts] Ignored unknown mode ${String(m)} for ${event}.`);
  }
  const bool = (v: unknown, d: boolean) => (typeof v === 'boolean' ? v : d);
  const floor = saved.batteryFloor;
  return {
    modes,
    sound: bool(saved.sound, DEFAULT_ALERT_SETTINGS.sound),
    flash: bool(saved.flash, DEFAULT_ALERT_SETTINGS.flash),
    keepAwake: bool(saved.keepAwake, DEFAULT_ALERT_SETTINGS.keepAwake),
    batteryFloor: typeof floor === 'number' && floor >= 0 && floor <= 100
      ? floor : DEFAULT_ALERT_SETTINGS.batteryFloor,
  };
}
