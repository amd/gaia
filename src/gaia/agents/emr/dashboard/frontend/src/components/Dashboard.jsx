// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import WatchFolderPanel from './WatchFolderPanel'

// Field categories for extracted data display
const FIELD_CATEGORIES = {
  '👤 Identity': ['first_name', 'last_name', 'date_of_birth', 'gender', 'ssn'],
  '📞 Contact': ['phone', 'mobile_phone', 'email', 'address', 'city', 'state', 'zip_code'],
  '🏥 Insurance': ['insurance_provider', 'insurance_id', 'insurance_group'],
  '💊 Medical': ['reason_for_visit', 'allergies', 'medications', 'date_of_injury'],
  '🆘 Emergency': ['emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_relationship'],
  '💼 Employment': ['employer', 'occupation', 'work_related_injury'],
  '👨‍⚕️ Provider': ['referring_physician', 'referring_physician_phone'],
}

// Fields to skip in display
const SKIP_FIELDS = new Set([
  'id', 'source_file', 'raw_extraction', 'file_content', 'file_hash',
  'is_new_patient', 'processing_time_seconds', 'changes_detected',
  'created_at', 'updated_at', 'estimated_manual_seconds'
])

function Dashboard({ stats, patients, events, dbAlerts, onAcknowledgeAlert, onMarkReviewed, processingProgress, config, watchFolderData, onRefreshWatchFolder }) {
  const navigate = useNavigate()
  const [processingError, setProcessingError] = useState(null)
  const [elapsedTime, setElapsedTime] = useState(0)
  const [expandedFeedItem, setExpandedFeedItem] = useState(null)

  // Find any processing error for current file
  useEffect(() => {
    if (processingProgress?.filename) {
      const error = events.find(
        e => e.type === 'processing_error' && e.data?.filename === processingProgress.filename
      )
      setProcessingError(error?.data || null)
    } else {
      setProcessingError(null)
    }
  }, [events, processingProgress])

  // Update elapsed time every 100ms while processing
  useEffect(() => {
    if (!processingProgress?.startTime) {
      setElapsedTime(0)
      return
    }

    const updateElapsed = () => {
      setElapsedTime((Date.now() - processingProgress.startTime) / 1000)
    }

    updateElapsed() // Initial update
    const interval = setInterval(updateElapsed, 100)

    return () => clearInterval(interval)
  }, [processingProgress?.startTime])

  // Format elapsed time
  const formatElapsedTime = (seconds) => {
    if (seconds < 60) {
      return `${seconds.toFixed(1)}s`
    }
    const mins = Math.floor(seconds / 60)
    const secs = (seconds % 60).toFixed(0)
    return `${mins}m ${secs}s`
  }

  // Processing step definitions
  const processingSteps = [
    { num: 1, name: 'Reading file' },
    { num: 2, name: 'Checking duplicates' },
    { num: 3, name: 'Preparing image' },
    { num: 4, name: 'Loading AI model' },
    { num: 5, name: 'Extracting data' },
    { num: 6, name: 'Parsing data' },
    { num: 7, name: 'Saving to database' },
  ]

  // Calculate progress percentage
  const getProgressPercent = () => {
    if (!processingProgress) return 0
    return Math.round((processingProgress.step_num / processingProgress.total_steps) * 100)
  }

  return (
    <main className="main-content">
      {/* Stats Row */}
      <div className="stats-row">
        <div className="stat-card highlight">
          <div className="stat-header">
            <span className="stat-label">Time Saved Today</span>
            <div className="stat-icon success">&#9201;</div>
          </div>
          <div className="stat-value success">
            {stats?.time_saved_minutes || 0} min
          </div>
          <div className="stat-subtext">vs. manual processing</div>
          <div className="stat-progress">
            <div
              className="stat-progress-bar"
              style={{ width: `${Math.min(100, (stats?.time_saved_minutes || 0) * 2)}%` }}
            />
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-header">
            <span className="stat-label">Patients Processed</span>
            <div className="stat-icon info">&#128101;</div>
          </div>
          <div className="stat-value">{stats?.total_patients || 0}</div>
          <div className="stat-subtext">
            {stats?.new_patients || 0} new, {stats?.returning_patients || 0} returning
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-header">
            <span className="stat-label">Avg Processing Time</span>
            <div className="stat-icon success">&#9889;</div>
          </div>
          <div className="stat-value">{stats?.avg_processing_seconds || '0.0'}s</div>
          <div className="stat-subtext">per intake form</div>
        </div>
        <div className="stat-card">
          <div className="stat-header">
            <span className="stat-label">Pending Review</span>
            <div className="stat-icon warning">&#128203;</div>
          </div>
          <div className="stat-value">{stats?.unacknowledged_alerts || 0}</div>
          <div className="stat-subtext">changes detected</div>
        </div>
      </div>

      {/* Main Grid */}
      <div className="main-grid">
        {/* Left Column - Combined Review & Alerts + Watch Folder */}
        <div className="left-column">
          <div className="card">
          <div className="card-header">
            <div className="card-title">
              <span>&#128203;</span> Review & Alerts
              {(dbAlerts.length > 0 || patients.filter(p => p.changes_detected?.length > 0).length > 0) && (
                <span className="card-badge">
                  {dbAlerts.length + patients.filter(p => p.changes_detected?.length > 0).length} Items
                </span>
              )}
            </div>
          </div>
          <div className="card-content">
            {/* Critical Alerts Section */}
            {dbAlerts.slice(0, 3).map(alert => (
              <div
                key={`alert-${alert.id}`}
                className={`alert-item ${alert.alert_type === 'allergy' ? 'critical' : 'warning'}`}
              >
                <div className="alert-header">
                  <span className="alert-icon">
                    {alert.alert_type === 'allergy' ? '⚠️' : '📋'}
                  </span>
                  <span className={`alert-type ${alert.alert_type === 'allergy' ? 'critical' : 'warning'}`}>
                    {alert.alert_type === 'allergy' ? 'ALLERGY ALERT' : 'MISSING DATA'}
                  </span>
                </div>
                <div className="alert-patient">
                  {alert.first_name} {alert.last_name}
                </div>
                <div className="alert-detail">{alert.message}</div>
                <button
                  className="alert-action"
                  onClick={() => onAcknowledgeAlert(alert.id)}
                  style={alert.alert_type !== 'allergy' ? { background: 'var(--warning)' } : {}}
                >
                  ACKNOWLEDGE
                </button>
              </div>
            ))}

            {/* Pending Reviews Section */}
            {patients.filter(p => p.changes_detected?.length > 0).slice(0, 3).map(patient => (
              <div key={`review-${patient.id}`} className="review-item">
                <div className="review-header">
                  <span className="review-name">{patient.first_name} {patient.last_name}</span>
                  <span className="review-badge">
                    {patient.changes_detected?.length || 0} Changes
                  </span>
                </div>
                <div className="review-changes">
                  <div className="review-label">Changes detected:</div>
                  {patient.changes_detected?.slice(0, 2).map((change, idx) => (
                    <div key={idx} className="change-item">
                      <span className="change-field">{change.field}:</span>
                      <span className="change-old">{change.old_value || 'N/A'}</span>
                      <span className="change-arrow">&#8594;</span>
                      <span className="change-new">{change.new_value}</span>
                    </div>
                  ))}
                </div>
                <div className="review-actions">
                  <button
                    className="btn btn-approve"
                    onClick={() => onMarkReviewed && onMarkReviewed(patient.id)}
                  >
                    Approve
                  </button>
                  <button
                    className="btn btn-edit"
                    onClick={() => navigate(`/patient/${patient.id}`)}
                  >
                    View
                  </button>
                </div>
              </div>
            ))}

            {dbAlerts.length === 0 && patients.filter(p => p.changes_detected?.length > 0).length === 0 && (
              <div style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '1rem' }}>
                No items requiring attention
              </div>
            )}
          </div>
        </div>

          {/* Watch Folder Panel - Below Alerts */}
          <WatchFolderPanel
            watchFolderData={watchFolderData}
            onRefresh={onRefreshWatchFolder}
          />
        </div>

        {/* Center Column - Live Feed + Processing */}
        <div className="card">
          <div className="card-header">
            <div className="card-title">
              <span>&#9889;</span> Live Intake Feed
            </div>
            <span style={{ color: 'var(--success-text)', fontSize: '0.8rem' }}>&#9679; Live</span>
          </div>

          {/* Status Bar - Watch Folder Info */}
          {config && (
            <div className="feed-status-bar">
              <div className="status-item">
                <span className="status-icon">📁</span>
                <span className="status-label">Watching:</span>
                <span className="status-value">{config.watch_dir}</span>
              </div>
              <div className="status-item">
                <span className="status-icon">💾</span>
                <span className="status-label">Database:</span>
                <span className="status-value">{stats?.total_patients || 0} patients</span>
              </div>
              {config.agent_running && (
                <div className="status-item status-active">
                  <span className="status-icon">✓</span>
                  <span>Agent Active</span>
                </div>
              )}
            </div>
          )}

          <div className="card-content">
            {/* New File Detected - show first in feed when processing */}
            {processingProgress && (
              <div className="feed-item">
                <div className="feed-status processing"></div>
                <div className="feed-content">
                  <div className="feed-time">
                    {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                  <div className="feed-name">{processingProgress.filename || 'New file'}</div>
                  <div className="feed-detail">
                    <span className="feed-detecting" style={{ color: 'var(--amd-red-text)' }}>
                      🔄 Processing...
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Active Processing Progress Card */}
            {processingProgress && (
              <div className={`processing-card ${processingError ? 'has-error' : ''}`}>
                <div className="processing-header">
                  {!processingError && processingProgress.status !== 'error' && (
                    <div className="processing-spinner"></div>
                  )}
                  {(processingError || processingProgress.status === 'error') && (
                    <div className="processing-error-icon">⚠️</div>
                  )}
                  <div className="processing-title">
                    {processingError || processingProgress.status === 'error' ? 'Error: ' : 'Processing: '}
                    {processingProgress.filename || 'intake form'}
                  </div>
                  <div className="processing-elapsed">
                    ⏱️ {formatElapsedTime(elapsedTime)}
                  </div>
                </div>

                {processingError ? (
                  <div className="processing-error">
                    <div className="error-message">{processingError.error}</div>
                    <div className="error-type">
                      {processingError.error_type === 'validation_error'
                        ? 'The form could not be processed - required fields are missing'
                        : 'An error occurred during processing'}
                    </div>
                  </div>
                ) : (
                  <>
                    {/* Progress Bar */}
                    <div className="processing-progress-container">
                      <div className="processing-progress-bar">
                        <div
                          className={`processing-progress-fill ${processingProgress.status === 'error' ? 'error' : ''}`}
                          style={{ width: `${getProgressPercent()}%` }}
                        />
                      </div>
                      <div className="processing-progress-text">
                        Step {processingProgress.step_num} of {processingProgress.total_steps} ({getProgressPercent()}%)
                      </div>
                    </div>

                    {/* Current Step */}
                    <div className="processing-current-step">
                      <span className="step-indicator">▶</span>
                      <span className="step-name">{processingProgress.step_name}</span>
                    </div>

                    {/* Step List */}
                    <div className="processing-steps">
                      {processingSteps.map((step) => {
                        const isComplete = step.num < processingProgress.step_num
                        const isActive = step.num === processingProgress.step_num
                        const stepClass = isComplete ? 'complete' : isActive ? 'active' : 'pending'
                        return (
                          <div key={step.num} className={`step ${stepClass}`}>
                            {isComplete ? '✓' : isActive ? '•' : '○'} {step.name}
                          </div>
                        )
                      })}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Feed Items (exclude processing_started since shown above, and successful processing_completed since patient_created shows same info) */}
            {events
              .filter(e => {
                // Always exclude these event types
                if (['status_update', 'heartbeat', 'processing_started'].includes(e.type)) {
                  return false
                }
                // Exclude successful processing_completed events (patient_created already shows the result)
                // Keep processing_completed only for duplicates and failures
                if (e.type === 'processing_completed' && e.data?.success && !e.data?.is_duplicate) {
                  return false
                }
                return true
              })
              .slice(0, 10)
              .map((event, idx) => {
                const isExpanded = expandedFeedItem === idx
                const isExpandable = event.type === 'patient_created' && event.data
                const fieldCount = isExpandable ? Object.keys(event.data).filter(k => !SKIP_FIELDS.has(k) && event.data[k]).length : 0

                return (
                <div key={idx} className={`feed-item ${event.type === 'processing_error' ? 'has-error' : ''} ${isExpanded ? 'expanded' : ''}`}>
                  <div
                    className={`feed-item-header ${isExpandable ? 'clickable' : ''}`}
                    onClick={() => isExpandable && setExpandedFeedItem(isExpanded ? null : idx)}
                  >
                    <div className={`feed-status ${
                      event.type === 'patient_created'
                        ? (event.data?.changes_detected?.length > 0 ? 'warning' : 'complete')
                        : event.type === 'processing_error'
                          ? 'error'
                          : event.type === 'processing_completed'
                            ? (event.data?.is_duplicate ? 'duplicate' : event.data?.success === false ? 'error' : 'complete')
                            : event.type === 'processing_started'
                              ? 'processing'
                              : 'complete'
                    }`}></div>
                    <div className="feed-content">
                      <div className="feed-time">
                        {new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </div>
                      <div className="feed-name">
                        {event.type === 'processing_error'
                          ? event.data?.filename || 'Unknown file'
                          : event.type === 'processing_started'
                            ? event.data?.filename || 'New file'
                            : event.type === 'processing_completed' && event.data?.is_duplicate
                              ? event.data?.patient_name || event.data?.filename || 'Duplicate'
                              : event.data?.first_name && event.data?.last_name
                                ? `${event.data.first_name} ${event.data.last_name}`
                                : event.data?.filename || 'Unknown'}
                      </div>
                      <div className="feed-detail">
                        {event.type === 'processing_started' && (
                          <span className="feed-detecting">
                            📄 New file detected - processing...
                          </span>
                        )}
                        {event.type === 'processing_error' && (
                          <span className="feed-error">
                            ⚠️ {event.data?.error || 'Processing failed'}
                          </span>
                        )}
                        {event.data?.is_new_patient !== undefined && (
                          <span className={`feed-tag ${event.data.is_new_patient ? 'new' : 'returning'}`}>
                            {event.data.is_new_patient ? 'New Patient' : 'Returning'}
                          </span>
                        )}
                        {event.type === 'patient_created' && (
                          <>
                            {event.data?.changes_detected?.length > 0
                              ? 'Changes Detected ⚠'
                              : `Complete ✓`}
                            {event.data?.processing_time_seconds &&
                              ` | ${event.data.processing_time_seconds.toFixed(1)}s`}
                            <span className="feed-field-count">{fieldCount} fields</span>
                          </>
                        )}
                        {event.type === 'processing_completed' && event.data?.is_duplicate && (
                          <span className="feed-duplicate">
                            ⚡ Duplicate - {event.data?.patient_name || 'already processed'}
                          </span>
                        )}
                        {event.type === 'processing_completed' && !event.data?.success && !event.data?.is_duplicate && !events.find(
                          e => e.type === 'processing_error' && e.data?.filename === event.data?.filename
                        ) && (
                          'Processing Failed ✗'
                        )}
                      </div>
                    </div>
                    {isExpandable && (
                      <div className="feed-expand-icon">{isExpanded ? '▼' : '▶'}</div>
                    )}
                  </div>

                  {/* Expanded Details */}
                  {isExpanded && event.data && (
                    <div className="feed-expanded-details">
                      {Object.entries(FIELD_CATEGORIES).map(([category, fields]) => {
                        const categoryFields = fields.filter(f => event.data[f] && event.data[f] !== 'null')
                        if (categoryFields.length === 0) return null
                        return (
                          <div key={category} className="feed-category">
                            <div className="feed-category-title">{category}</div>
                            <div className="feed-category-fields">
                              {categoryFields.map(field => (
                                <div key={field} className="feed-field">
                                  <span className="feed-field-label">{field.replace(/_/g, ' ')}</span>
                                  <span className="feed-field-value">{String(event.data[field])}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )
                      })}

                      {/* Additional fields not in categories */}
                      {(() => {
                        const allCategoryFields = new Set(Object.values(FIELD_CATEGORIES).flat())
                        const additionalFields = Object.entries(event.data)
                          .filter(([k, v]) => !allCategoryFields.has(k) && !SKIP_FIELDS.has(k) && v && v !== 'null')
                        if (additionalFields.length === 0) return null
                        return (
                          <div className="feed-category">
                            <div className="feed-category-title">📋 Additional</div>
                            <div className="feed-category-fields">
                              {additionalFields.map(([field, value]) => (
                                <div key={field} className="feed-field">
                                  <span className="feed-field-label">{field.replace(/_/g, ' ')}</span>
                                  <span className="feed-field-value">{String(value)}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )
                      })()}

                      <div className="feed-view-patient">
                        <button
                          className="btn btn-sm btn-secondary"
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/patient/${event.data.id}`)
                          }}
                        >
                          View Full Record →
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            )}

            {events.length === 0 && !processingProgress && (
              <div style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '2rem' }}>
                {patients.length > 0 ? (
                  <>
                    <div style={{ marginBottom: '0.5rem' }}>
                      ✓ {patients.length} patients in database
                    </div>
                    <div style={{ fontSize: '0.85rem' }}>
                      Drop new files into the watch folder to see live processing
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ marginBottom: '0.5rem' }}>
                      Waiting for intake forms...
                    </div>
                    <div style={{ fontSize: '0.85rem' }}>
                      Drop image or PDF files into: {config?.watch_dir || 'watch folder'}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right Column - Efficiency Metrics */}
        <div>
          {/* Efficiency Metrics */}
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                <span>&#128200;</span> Efficiency Metrics
              </div>
            </div>
            <div className="card-content">
              <div className="time-widget">
                <div className="time-value">{stats?.time_saved_percent || '0%'}</div>
                <div className="time-label">Time Reduction</div>

                {/* Totals Section */}
                <div className="efficiency-totals">
                  <div className="efficiency-row">
                    <span className="efficiency-label">Forms Processed:</span>
                    <span className="efficiency-value">{stats?.extraction_success || 0}</span>
                  </div>
                  <div className="efficiency-row">
                    <span className="efficiency-label">Manual Would Take:</span>
                    <span className="efficiency-value manual">
                      {stats?.total_estimated_manual_seconds
                        ? `${Math.round(stats.total_estimated_manual_seconds / 60 * 10) / 10} min`
                        : '0 min'}
                    </span>
                  </div>
                  <div className="efficiency-row">
                    <span className="efficiency-label">AI Processing Time:</span>
                    <span className="efficiency-value ai">
                      {stats?.total_ai_processing_seconds
                        ? `${Math.round(stats.total_ai_processing_seconds * 10) / 10} sec`
                        : '0 sec'}
                    </span>
                  </div>
                  <div className="efficiency-row highlight">
                    <span className="efficiency-label">Time Saved:</span>
                    <span className="efficiency-value saved">
                      {stats?.time_saved_minutes
                        ? `${stats.time_saved_minutes} min`
                        : '0 min'}
                    </span>
                  </div>
                </div>

                {/* Per-Form Averages */}
                <div className="efficiency-averages">
                  <span className="avg-label">Per Form:</span>
                  <span className="avg-item">
                    ~{stats?.avg_manual_seconds
                      ? `${Math.round(stats.avg_manual_seconds / 60 * 10) / 10} min manual`
                      : 'N/A'}
                  </span>
                  <span className="avg-separator">vs</span>
                  <span className="avg-item highlight">
                    {stats?.avg_processing_seconds
                      ? `${stats.avg_processing_seconds}s AI`
                      : 'N/A'}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}

export default Dashboard
