// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useRef, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import amdLogo from '../assets/amd.png'

function Header({ alertCount = 0, alerts = [], onAcknowledgeAlert }) {
  const navigate = useNavigate()
  const location = useLocation()
  const [showDropdown, setShowDropdown] = useState(false)
  const dropdownRef = useRef(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleAlertClick = (alert) => {
    if (onAcknowledgeAlert) {
      onAcknowledgeAlert(alert.id)
    }
    setShowDropdown(false)
  }

  return (
    <>
      {/* Header */}
      <header className="header">
        <div className="logo-section">
          <img src={amdLogo} alt="AMD" className="amd-logo-img" />
          <div className="logo" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
            <div>
              <div className="logo-text">GAIA Medical Intake</div>
              <div className="logo-subtitle">AI-Powered Patient Processing</div>
            </div>
          </div>
          <div className="amd-badge">
            <span>Ryzen AI</span>
            <span>|</span>
            <span>Running Locally</span>
          </div>
          <div className="poc-badge-subtle" title="This is a demonstration application, not for production use with real patient data">
            <span>DEMO</span>
          </div>
        </div>
        <div className="header-right">
          <div className="alerts-dropdown-container" ref={dropdownRef}>
            <div
              className="notification-badge"
              onClick={() => setShowDropdown(!showDropdown)}
            >
              <span>🔔 Alerts</span>
              {alertCount > 0 && <div className="notification-count">{alertCount}</div>}
            </div>

            {showDropdown && (
              <div className="alerts-dropdown">
                <div className="alerts-dropdown-header">
                  <span className="alerts-dropdown-title">Active Alerts</span>
                  <span style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>
                    {alerts.length} total
                  </span>
                </div>
                <div className="alerts-dropdown-content">
                  {alerts.length > 0 ? (
                    alerts.slice(0, 5).map(alert => (
                      <div
                        key={alert.id}
                        className={`alerts-dropdown-item ${alert.alert_type === 'allergy' ? 'critical' : 'warning'}`}
                        onClick={() => handleAlertClick(alert)}
                      >
                        <span className="alert-dropdown-icon">
                          {alert.alert_type === 'allergy' ? '⚠️' : '📋'}
                        </span>
                        <div className="alert-dropdown-info">
                          <div className={`alert-dropdown-type ${alert.alert_type === 'allergy' ? 'critical' : 'warning'}`}>
                            {alert.alert_type === 'allergy' ? 'Allergy Alert' : 'Missing Data'}
                          </div>
                          <div className="alert-dropdown-patient">
                            {alert.first_name} {alert.last_name}
                          </div>
                          <div className="alert-dropdown-message">{alert.message}</div>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="alerts-dropdown-empty">
                      ✓ No active alerts
                    </div>
                  )}
                </div>
                {alerts.length > 0 && (
                  <div className="alerts-dropdown-footer">
                    <a onClick={() => { navigate('/'); setShowDropdown(false); }}>
                      View All on Dashboard →
                    </a>
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="user-profile">
            <div className="user-avatar">DS</div>
            <span>Dr. Smith</span>
          </div>
        </div>
      </header>

      {/* Navigation Tabs */}
      <nav className="nav-tabs">
        <div
          className={`nav-tab ${location.pathname === '/' ? 'active' : ''}`}
          onClick={() => navigate('/')}
        >
          Dashboard
        </div>
        <div
          className={`nav-tab ${location.pathname === '/patients' ? 'active' : ''}`}
          onClick={() => navigate('/patients')}
        >
          Patient Database
        </div>
        <div
          className={`nav-tab ${location.pathname === '/chat' ? 'active' : ''}`}
          onClick={() => navigate('/chat')}
        >
          💬 Chat
        </div>
        <div
          className={`nav-tab ${location.pathname === '/settings' ? 'active' : ''}`}
          onClick={() => navigate('/settings')}
        >
          Settings
        </div>
      </nav>
    </>
  )
}

export default Header
