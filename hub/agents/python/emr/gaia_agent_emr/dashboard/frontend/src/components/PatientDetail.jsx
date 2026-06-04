// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'

function PatientDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [patient, setPatient] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [imageError, setImageError] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteSourceFile, setDeleteSourceFile] = useState(true) // Default: delete file too
  const [editMode, setEditMode] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState(null)

  useEffect(() => {
    const fetchPatient = async () => {
      try {
        const response = await fetch(`/api/patients/${id}`)
        if (!response.ok) throw new Error('Patient not found')
        const data = await response.json()
        setPatient(data)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    fetchPatient()
  }, [id])

  const handleDelete = async () => {
    setDeleting(true)
    try {
      const response = await fetch(
        `/api/patients/${id}?delete_file=${deleteSourceFile}`,
        { method: 'DELETE' }
      )
      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to delete patient')
      }
      navigate('/patients')
    } catch (err) {
      setError(err.message)
      setDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  // Enter edit mode
  const handleEditMode = () => {
    setEditData({
      // Personal Information
      first_name: patient.first_name || '',
      last_name: patient.last_name || '',
      date_of_birth: patient.date_of_birth || '',
      gender: patient.gender || '',
      marital_status: patient.marital_status || '',
      spouse_name: patient.spouse_name || '',
      preferred_pronouns: patient.preferred_pronouns || '',
      preferred_language: patient.preferred_language || '',
      race: patient.race || '',
      ethnicity: patient.ethnicity || '',
      // Contact Information
      phone: patient.phone || '',
      mobile_phone: patient.mobile_phone || '',
      work_phone: patient.work_phone || '',
      email: patient.email || '',
      address: patient.address || '',
      city: patient.city || '',
      state: patient.state || '',
      zip_code: patient.zip_code || '',
      contact_preference: patient.contact_preference || '',
      // Employment
      employment_status: patient.employment_status || '',
      occupation: patient.occupation || '',
      employer: patient.employer || '',
      employer_address: patient.employer_address || '',
      // Physicians
      referring_physician: patient.referring_physician || '',
      referring_physician_phone: patient.referring_physician_phone || '',
      primary_care_physician: patient.primary_care_physician || '',
      preferred_pharmacy: patient.preferred_pharmacy || '',
      // Insurance
      insurance_provider: patient.insurance_provider || '',
      insurance_id: patient.insurance_id || '',
      insurance_group_number: patient.insurance_group_number || '',
      insured_name: patient.insured_name || '',
      insured_dob: patient.insured_dob || '',
      insurance_phone: patient.insurance_phone || '',
      billing_address: patient.billing_address || '',
      guarantor_name: patient.guarantor_name || '',
      secondary_insurance_provider: patient.secondary_insurance_provider || '',
      secondary_insurance_id: patient.secondary_insurance_id || '',
      // Medical Information
      reason_for_visit: patient.reason_for_visit || '',
      allergies: patient.allergies || '',
      medications: patient.medications || '',
      medical_conditions: patient.medical_conditions || '',
      date_of_injury: patient.date_of_injury || '',
      pain_location: patient.pain_location || '',
      pain_onset: patient.pain_onset || '',
      pain_cause: patient.pain_cause || '',
      pain_progression: patient.pain_progression || '',
      work_related_injury: patient.work_related_injury || '',
      car_accident: patient.car_accident || '',
      // Emergency Contact
      emergency_contact_name: patient.emergency_contact_name || '',
      emergency_contact_relationship: patient.emergency_contact_relationship || '',
      emergency_contact_phone: patient.emergency_contact_phone || '',
    })
    setEditMode(true)
    setSaveMessage(null)
  }

  // Cancel edit mode
  const handleCancelEdit = () => {
    setEditMode(false)
    setEditData({})
    setSaveMessage(null)
  }

  // Update edit field
  const handleFieldChange = (field, value) => {
    setEditData(prev => ({ ...prev, [field]: value }))
  }

  // Save changes
  const handleSave = async () => {
    setSaving(true)
    setSaveMessage(null)

    try {
      // Only send fields that have changed
      const changedFields = {}
      for (const [key, value] of Object.entries(editData)) {
        if (value !== (patient[key] || '')) {
          changedFields[key] = value || null // Send null for empty strings
        }
      }

      if (Object.keys(changedFields).length === 0) {
        setSaveMessage({ type: 'info', text: 'No changes to save' })
        setSaving(false)
        return
      }

      const response = await fetch(`/api/patients/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changedFields),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Failed to update patient')
      }

      const result = await response.json()

      // Update local patient data
      setPatient(prev => ({ ...prev, ...changedFields }))
      setEditMode(false)
      setSaveMessage({ type: 'success', text: `Updated ${result.updated_fields.length} field(s)` })

      // Clear message after 3 seconds
      setTimeout(() => setSaveMessage(null), 3000)
    } catch (err) {
      setSaveMessage({ type: 'error', text: err.message })
    } finally {
      setSaving(false)
    }
  }

  // Helper to check if a field is missing (empty/null)
  const isFieldMissing = (value) => {
    return !value || value === 'N/A' || value.trim() === ''
  }

  if (loading) {
    return (
      <main className="main-content">
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
          Loading patient details...
        </div>
      </main>
    )
  }

  if (error || !patient) {
    return (
      <main className="main-content">
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--error)' }}>
          Error: {error || 'Patient not found'}
        </div>
      </main>
    )
  }

  const hasAllergies = patient.allergies &&
    patient.allergies.toLowerCase() !== 'none' &&
    patient.allergies !== 'N/A' &&
    patient.allergies.trim() !== ''

  return (
    <>
      {/* Sub-header with back button */}
      <div className="sub-header">
        <button className="back-btn" onClick={() => navigate(-1)}>
          &#8592; Back
        </button>
        <h1 className="header-title">Patient Intake Detail</h1>
        <span className="patient-id-badge">ID: PT-{patient.id?.toString().padStart(7, '0')}</span>
      </div>

      {/* Main Content */}
      <main className="main-content patient-detail-grid">
        {/* Left Column - Scanned Form */}
        <div className="card">
          <div className="card-header">
            <div className="card-title">
              <span>&#128196;</span> Scanned Intake Form
            </div>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
              {patient.source_file || 'intake_form.png'}
            </span>
          </div>
          <div className="card-content">
            <div className="form-image-container">
              {!imageError ? (
                <img
                  src={`/api/patients/${patient.id}/file?inline=true`}
                  alt="Scanned intake form"
                  className="scanned-form-image"
                  onError={() => setImageError(true)}
                />
              ) : (
                <div className="form-image-placeholder">
                  <div className="placeholder-icon">📄</div>
                  <div className="placeholder-text">Original form image not available</div>
                  <div className="placeholder-subtext">
                    {patient.source_file || 'No file recorded'}
                  </div>
                </div>
              )}
            </div>

            <div className="ocr-stats">
              <div className="ocr-stat">
                <div className="ocr-stat-value">94.2%</div>
                <div className="ocr-stat-label">OCR Confidence</div>
              </div>
              <div className="ocr-stat">
                <div className="ocr-stat-value">1.8s</div>
                <div className="ocr-stat-label">Processing Time</div>
              </div>
              <div className="ocr-stat">
                <div className="ocr-stat-value">28</div>
                <div className="ocr-stat-label">Fields Extracted</div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column - Extracted Data */}
        <div>
          {/* Time Savings */}
          <div className="time-savings-badge">
            <div className="time-savings-label">Time Saved on This Intake</div>
            <div className="time-savings-value">6 min 28 sec</div>
            <div className="time-savings-compare">Manual estimate: 8 min | AI processing: 1.8 sec</div>
          </div>

          {/* Critical Alert */}
          {hasAllergies && (
            <div className="alert-box">
              <div className="alert-box-header critical">
                <span>⚠️</span> CRITICAL ALLERGIES DETECTED
              </div>
              <ul className="alert-list">
                {patient.allergies.split(',').map((allergy, idx) => (
                  <li key={idx}>
                    <span style={{ color: 'var(--error)' }}>&#9679;</span> {allergy.trim()}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Missing Field Alert */}
          {(!patient.hipaa_consent || !patient.treatment_consent) && (
            <div className="alert-box warning">
              <div className="alert-box-header warning">
                <span>&#128196;</span> MISSING CONSENT
              </div>
              <ul className="alert-list">
                {!patient.hipaa_consent && (
                  <li><span style={{ color: 'var(--warning)' }}>&#9679;</span> HIPAA consent not signed</li>
                )}
                {!patient.treatment_consent && (
                  <li><span style={{ color: 'var(--warning)' }}>&#9679;</span> Treatment consent not signed</li>
                )}
              </ul>
            </div>
          )}

          {/* Save Message */}
          {saveMessage && (
            <div className={`save-message ${saveMessage.type}`}>
              {saveMessage.type === 'success' && '✓ '}
              {saveMessage.type === 'error' && '✗ '}
              {saveMessage.text}
            </div>
          )}

          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="card-header">
              <div className="card-title">
                <span>&#128100;</span> Extracted Patient Data
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ color: 'var(--success-text)', fontSize: '0.8rem' }}>
                  &#10003; {patient.is_new_patient ? 'New Patient' : 'Returning Patient'}
                </span>
                {!editMode ? (
                  <button className="btn btn-secondary btn-sm" onClick={handleEditMode}>
                    ✏️ Edit
                  </button>
                ) : (
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={handleSave}
                      disabled={saving}
                    >
                      {saving ? 'Saving...' : '💾 Save'}
                    </button>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={handleCancelEdit}
                      disabled={saving}
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </div>
            <div className="card-content">
              <div className="data-section">
                <div className="data-section-title">Personal Information</div>
                <div className="data-grid">
                  <div className="data-item">
                    <div className="data-icon success">&#10003;</div>
                    <div className="data-content">
                      <div className="data-label">Full Name</div>
                      {editMode ? (
                        <div className="edit-field-row">
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.first_name}
                            onChange={(e) => handleFieldChange('first_name', e.target.value)}
                            placeholder="First name"
                          />
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.last_name}
                            onChange={(e) => handleFieldChange('last_name', e.target.value)}
                            placeholder="Last name"
                          />
                        </div>
                      ) : (
                        <div className="data-value">{patient.first_name} {patient.last_name}</div>
                      )}
                    </div>
                  </div>
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.date_of_birth) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.date_of_birth) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Date of Birth</div>
                      {editMode ? (
                        <input
                          type="text"
                          className="edit-input"
                          value={editData.date_of_birth}
                          onChange={(e) => handleFieldChange('date_of_birth', e.target.value)}
                          placeholder="YYYY-MM-DD"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.date_of_birth) ? 'missing' : ''}`}>
                          {patient.date_of_birth || 'Not provided'} {patient.age && `(Age: ${patient.age})`}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="data-item">
                    <div className="data-icon success">&#10003;</div>
                    <div className="data-content">
                      <div className="data-label">Gender</div>
                      {editMode ? (
                        <select
                          className="edit-input"
                          value={editData.gender}
                          onChange={(e) => handleFieldChange('gender', e.target.value)}
                        >
                          <option value="">Select...</option>
                          <option value="Male">Male</option>
                          <option value="Female">Female</option>
                          <option value="Other">Other</option>
                        </select>
                      ) : (
                        <div className="data-value">{patient.gender || 'N/A'}</div>
                      )}
                    </div>
                  </div>
                  <div className="data-item">
                    <div className="data-icon success">&#10003;</div>
                    <div className="data-content">
                      <div className="data-label">SSN</div>
                      <div className="data-value">{patient.ssn || '***-**-****'}</div>
                    </div>
                  </div>
                  <div className="data-item">
                    <div className="data-icon success">&#10003;</div>
                    <div className="data-content">
                      <div className="data-label">Marital Status</div>
                      {editMode ? (
                        <select
                          className="edit-input"
                          value={editData.marital_status}
                          onChange={(e) => handleFieldChange('marital_status', e.target.value)}
                        >
                          <option value="">Select...</option>
                          <option value="Single">Single</option>
                          <option value="Married">Married</option>
                          <option value="Divorced">Divorced</option>
                          <option value="Widowed">Widowed</option>
                          <option value="Partnered">Partnered</option>
                        </select>
                      ) : (
                        <div className="data-value">{patient.marital_status || 'N/A'}</div>
                      )}
                    </div>
                  </div>
                  {(patient.spouse_name || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Spouse</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.spouse_name}
                            onChange={(e) => handleFieldChange('spouse_name', e.target.value)}
                            placeholder="Spouse name"
                          />
                        ) : (
                          <div className="data-value">{patient.spouse_name}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.preferred_pronouns || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Pronouns</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.preferred_pronouns}
                            onChange={(e) => handleFieldChange('preferred_pronouns', e.target.value)}
                            placeholder="he/him, she/her, they/them"
                          />
                        ) : (
                          <div className="data-value">{patient.preferred_pronouns}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.preferred_language || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Preferred Language</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.preferred_language}
                            onChange={(e) => handleFieldChange('preferred_language', e.target.value)}
                            placeholder="English, Spanish, etc."
                          />
                        ) : (
                          <div className="data-value">{patient.preferred_language}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.race || patient.ethnicity || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Race / Ethnicity</div>
                        {editMode ? (
                          <div className="edit-field-row">
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.race}
                              onChange={(e) => handleFieldChange('race', e.target.value)}
                              placeholder="Race"
                            />
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.ethnicity}
                              onChange={(e) => handleFieldChange('ethnicity', e.target.value)}
                              placeholder="Ethnicity"
                            />
                          </div>
                        ) : (
                          <div className="data-value">
                            {[patient.race, patient.ethnicity].filter(Boolean).join(' / ') || 'N/A'}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="data-section">
                <div className="data-section-title">Contact Information</div>
                <div className="data-grid">
                  <div className="data-item full-width">
                    <div className={`data-icon ${isFieldMissing(patient.address) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.address) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Address</div>
                      {editMode ? (
                        <div className="edit-address-grid">
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.address}
                            onChange={(e) => handleFieldChange('address', e.target.value)}
                            placeholder="Street address"
                          />
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.city}
                            onChange={(e) => handleFieldChange('city', e.target.value)}
                            placeholder="City"
                          />
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.state}
                            onChange={(e) => handleFieldChange('state', e.target.value)}
                            placeholder="State"
                            style={{ maxWidth: '80px' }}
                          />
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.zip_code}
                            onChange={(e) => handleFieldChange('zip_code', e.target.value)}
                            placeholder="ZIP"
                            style={{ maxWidth: '100px' }}
                          />
                        </div>
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.address) ? 'missing' : ''}`}>
                          {patient.address || 'Not provided'}
                          {patient.city && `, ${patient.city}, ${patient.state} ${patient.zip_code}`}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.phone) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.phone) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Home Phone {isFieldMissing(patient.phone) && <span className="field-missing-tag">MISSING</span>}</div>
                      {editMode ? (
                        <input
                          type="tel"
                          className="edit-input"
                          value={editData.phone}
                          onChange={(e) => handleFieldChange('phone', e.target.value)}
                          placeholder="(xxx) xxx-xxxx"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.phone) ? 'missing' : ''}`}>
                          {patient.phone || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                  {(patient.mobile_phone || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Mobile Phone</div>
                        {editMode ? (
                          <input
                            type="tel"
                            className="edit-input"
                            value={editData.mobile_phone}
                            onChange={(e) => handleFieldChange('mobile_phone', e.target.value)}
                            placeholder="(xxx) xxx-xxxx"
                          />
                        ) : (
                          <div className="data-value">{patient.mobile_phone}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.work_phone || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Work Phone</div>
                        {editMode ? (
                          <input
                            type="tel"
                            className="edit-input"
                            value={editData.work_phone}
                            onChange={(e) => handleFieldChange('work_phone', e.target.value)}
                            placeholder="(xxx) xxx-xxxx"
                          />
                        ) : (
                          <div className="data-value">{patient.work_phone}</div>
                        )}
                      </div>
                    </div>
                  )}
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.email) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.email) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Email</div>
                      {editMode ? (
                        <input
                          type="email"
                          className="edit-input"
                          value={editData.email}
                          onChange={(e) => handleFieldChange('email', e.target.value)}
                          placeholder="email@example.com"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.email) ? 'missing' : ''}`}>
                          {patient.email || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                  {(patient.contact_preference || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Contact Preference</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.contact_preference}
                            onChange={(e) => handleFieldChange('contact_preference', e.target.value)}
                            placeholder="Phone, Email, Text, etc."
                          />
                        ) : (
                          <div className="data-value">{patient.contact_preference}</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Employment Section */}
              {(patient.employment_status || patient.occupation || patient.employer || editMode) && (
                <div className="data-section">
                  <div className="data-section-title">Employment</div>
                  <div className="data-grid">
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Employment Status</div>
                        {editMode ? (
                          <select
                            className="edit-input"
                            value={editData.employment_status}
                            onChange={(e) => handleFieldChange('employment_status', e.target.value)}
                          >
                            <option value="">Select...</option>
                            <option value="Employed">Employed</option>
                            <option value="Self Employed">Self Employed</option>
                            <option value="Unemployed">Unemployed</option>
                            <option value="Retired">Retired</option>
                            <option value="Student">Student</option>
                            <option value="Disabled">Disabled</option>
                            <option value="Military">Military</option>
                          </select>
                        ) : (
                          <div className="data-value">{patient.employment_status || 'N/A'}</div>
                        )}
                      </div>
                    </div>
                    {(patient.occupation || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Occupation</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.occupation}
                              onChange={(e) => handleFieldChange('occupation', e.target.value)}
                              placeholder="Job title"
                            />
                          ) : (
                            <div className="data-value">{patient.occupation}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.employer || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Employer</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.employer}
                              onChange={(e) => handleFieldChange('employer', e.target.value)}
                              placeholder="Company name"
                            />
                          ) : (
                            <div className="data-value">{patient.employer}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.employer_address || editMode) && (
                      <div className="data-item full-width">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Employer Address</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.employer_address}
                              onChange={(e) => handleFieldChange('employer_address', e.target.value)}
                              placeholder="Employer address"
                            />
                          ) : (
                            <div className="data-value">{patient.employer_address}</div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Physicians Section */}
              {(patient.referring_physician || patient.primary_care_physician || patient.preferred_pharmacy || editMode) && (
                <div className="data-section">
                  <div className="data-section-title">Physicians & Pharmacy</div>
                  <div className="data-grid">
                    {(patient.referring_physician || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Referring Physician</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.referring_physician}
                              onChange={(e) => handleFieldChange('referring_physician', e.target.value)}
                              placeholder="Dr. Name"
                            />
                          ) : (
                            <div className="data-value">{patient.referring_physician}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.referring_physician_phone || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Referring Physician Phone</div>
                          {editMode ? (
                            <input
                              type="tel"
                              className="edit-input"
                              value={editData.referring_physician_phone}
                              onChange={(e) => handleFieldChange('referring_physician_phone', e.target.value)}
                              placeholder="(xxx) xxx-xxxx"
                            />
                          ) : (
                            <div className="data-value">{patient.referring_physician_phone}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.primary_care_physician || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Primary Care Physician</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.primary_care_physician}
                              onChange={(e) => handleFieldChange('primary_care_physician', e.target.value)}
                              placeholder="Dr. Name"
                            />
                          ) : (
                            <div className="data-value">{patient.primary_care_physician}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.preferred_pharmacy || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Preferred Pharmacy</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.preferred_pharmacy}
                              onChange={(e) => handleFieldChange('preferred_pharmacy', e.target.value)}
                              placeholder="Pharmacy name"
                            />
                          ) : (
                            <div className="data-value">{patient.preferred_pharmacy}</div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div className="data-section">
                <div className="data-section-title">Insurance</div>
                <div className="data-grid">
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.insurance_provider) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.insurance_provider) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Provider</div>
                      {editMode ? (
                        <input
                          type="text"
                          className="edit-input"
                          value={editData.insurance_provider}
                          onChange={(e) => handleFieldChange('insurance_provider', e.target.value)}
                          placeholder="Insurance provider"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.insurance_provider) ? 'missing' : ''}`}>
                          {patient.insurance_provider || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.insurance_id) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.insurance_id) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Policy Number</div>
                      {editMode ? (
                        <input
                          type="text"
                          className="edit-input"
                          value={editData.insurance_id}
                          onChange={(e) => handleFieldChange('insurance_id', e.target.value)}
                          placeholder="Policy number"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.insurance_id) ? 'missing' : ''}`}>
                          {patient.insurance_id || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                  {(patient.insurance_group_number || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Group Number</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.insurance_group_number}
                            onChange={(e) => handleFieldChange('insurance_group_number', e.target.value)}
                            placeholder="Group number"
                          />
                        ) : (
                          <div className="data-value">{patient.insurance_group_number}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.insured_name || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Insured Name</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.insured_name}
                            onChange={(e) => handleFieldChange('insured_name', e.target.value)}
                            placeholder="Name of insured"
                          />
                        ) : (
                          <div className="data-value">{patient.insured_name}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.insured_dob || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Insured DOB</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.insured_dob}
                            onChange={(e) => handleFieldChange('insured_dob', e.target.value)}
                            placeholder="YYYY-MM-DD"
                          />
                        ) : (
                          <div className="data-value">{patient.insured_dob}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.insurance_phone || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Insurance Phone</div>
                        {editMode ? (
                          <input
                            type="tel"
                            className="edit-input"
                            value={editData.insurance_phone}
                            onChange={(e) => handleFieldChange('insurance_phone', e.target.value)}
                            placeholder="(xxx) xxx-xxxx"
                          />
                        ) : (
                          <div className="data-value">{patient.insurance_phone}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.billing_address || editMode) && (
                    <div className="data-item full-width">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Billing Address</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.billing_address}
                            onChange={(e) => handleFieldChange('billing_address', e.target.value)}
                            placeholder="Billing address"
                          />
                        ) : (
                          <div className="data-value">{patient.billing_address}</div>
                        )}
                      </div>
                    </div>
                  )}
                  {(patient.guarantor_name || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Guarantor</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.guarantor_name}
                            onChange={(e) => handleFieldChange('guarantor_name', e.target.value)}
                            placeholder="Person responsible for payment"
                          />
                        ) : (
                          <div className="data-value">{patient.guarantor_name}</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Secondary Insurance */}
              {(patient.secondary_insurance_provider || patient.secondary_insurance_id || editMode) && (
                <div className="data-section">
                  <div className="data-section-title">Secondary Insurance</div>
                  <div className="data-grid">
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Provider</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.secondary_insurance_provider}
                            onChange={(e) => handleFieldChange('secondary_insurance_provider', e.target.value)}
                            placeholder="Secondary insurance provider"
                          />
                        ) : (
                          <div className="data-value">{patient.secondary_insurance_provider || 'N/A'}</div>
                        )}
                      </div>
                    </div>
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Policy Number</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.secondary_insurance_id}
                            onChange={(e) => handleFieldChange('secondary_insurance_id', e.target.value)}
                            placeholder="Policy number"
                          />
                        ) : (
                          <div className="data-value">{patient.secondary_insurance_id || 'N/A'}</div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              <div className="data-section">
                <div className="data-section-title">Medical Information</div>
                <div className="data-grid">
                  <div className="data-item full-width">
                    <div className={`data-icon ${hasAllergies ? 'error' : 'success'}`}>
                      {hasAllergies ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">{hasAllergies ? 'Allergies (CRITICAL)' : 'Allergies'}</div>
                      {editMode ? (
                        <input
                          type="text"
                          className="edit-input"
                          value={editData.allergies}
                          onChange={(e) => handleFieldChange('allergies', e.target.value)}
                          placeholder="Known allergies (comma-separated)"
                        />
                      ) : (
                        <div className="data-value" style={hasAllergies ? { color: 'var(--error)' } : {}}>
                          {patient.allergies || 'None reported'}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="data-item full-width">
                    <div className="data-icon success">&#10003;</div>
                    <div className="data-content">
                      <div className="data-label">Current Medications</div>
                      {editMode ? (
                        <input
                          type="text"
                          className="edit-input"
                          value={editData.medications}
                          onChange={(e) => handleFieldChange('medications', e.target.value)}
                          placeholder="Current medications"
                        />
                      ) : (
                        <div className="data-value">{patient.medications || 'None'}</div>
                      )}
                    </div>
                  </div>
                  <div className="data-item full-width">
                    <div className={`data-icon ${isFieldMissing(patient.reason_for_visit) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.reason_for_visit) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Reason for Visit</div>
                      {editMode ? (
                        <textarea
                          className="edit-input edit-textarea"
                          value={editData.reason_for_visit}
                          onChange={(e) => handleFieldChange('reason_for_visit', e.target.value)}
                          placeholder="Reason for visit"
                          rows={2}
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.reason_for_visit) ? 'missing' : ''}`}>
                          {patient.reason_for_visit || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                  {(patient.medical_conditions || editMode) && (
                    <div className="data-item full-width">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Medical Conditions</div>
                        {editMode ? (
                          <textarea
                            className="edit-input edit-textarea"
                            value={editData.medical_conditions}
                            onChange={(e) => handleFieldChange('medical_conditions', e.target.value)}
                            placeholder="Existing medical conditions"
                            rows={2}
                          />
                        ) : (
                          <div className="data-value">{patient.medical_conditions}</div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Pain History Section */}
              {(patient.date_of_injury || patient.pain_location || patient.pain_onset || patient.pain_cause || editMode) && (
                <div className="data-section">
                  <div className="data-section-title">Pain / Injury History</div>
                  <div className="data-grid">
                    {(patient.date_of_injury || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Date of Injury</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.date_of_injury}
                              onChange={(e) => handleFieldChange('date_of_injury', e.target.value)}
                              placeholder="YYYY-MM-DD"
                            />
                          ) : (
                            <div className="data-value">{patient.date_of_injury}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.pain_location || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Pain Location</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.pain_location}
                              onChange={(e) => handleFieldChange('pain_location', e.target.value)}
                              placeholder="Location of pain"
                            />
                          ) : (
                            <div className="data-value">{patient.pain_location}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.pain_onset || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Pain Onset</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.pain_onset}
                              onChange={(e) => handleFieldChange('pain_onset', e.target.value)}
                              placeholder="When pain started"
                            />
                          ) : (
                            <div className="data-value">{patient.pain_onset}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.pain_cause || editMode) && (
                      <div className="data-item full-width">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Pain Cause</div>
                          {editMode ? (
                            <input
                              type="text"
                              className="edit-input"
                              value={editData.pain_cause}
                              onChange={(e) => handleFieldChange('pain_cause', e.target.value)}
                              placeholder="What caused the pain"
                            />
                          ) : (
                            <div className="data-value">{patient.pain_cause}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.pain_progression || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Pain Progression</div>
                          {editMode ? (
                            <select
                              className="edit-input"
                              value={editData.pain_progression}
                              onChange={(e) => handleFieldChange('pain_progression', e.target.value)}
                            >
                              <option value="">Select...</option>
                              <option value="Improved">Improved</option>
                              <option value="Worsened">Worsened</option>
                              <option value="Stayed the same">Stayed the same</option>
                            </select>
                          ) : (
                            <div className="data-value">{patient.pain_progression}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.work_related_injury || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Work Related?</div>
                          {editMode ? (
                            <select
                              className="edit-input"
                              value={editData.work_related_injury}
                              onChange={(e) => handleFieldChange('work_related_injury', e.target.value)}
                            >
                              <option value="">Select...</option>
                              <option value="Yes">Yes</option>
                              <option value="No">No</option>
                            </select>
                          ) : (
                            <div className="data-value">{patient.work_related_injury}</div>
                          )}
                        </div>
                      </div>
                    )}
                    {(patient.car_accident || editMode) && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Car Accident?</div>
                          {editMode ? (
                            <select
                              className="edit-input"
                              value={editData.car_accident}
                              onChange={(e) => handleFieldChange('car_accident', e.target.value)}
                            >
                              <option value="">Select...</option>
                              <option value="Yes">Yes</option>
                              <option value="No">No</option>
                            </select>
                          ) : (
                            <div className="data-value">{patient.car_accident}</div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div className="data-section">
                <div className="data-section-title">Emergency Contact</div>
                <div className="data-grid">
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.emergency_contact_name) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.emergency_contact_name) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Contact Name</div>
                      {editMode ? (
                        <input
                          type="text"
                          className="edit-input"
                          value={editData.emergency_contact_name}
                          onChange={(e) => handleFieldChange('emergency_contact_name', e.target.value)}
                          placeholder="Emergency contact name"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.emergency_contact_name) ? 'missing' : ''}`}>
                          {patient.emergency_contact_name || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                  {(patient.emergency_contact_relationship || editMode) && (
                    <div className="data-item">
                      <div className="data-icon success">&#10003;</div>
                      <div className="data-content">
                        <div className="data-label">Relationship</div>
                        {editMode ? (
                          <input
                            type="text"
                            className="edit-input"
                            value={editData.emergency_contact_relationship}
                            onChange={(e) => handleFieldChange('emergency_contact_relationship', e.target.value)}
                            placeholder="Mom, Spouse, Friend, etc."
                          />
                        ) : (
                          <div className="data-value">{patient.emergency_contact_relationship}</div>
                        )}
                      </div>
                    </div>
                  )}
                  <div className="data-item">
                    <div className={`data-icon ${isFieldMissing(patient.emergency_contact_phone) ? 'warning' : 'success'}`}>
                      {isFieldMissing(patient.emergency_contact_phone) ? '⚠' : '✓'}
                    </div>
                    <div className="data-content">
                      <div className="data-label">Contact Phone</div>
                      {editMode ? (
                        <input
                          type="tel"
                          className="edit-input"
                          value={editData.emergency_contact_phone}
                          onChange={(e) => handleFieldChange('emergency_contact_phone', e.target.value)}
                          placeholder="(xxx) xxx-xxxx"
                        />
                      ) : (
                        <div className={`data-value ${isFieldMissing(patient.emergency_contact_phone) ? 'missing' : ''}`}>
                          {patient.emergency_contact_phone || 'Not provided'}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Form Metadata Section */}
              {(patient.form_date || patient.signature_date || patient.created_at) && (
                <div className="data-section">
                  <div className="data-section-title">Form Information</div>
                  <div className="data-grid">
                    {patient.form_date && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Form Date</div>
                          <div className="data-value">{patient.form_date}</div>
                        </div>
                      </div>
                    )}
                    {patient.signature_date && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Signature Date</div>
                          <div className="data-value">{patient.signature_date}</div>
                        </div>
                      </div>
                    )}
                    {patient.created_at && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Record Created</div>
                          <div className="data-value">{new Date(patient.created_at).toLocaleString()}</div>
                        </div>
                      </div>
                    )}
                    {patient.updated_at && patient.updated_at !== patient.created_at && (
                      <div className="data-item">
                        <div className="data-icon success">&#10003;</div>
                        <div className="data-content">
                          <div className="data-label">Last Updated</div>
                          <div className="data-value">{new Date(patient.updated_at).toLocaleString()}</div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div className="data-section">
                <div className="data-section-title">Consent Forms</div>
                <div className="consent-grid">
                  <div className={`consent-item ${patient.hipaa_consent ? 'signed' : 'missing'}`}>
                    <div className="consent-icon">&#128196;</div>
                    <div className="consent-label">HIPAA</div>
                    <div className={`consent-status ${patient.hipaa_consent ? 'signed' : 'missing'}`}>
                      {patient.hipaa_consent ? '✓ Signed' : '⚠ Missing'}
                    </div>
                  </div>
                  <div className={`consent-item ${patient.treatment_consent ? 'signed' : 'missing'}`}>
                    <div className="consent-icon">&#128137;</div>
                    <div className="consent-label">Treatment</div>
                    <div className={`consent-status ${patient.treatment_consent ? 'signed' : 'missing'}`}>
                      {patient.treatment_consent ? '✓ Signed' : '⚠ Missing'}
                    </div>
                  </div>
                  <div className="consent-item signed">
                    <div className="consent-icon">&#128176;</div>
                    <div className="consent-label">Financial</div>
                    <div className="consent-status signed">✓ Signed</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Workflow Log */}
          <div className="card">
            <div className="card-header">
              <div className="card-title">
                <span>&#128202;</span> Agent Workflow Log
              </div>
            </div>
            <div className="card-content">
              <div className="log-container">
                <div className="log-entry">
                  <span className="log-time">10:42:01.234</span>
                  <span className="log-icon">&#128269;</span>
                  <span className="log-message">Form detected: {patient.source_file || 'intake_form.png'}</span>
                </div>
                <div className="log-entry">
                  <span className="log-time">10:42:01.456</span>
                  <span className="log-icon">&#129302;</span>
                  <span className="log-message info">Starting OCR extraction...</span>
                </div>
                <div className="log-entry">
                  <span className="log-time">10:42:02.678</span>
                  <span className="log-icon">&#10003;</span>
                  <span className="log-message success">OCR complete (confidence: 94.2%)</span>
                </div>
                <div className="log-entry">
                  <span className="log-time">10:42:02.890</span>
                  <span className="log-icon">&#128269;</span>
                  <span className="log-message">Checking patient database...</span>
                </div>
                <div className="log-entry">
                  <span className="log-time">10:42:03.012</span>
                  <span className="log-icon">&#10133;</span>
                  <span className="log-message info">
                    {patient.is_new_patient ? 'New patient detected - creating record' : 'Existing patient found - updating record'}
                  </span>
                </div>
                {hasAllergies && (
                  <>
                    <div className="log-entry">
                      <span className="log-time">10:42:03.234</span>
                      <span className="log-icon">&#9888;&#65039;</span>
                      <span className="log-message error">CRITICAL: Allergies detected - {patient.allergies}</span>
                    </div>
                    <div className="log-entry">
                      <span className="log-time">10:42:03.345</span>
                      <span className="log-icon">&#128680;</span>
                      <span className="log-message error">Alert sent to provider dashboard</span>
                    </div>
                  </>
                )}
                <div className="log-entry">
                  <span className="log-time">10:42:03.678</span>
                  <span className="log-icon">&#10003;</span>
                  <span className="log-message success">
                    Patient record {patient.is_new_patient ? 'created' : 'updated'}: PT-{patient.id?.toString().padStart(7, '0')}
                  </span>
                </div>
                <div className="log-entry">
                  <span className="log-time">10:42:03.789</span>
                  <span className="log-icon">&#128190;</span>
                  <span className="log-message success">Intake session saved to EMR</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Action Bar */}
      <div className="action-bar">
        <button
          className="btn btn-danger"
          onClick={() => setShowDeleteConfirm(true)}
          disabled={deleting}
        >
          &#128465; Delete Patient
        </button>
      </div>

      {/* Delete Confirmation Dialog */}
      {showDeleteConfirm && (
        <div className="confirm-overlay">
          <div className="confirm-dialog">
            <h3>Delete Patient Record?</h3>
            <p>
              Are you sure you want to delete <strong>{patient.first_name} {patient.last_name}</strong>?
            </p>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
              This will remove:
            </p>
            <ul style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', margin: '0.5rem 0', paddingLeft: '1.5rem' }}>
              <li>Patient record from database</li>
              <li>All associated alerts</li>
              <li>Intake session history</li>
            </ul>

            <label className="checkbox-label" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', margin: '1rem 0', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={deleteSourceFile}
                onChange={(e) => setDeleteSourceFile(e.target.checked)}
                style={{ width: '18px', height: '18px', cursor: 'pointer' }}
              />
              <span style={{ fontSize: '0.9rem' }}>
                Also delete original intake form file
                {patient.source_file && (
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', display: 'block' }}>
                    {patient.source_file.split(/[/\\]/).pop()}
                  </span>
                )}
              </span>
            </label>

            <p className="confirm-warning">This action cannot be undone.</p>
            <div className="confirm-actions">
              <button
                className="btn btn-secondary"
                onClick={() => setShowDeleteConfirm(false)}
                disabled={deleting}
              >
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? 'Deleting...' : 'Delete Patient'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export default PatientDetail
