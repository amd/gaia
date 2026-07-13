// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'

function PatientDatabase({ patients, stats, onMarkReviewed }) {
  const navigate = useNavigate()
  const [searchTerm, setSearchTerm] = useState('')
  const [activeFilter, setActiveFilter] = useState('all')
  const [selectedPatient, setSelectedPatient] = useState(null)
  const [showDetailPanel, setShowDetailPanel] = useState(false)

  // Filter patients based on search and filter
  const filteredPatients = patients.filter(patient => {
    const matchesSearch = !searchTerm ||
      `${patient.first_name} ${patient.last_name}`.toLowerCase().includes(searchTerm.toLowerCase()) ||
      patient.phone?.includes(searchTerm) ||
      patient.id?.toString().includes(searchTerm)

    const matchesFilter =
      activeFilter === 'all' ||
      (activeFilter === 'new' && patient.is_new_patient) ||
      (activeFilter === 'returning' && !patient.is_new_patient) ||
      (activeFilter === 'allergies' && patient.allergies && patient.allergies.toLowerCase() !== 'none' && patient.allergies !== 'N/A') ||
      (activeFilter === 'pending' && patient.changes_detected?.length > 0)

    return matchesSearch && matchesFilter
  })

  const openDetail = (patient) => {
    setSelectedPatient(patient)
    setShowDetailPanel(true)
  }

  const closeDetail = () => {
    setShowDetailPanel(false)
    setSelectedPatient(null)
  }

  const getInitials = (firstName, lastName) => {
    return `${(firstName || '')[0] || ''}${(lastName || '')[0] || ''}`.toUpperCase()
  }

  const getGenderClass = (gender) => {
    return gender?.toLowerCase() === 'female' ? 'female' : 'male'
  }

  const hasAllergies = (allergies) => {
    return allergies && allergies.toLowerCase() !== 'none' && allergies !== 'N/A' && allergies.trim() !== ''
  }

  return (
    <>
      <main className="main-content">
        {/* Stats Bar */}
        <div className="stats-bar">
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--info)' }}>{stats?.total_patients || patients.length}</div>
            <div className="stat-label">Total Patients</div>
          </div>
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--success-text)' }}>
              {patients.filter(p => !p.is_new_patient).length}
            </div>
            <div className="stat-label">Active Patients</div>
          </div>
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--warning)' }}>{stats?.new_patients || 0}</div>
            <div className="stat-label">New This Month</div>
          </div>
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--error)' }}>
              {patients.filter(p => hasAllergies(p.allergies)).length}
            </div>
            <div className="stat-label">With Allergies</div>
          </div>
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--purple)' }}>
              {patients.filter(p => p.changes_detected?.length > 0).length}
            </div>
            <div className="stat-label">Pending Review</div>
          </div>
        </div>

        {/* Toolbar */}
        <div className="toolbar">
          <div className="search-box">
            <span className="search-icon">&#128269;</span>
            <input
              type="text"
              className="search-input"
              placeholder="Search patients by name, ID, or phone..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <div className="filters">
            {[
              { key: 'all', label: 'All Patients' },
              { key: 'new', label: 'New' },
              { key: 'returning', label: 'Returning' },
              { key: 'allergies', label: 'With Allergies' },
              { key: 'pending', label: 'Pending' },
            ].map(filter => (
              <button
                key={filter.key}
                className={`filter-btn ${activeFilter === filter.key ? 'active' : ''}`}
                onClick={() => setActiveFilter(filter.key)}
              >
                {filter.label}
              </button>
            ))}
          </div>
          <button className="action-btn">
            <span>+</span> Add Patient
          </button>
        </div>

        {/* Table */}
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th className="sortable">Patient <span className="sort-icon">&#8593;</span></th>
                <th>Type</th>
                <th className="sortable">Status</th>
                <th>Allergies</th>
                <th>Insurance</th>
                <th className="sortable">Last Visit</th>
                <th className="sortable">Intake Date</th>
                <th>AI Time</th>
                <th>Time Saved</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredPatients.map(patient => (
                <tr key={patient.id} onClick={() => openDetail(patient)}>
                  <td>
                    <div className="patient-cell">
                      <div className={`patient-avatar ${getGenderClass(patient.gender)}`}>
                        {getInitials(patient.first_name, patient.last_name)}
                      </div>
                      <div className="patient-info">
                        <h4>{patient.first_name} {patient.last_name}</h4>
                        <span className="patient-id">PT-{patient.id?.toString().padStart(7, '0')}</span>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span className={`type-badge ${patient.is_new_patient ? 'new' : 'returning'}`}>
                      {patient.is_new_patient ? 'New' : 'Returning'}
                    </span>
                  </td>
                  <td>
                    <span className={`status-badge ${
                      patient.changes_detected?.length > 0 ? 'pending' : 'active'
                    }`}>
                      &#9679; {patient.changes_detected?.length > 0 ? 'Pending Review' : 'Active'}
                    </span>
                  </td>
                  <td>
                    <div className="allergy-indicator">
                      <span className={`allergy-dot ${hasAllergies(patient.allergies) ? 'critical' : 'none'}`}></span>
                      <span className={`allergy-text ${hasAllergies(patient.allergies) ? 'critical' : ''}`}>
                        {hasAllergies(patient.allergies) ? patient.allergies : 'None reported'}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="insurance-cell">
                      <span className="insurance-provider">{patient.insurance_provider || 'N/A'}</span>
                      <span className="insurance-id">{patient.insurance_policy_number || ''}</span>
                    </div>
                  </td>
                  <td>
                    <div className="date-cell">
                      <div className="date-primary">
                        {patient.created_at ? new Date(patient.created_at).toLocaleDateString() : 'N/A'}
                      </div>
                      <div className="date-secondary">
                        {patient.created_at ? new Date(patient.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="date-cell">
                      <div className="date-primary">
                        {patient.created_at ? new Date(patient.created_at).toLocaleDateString() : 'N/A'}
                      </div>
                      <div className="date-secondary">AI Processed</div>
                    </div>
                  </td>
                  <td>
                    <div className="processing-time-cell">
                      <span className="processing-value">
                        {patient.processing_time_seconds
                          ? `${patient.processing_time_seconds.toFixed(1)}s`
                          : 'N/A'}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="time-saved-cell">
                      {patient.estimated_manual_seconds && patient.processing_time_seconds ? (
                        <>
                          <span className="time-saved-value" style={{ color: 'var(--success-text)' }}>
                            {Math.round((patient.estimated_manual_seconds - patient.processing_time_seconds) / 60 * 10) / 10} min
                          </span>
                          <span className="time-saved-percent">
                            ({Math.round((1 - patient.processing_time_seconds / patient.estimated_manual_seconds) * 100)}%)
                          </span>
                        </>
                      ) : (
                        <span style={{ color: 'var(--text-secondary)' }}>N/A</span>
                      )}
                    </div>
                  </td>
                  <td>
                    <div className="actions-cell" onClick={(e) => e.stopPropagation()}>
                      <div
                        className="action-icon view"
                        title="View Details"
                        onClick={() => openDetail(patient)}
                      >
                        &#128065;
                      </div>
                      <div
                        className="action-icon edit"
                        title="Edit"
                        onClick={() => navigate(`/patient/${patient.id}`)}
                      >
                        &#9998;
                      </div>
                      {patient.changes_detected?.length > 0 && (
                        <div
                          className="action-icon approve"
                          title="Approve Changes"
                          onClick={() => onMarkReviewed && onMarkReviewed(patient.id)}
                          style={{ color: 'var(--success-text)' }}
                        >
                          &#10003;
                        </div>
                      )}
                      {hasAllergies(patient.allergies) && (
                        <div className="action-icon alert" title="Allergy Alert">&#128276;</div>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {filteredPatients.length === 0 && (
            <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
              No patients found matching your criteria.
            </div>
          )}

          {/* Pagination */}
          <div className="pagination">
            <div className="pagination-info">
              Showing <strong>1-{filteredPatients.length}</strong> of <strong>{patients.length}</strong> patients
            </div>
            <div className="pagination-controls">
              <button className="page-btn" disabled>&#8592;</button>
              <button className="page-btn active">1</button>
              <button className="page-btn">&#8594;</button>
            </div>
          </div>
        </div>
      </main>

      {/* Overlay */}
      <div
        className={`overlay ${showDetailPanel ? 'open' : ''}`}
        onClick={closeDetail}
      ></div>

      {/* Detail Panel */}
      <div className={`detail-panel ${showDetailPanel ? 'open' : ''}`}>
        <div className="detail-header">
          <span className="detail-title">Patient Details</span>
          <button className="close-btn" onClick={closeDetail}>&#10005;</button>
        </div>
        {selectedPatient && (
          <div className="detail-content">
            <div className="detail-section">
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
                <div
                  className={`patient-avatar ${getGenderClass(selectedPatient.gender)}`}
                  style={{ width: '60px', height: '60px', fontSize: '1.25rem' }}
                >
                  {getInitials(selectedPatient.first_name, selectedPatient.last_name)}
                </div>
                <div>
                  <h3 style={{ fontSize: '1.25rem', marginBottom: '0.25rem' }}>
                    {selectedPatient.first_name} {selectedPatient.last_name}
                  </h3>
                  <span className="patient-id" style={{ fontSize: '0.85rem' }}>
                    PT-{selectedPatient.id?.toString().padStart(7, '0')}
                  </span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                <span className={`type-badge ${selectedPatient.is_new_patient ? 'new' : 'returning'}`}>
                  {selectedPatient.is_new_patient ? 'New Patient' : 'Returning'}
                </span>
                <span className={`status-badge ${selectedPatient.changes_detected?.length > 0 ? 'pending' : 'active'}`}>
                  &#9679; {selectedPatient.changes_detected?.length > 0 ? 'Pending Review' : 'Active'}
                </span>
              </div>
            </div>

            <div className="detail-section">
              <div className="detail-section-title">Personal Information</div>
              <div className="detail-field">
                <span className="detail-label">Date of Birth</span>
                <span className="detail-value">{selectedPatient.date_of_birth || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Gender</span>
                <span className="detail-value">{selectedPatient.gender || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Phone</span>
                <span className="detail-value">{selectedPatient.phone || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Email</span>
                <span className="detail-value">{selectedPatient.email || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Address</span>
                <span className="detail-value" style={{ textAlign: 'right' }}>
                  {selectedPatient.address || 'N/A'}
                  {selectedPatient.city && <><br />{selectedPatient.city}, {selectedPatient.state} {selectedPatient.zip_code}</>}
                </span>
              </div>
            </div>

            {hasAllergies(selectedPatient.allergies) && (
              <div className="detail-section">
                <div className="detail-section-title">&#9888; Allergies</div>
                <div className="allergy-list">
                  {selectedPatient.allergies.split(',').map((allergy, idx) => (
                    <span key={idx} className="allergy-tag">{allergy.trim()}</span>
                  ))}
                </div>
              </div>
            )}

            <div className="detail-section">
              <div className="detail-section-title">Insurance Information</div>
              <div className="detail-field">
                <span className="detail-label">Provider</span>
                <span className="detail-value">{selectedPatient.insurance_provider || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Policy Number</span>
                <span className="detail-value">{selectedPatient.insurance_policy_number || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Group Number</span>
                <span className="detail-value">{selectedPatient.insurance_group_number || 'N/A'}</span>
              </div>
            </div>

            <div className="detail-section">
              <div className="detail-section-title">Emergency Contact</div>
              <div className="detail-field">
                <span className="detail-label">Name</span>
                <span className="detail-value">{selectedPatient.emergency_contact_name || 'N/A'}</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Phone</span>
                <span className="detail-value">{selectedPatient.emergency_contact_phone || 'N/A'}</span>
              </div>
            </div>

            <div className="detail-section">
              <div className="detail-section-title">Intake Processing</div>
              <div className="detail-field">
                <span className="detail-label">Method</span>
                <span className="detail-value">AI Processed (VLM)</span>
              </div>
              <div className="detail-field">
                <span className="detail-label">AI Processing Time</span>
                <span className="detail-value">
                  {selectedPatient.processing_time_seconds
                    ? `${selectedPatient.processing_time_seconds.toFixed(1)} seconds`
                    : 'N/A'}
                </span>
              </div>
              <div className="detail-field">
                <span className="detail-label">Est. Manual Entry</span>
                <span className="detail-value">
                  {selectedPatient.estimated_manual_seconds
                    ? `${Math.round(selectedPatient.estimated_manual_seconds / 60 * 10) / 10} minutes`
                    : 'N/A'}
                </span>
              </div>
              {selectedPatient.estimated_manual_seconds && selectedPatient.processing_time_seconds && (
                <div className="detail-field">
                  <span className="detail-label">Time Saved</span>
                  <span className="detail-value" style={{ color: 'var(--success-text)', fontWeight: '600' }}>
                    {Math.round((selectedPatient.estimated_manual_seconds - selectedPatient.processing_time_seconds) / 60 * 10) / 10} min
                    ({Math.round((1 - selectedPatient.processing_time_seconds / selectedPatient.estimated_manual_seconds) * 100)}% faster)
                  </span>
                </div>
              )}
              <div className="detail-field">
                <span className="detail-label">Source File</span>
                <span className="detail-value" style={{ fontFamily: 'monospace' }}>
                  {selectedPatient.source_file || 'N/A'}
                </span>
              </div>
            </div>

            {/* Changes Detected Section */}
            {selectedPatient.changes_detected?.length > 0 && (
              <div className="detail-section" style={{ background: 'rgba(255, 193, 7, 0.1)', borderRadius: '8px', padding: '1rem', marginTop: '1rem' }}>
                <div className="detail-section-title" style={{ color: 'var(--warning)' }}>
                  &#9888; Pending Review ({selectedPatient.changes_detected.length} changes)
                </div>
                <div style={{ fontSize: '0.85rem', marginBottom: '0.75rem' }}>
                  {selectedPatient.changes_detected.slice(0, 3).map((change, idx) => (
                    <div key={idx} style={{ marginBottom: '0.5rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      <span style={{ fontWeight: '500' }}>{change.field}:</span>
                      <span style={{ color: 'var(--text-secondary)', textDecoration: 'line-through' }}>{change.old_value || 'N/A'}</span>
                      <span>&#8594;</span>
                      <span style={{ color: 'var(--success-text)' }}>{change.new_value}</span>
                    </div>
                  ))}
                  {selectedPatient.changes_detected.length > 3 && (
                    <div style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                      +{selectedPatient.changes_detected.length - 3} more changes
                    </div>
                  )}
                </div>
                <button
                  className="btn btn-approve"
                  style={{ width: '100%' }}
                  onClick={() => {
                    onMarkReviewed && onMarkReviewed(selectedPatient.id)
                    // Update local state to reflect change
                    setSelectedPatient({ ...selectedPatient, changes_detected: null })
                  }}
                >
                  &#10003; Approve Changes
                </button>
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
              <button
                className="action-btn"
                style={{ flex: 1 }}
                onClick={() => navigate(`/patient/${selectedPatient.id}`)}
              >
                View Full Details
              </button>
              {selectedPatient.source_file && (
                <a
                  href={`/api/patients/${selectedPatient.id}/file`}
                  className="filter-btn"
                  style={{ flex: 1, textAlign: 'center', textDecoration: 'none' }}
                >
                  Download Form
                </a>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  )
}

export default PatientDatabase
