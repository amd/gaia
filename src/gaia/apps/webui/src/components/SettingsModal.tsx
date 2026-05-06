import React from 'react';
import './SettingsModal.css';

// Minimal SettingsModal used by tests — full implementation lives in app code.
export default function SettingsModal({ isOpen = true, onClose }: { isOpen?: boolean; onClose?: () => void }) {
  return (
    <div className="settings-modal" role="dialog" aria-modal="true" data-open={isOpen}>
      <header>
        <h3>Settings</h3>
        <div className="settings-version">Version: __APP_VERSION__</div>
      </header>

      <section className="settings-section">
        <p>Configuration and preferences for the GAIA Agent UI.</p>
      </section>

      <div className="danger-zone">
        <div className="danger-warning">Danger Zone</div>
        <p>Clicking this action will permanently delete all sessions and cannot be undone.</p>
      </div>
    </div>
  );
}
