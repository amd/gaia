// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useEffect, useRef } from 'react'

function Settings() {
  const [config, setConfig] = useState(null)
  const [watchDir, setWatchDir] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [stats, setStats] = useState(null)
  const [message, setMessage] = useState(null)
  const [dragActive, setDragActive] = useState(false)
  const [initStatus, setInitStatus] = useState(null)
  const [initializing, setInitializing] = useState(false)
  const fileInputRef = useRef(null)

  // Fetch current config and stats
  useEffect(() => {
    fetchConfig()
    fetchStats()
    fetchInitStatus()
  }, [])

  const fetchConfig = async () => {
    try {
      const response = await fetch('/api/config')
      const data = await response.json()
      setConfig(data)
      setWatchDir(data.watch_dir || '')
      setLoading(false)
    } catch (error) {
      console.error('Error fetching config:', error)
      setMessage({ type: 'error', text: 'Failed to load configuration' })
      setLoading(false)
    }
  }

  const fetchStats = async () => {
    try {
      const response = await fetch('/api/stats')
      const data = await response.json()
      setStats(data)
    } catch (error) {
      console.error('Error fetching stats:', error)
    }
  }

  const fetchInitStatus = async () => {
    try {
      const response = await fetch('/api/init/status')
      const data = await response.json()
      setInitStatus(data)
    } catch (error) {
      console.error('Error fetching init status:', error)
      setInitStatus({ initialized: false, message: 'Failed to check status' })
    }
  }

  const handleInitialize = async () => {
    setInitializing(true)
    setMessage(null)

    try {
      const response = await fetch('/api/init', { method: 'POST' })
      const data = await response.json()

      if (data.success) {
        setMessage({ type: 'success', text: data.message })
        fetchInitStatus() // Refresh status
      } else {
        setMessage({ type: 'error', text: data.message })
      }
    } catch (error) {
      console.error('Error initializing:', error)
      setMessage({ type: 'error', text: 'Failed to initialize VLM model' })
    } finally {
      setInitializing(false)
    }
  }

  const handleClearDatabase = async () => {
    setClearing(true)
    setMessage(null)

    try {
      const response = await fetch('/api/database', {
        method: 'DELETE',
      })

      const data = await response.json()

      if (response.ok && data.success) {
        const deleted = data.deleted || {}
        setMessage({
          type: 'success',
          text: `Database cleared: ${deleted.patients || 0} patients, ${deleted.alerts || 0} alerts, ${deleted.intake_sessions || 0} sessions removed`,
        })
        setShowClearConfirm(false)
        // Refresh stats
        fetchStats()
      } else {
        setMessage({ type: 'error', text: data.detail || 'Failed to clear database' })
      }
    } catch (error) {
      console.error('Error clearing database:', error)
      setMessage({ type: 'error', text: 'Failed to clear database' })
    } finally {
      setClearing(false)
    }
  }

  const handleSaveWatchDir = async () => {
    if (!watchDir.trim()) {
      setMessage({ type: 'error', text: 'Please enter a directory path' })
      return
    }

    setSaving(true)
    setMessage(null)

    try {
      const response = await fetch('/api/config/watch-dir', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ watch_dir: watchDir }),
      })

      const data = await response.json()

      if (response.ok && data.success) {
        setMessage({ type: 'success', text: data.message })
        setConfig(prev => ({ ...prev, watch_dir: data.watch_dir }))
      } else {
        setMessage({ type: 'error', text: data.detail || 'Failed to update directory' })
      }
    } catch (error) {
      console.error('Error saving watch dir:', error)
      setMessage({ type: 'error', text: 'Failed to save configuration' })
    } finally {
      setSaving(false)
    }
  }

  const handleFileUpload = async (files) => {
    if (!files || files.length === 0) return

    setUploading(true)
    setMessage(null)

    const results = []

    for (const file of files) {
      try {
        const formData = new FormData()
        formData.append('file', file)

        const response = await fetch('/api/upload', {
          method: 'POST',
          body: formData,
        })

        const data = await response.json()

        if (response.ok && data.success) {
          results.push({
            filename: file.name,
            success: true,
            message: `${data.patient_name} (ID: ${data.patient_id})`,
          })
        } else {
          results.push({
            filename: file.name,
            success: false,
            message: data.detail || data.message || 'Processing failed',
          })
        }
      } catch (error) {
        results.push({
          filename: file.name,
          success: false,
          message: 'Upload failed',
        })
      }
    }

    const successCount = results.filter(r => r.success).length

    if (successCount === files.length) {
      setMessage({
        type: 'success',
        text: `Successfully processed ${successCount} file(s)`,
        details: results,
      })
    } else if (successCount > 0) {
      setMessage({
        type: 'warning',
        text: `Processed ${successCount} of ${files.length} file(s)`,
        details: results,
      })
    } else {
      setMessage({
        type: 'error',
        text: 'Failed to process files',
        details: results,
      })
    }

    setUploading(false)
  }

  const handleDrag = (e) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true)
    } else if (e.type === 'dragleave') {
      setDragActive(false)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileUpload(Array.from(e.dataTransfer.files))
    }
  }

  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFileUpload(Array.from(e.target.files))
    }
  }

  if (loading) {
    return (
      <main className="main-content">
        <div className="settings-container">
          <div className="loading-spinner">Loading configuration...</div>
        </div>
      </main>
    )
  }

  return (
    <main className="main-content">
      <div className="settings-container">
        <h1 className="settings-title">Settings</h1>

        {/* Message Display */}
        {message && (
          <div className={`settings-message ${message.type}`}>
            <span>{message.text}</span>
            {message.details && (
              <ul className="message-details">
                {message.details.map((d, i) => (
                  <li key={i} className={d.success ? 'success' : 'error'}>
                    <strong>{d.filename}:</strong> {d.message}
                  </li>
                ))}
              </ul>
            )}
            <button className="message-close" onClick={() => setMessage(null)}>×</button>
          </div>
        )}

        {/* Watch Directory Section */}
        <section className="settings-section">
          <h2 className="section-title">
            <span className="section-icon">📁</span>
            Watch Directory
          </h2>
          <p className="section-description">
            The agent monitors this directory for new intake forms. When a file is added,
            it will be automatically processed.
          </p>

          <div className="watch-dir-form">
            <div className="input-group">
              <label htmlFor="watchDir">Directory Path</label>
              <div className="input-row">
                <input
                  id="watchDir"
                  type="text"
                  value={watchDir}
                  onChange={(e) => setWatchDir(e.target.value)}
                  placeholder="/path/to/intake_forms"
                  className="text-input"
                />
                <button
                  onClick={handleSaveWatchDir}
                  disabled={saving || watchDir === config?.watch_dir}
                  className="btn-primary"
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
              </div>
              {config?.watch_dir && watchDir !== config.watch_dir && (
                <p className="input-hint">
                  Current: <code>{config.watch_dir}</code>
                </p>
              )}
            </div>
          </div>
        </section>

        {/* File Upload Section */}
        <section className="settings-section">
          <h2 className="section-title">
            <span className="section-icon">📤</span>
            Upload Intake Form
          </h2>
          <p className="section-description">
            Upload patient intake forms directly. Supported formats: PNG, JPG, JPEG, PDF, TIFF, BMP.
          </p>

          <div
            className={`dropzone ${dragActive ? 'active' : ''} ${uploading ? 'uploading' : ''}`}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
            onClick={() => !uploading && fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".png,.jpg,.jpeg,.pdf,.tiff,.bmp"
              onChange={handleFileSelect}
              style={{ display: 'none' }}
            />

            {uploading ? (
              <div className="dropzone-content">
                <div className="upload-spinner"></div>
                <p>Processing files...</p>
              </div>
            ) : (
              <div className="dropzone-content">
                <div className="dropzone-icon">📄</div>
                <p className="dropzone-text">
                  Drag and drop intake forms here, or <span className="link">browse</span>
                </p>
                <p className="dropzone-hint">PNG, JPG, PDF, TIFF, BMP</p>
              </div>
            )}
          </div>
        </section>

        {/* System Info Section */}
        <section className="settings-section">
          <h2 className="section-title">
            <span className="section-icon">ℹ️</span>
            System Information
          </h2>

          <div className="info-grid">
            <div className="info-item">
              <span className="info-label">Agent Status</span>
              <span className={`info-value status ${config?.agent_running ? 'running' : 'stopped'}`}>
                {config?.agent_running ? '● Running' : '○ Stopped'}
              </span>
            </div>
            <div className="info-item">
              <span className="info-label">VLM Model</span>
              <span className="info-value">{config?.vlm_model || 'N/A'}</span>
            </div>
            <div className="info-item full-width">
              <span className="info-label">Database Path</span>
              <span className="info-value code" title={config?.db_path}>{config?.db_path || 'N/A'}</span>
            </div>
            <div className="info-item full-width">
              <span className="info-label">Watch Directory (Full Path)</span>
              <span className="info-value code" title={config?.watch_dir}>{config?.watch_dir || 'N/A'}</span>
            </div>
          </div>
        </section>

        {/* Model Initialization Section */}
        <section className="settings-section">
          <h2 className="section-title">
            <span className="section-icon">🤖</span>
            Model Initialization
          </h2>
          <p className="section-description">
            Initialize required models: VLM (form extraction), LLM (chat), and Embedding (search).
          </p>

          <div className="init-status-card">
            {/* Server and Context Status */}
            <div className="init-status-grid">
              <div className="init-status-item">
                <span className="init-label">Lemonade Server</span>
                <span className={`init-value ${initStatus?.server_running ? 'success' : 'error'}`}>
                  {initStatus?.server_running ? '● Running' : '○ Not Running'}
                </span>
              </div>
              <div className="init-status-item">
                <span className="init-label">Context Size</span>
                <span className={`init-value ${initStatus?.context_size_ok ? 'success' : initStatus?.context_size > 0 ? 'warning' : ''}`}>
                  {initStatus?.context_size > 0
                    ? `${initStatus.context_size.toLocaleString()} tokens`
                    : 'Not available'}
                  {initStatus?.context_size > 0 && !initStatus?.context_size_ok && (
                    <span className="context-warning"> (need 32k)</span>
                  )}
                </span>
              </div>
              <div className="init-status-item">
                <span className="init-label">Models Ready</span>
                <span className={`init-value ${initStatus?.ready_count === 3 ? 'success' : initStatus?.ready_count > 0 ? 'warning' : 'error'}`}>
                  {initStatus?.ready_count || 0} / 3
                </span>
              </div>
            </div>

            {/* Required Models Status Table */}
            {initStatus?.models && (
              <div className="init-models-table">
                <div className="models-header">
                  <span>Model</span>
                  <span>Purpose</span>
                  <span>Status</span>
                </div>
                {Object.entries(initStatus.models).map(([key, model]) => (
                  <div key={key} className="model-row">
                    <span className="model-name" title={model.name}>
                      {model.name || 'Unknown'}
                    </span>
                    <span className="model-purpose">{model.purpose || '-'}</span>
                    <span className={`model-status ${model.loaded ? 'loaded' : model.available ? 'available' : 'missing'}`}>
                      {model.loaded ? '● Ready' : model.available ? '○ Downloaded' : '○ Not Downloaded'}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Context Size Warning */}
            {initStatus?.context_size > 0 && !initStatus?.context_size_ok && (
              <div className="init-warning">
                <span className="warning-icon">⚠️</span>
                <div className="warning-content">
                  <strong>Context size too small</strong>
                  <p>Current: {initStatus.context_size.toLocaleString()} tokens. Recommended: 32,768 tokens.</p>
                  <p className="warning-fix">Fix: Right-click Lemonade tray → Settings → Context Size → 32768</p>
                </div>
              </div>
            )}

            <div className="init-actions">
              <button
                className="btn-primary"
                onClick={handleInitialize}
                disabled={initializing || (initStatus?.ready_count === 3 && initStatus?.context_size_ok)}
              >
                {initializing ? 'Initializing...' :
                 initStatus?.ready_count === 3 && initStatus?.context_size_ok ? '✓ All Models Ready' :
                 initStatus?.ready_count === 3 ? 'Reload Models' :
                 initStatus?.ready_count > 0 ? `Initialize (${3 - initStatus.ready_count} remaining)` :
                 'Initialize All Models'}
              </button>
              <button
                className="btn-secondary"
                onClick={fetchInitStatus}
                disabled={initializing}
              >
                Refresh Status
              </button>
            </div>

            {initializing && (
              <div className="init-progress">
                <div className="init-spinner"></div>
                <span>Initializing models... This may take a few minutes if models need to be downloaded.</span>
              </div>
            )}
          </div>
        </section>

        {/* Danger Zone Section */}
        <section className="settings-section danger-zone">
          <h2 className="section-title">
            <span className="section-icon">⚠️</span>
            Danger Zone
          </h2>
          <p className="section-description">
            These actions are irreversible. Please proceed with caution.
          </p>

          <div className="danger-action">
            <div className="danger-info">
              <h3>Clear Database</h3>
              <p>
                Remove all patient records, alerts, and intake sessions from the database.
                {stats?.total_patients > 0 && (
                  <span className="data-count">
                    {' '}Currently storing <strong>{stats.total_patients}</strong> patient record(s).
                  </span>
                )}
              </p>
            </div>

            {!showClearConfirm ? (
              <button
                className="btn-danger"
                onClick={() => setShowClearConfirm(true)}
                disabled={clearing || !stats || stats.total_patients === 0}
              >
                Clear Database
              </button>
            ) : (
              <div className="confirm-dialog">
                <p className="confirm-warning">
                  Are you sure? This will permanently delete all {stats?.total_patients || 0} patient records.
                </p>
                <div className="confirm-buttons">
                  <button
                    className="btn-danger-confirm"
                    onClick={handleClearDatabase}
                    disabled={clearing}
                  >
                    {clearing ? 'Clearing...' : 'Yes, Clear Everything'}
                  </button>
                  <button
                    className="btn-cancel"
                    onClick={() => setShowClearConfirm(false)}
                    disabled={clearing}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  )
}

export default Settings
