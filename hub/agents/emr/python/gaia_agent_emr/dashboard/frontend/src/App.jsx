// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useEffect, useRef } from 'react'
import { Routes, Route } from 'react-router-dom'
import Header from './components/Header'
import Dashboard from './components/Dashboard'
import PatientDatabase from './components/PatientDatabase'
import PatientDetail from './components/PatientDetail'
import Settings from './components/Settings'
import Chat from './components/Chat'

function App() {
  const [stats, setStats] = useState(null)
  const [patients, setPatients] = useState([])
  const [events, setEvents] = useState([])
  const [dbAlerts, setDbAlerts] = useState([])
  const [processingProgress, setProcessingProgress] = useState(null)
  const [pendingAcks, setPendingAcks] = useState(new Set()) // Track alerts being acknowledged
  const [config, setConfig] = useState(null) // Watch folder, database path, etc.
  const [watchFolderData, setWatchFolderData] = useState(null) // Watch folder files and status
  const initialFeedLoaded = useRef(false) // Track if we've loaded initial feed

  // Function to refresh watch folder data
  const refreshWatchFolder = async () => {
    try {
      const response = await fetch('/api/watch-folder')
      const data = await response.json()
      setWatchFolderData(data)
    } catch (error) {
      console.error('Error fetching watch folder:', error)
    }
  }

  // Fetch config (watch folder, database path)
  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const response = await fetch('/api/config')
        const data = await response.json()
        setConfig(data)
      } catch (error) {
        console.error('Error fetching config:', error)
      }
    }
    fetchConfig()
  }, [])

  // Fetch watch folder data
  useEffect(() => {
    refreshWatchFolder()
    const interval = setInterval(refreshWatchFolder, 3000) // Refresh every 3s for responsive status
    return () => clearInterval(interval)
  }, [])

  // Fetch stats
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await fetch('/api/stats')
        const data = await response.json()
        setStats(data)
      } catch (error) {
        console.error('Error fetching stats:', error)
      }
    }

    fetchStats()
    const interval = setInterval(fetchStats, 5000) // Refresh every 5s
    return () => clearInterval(interval)
  }, [])

  // Fetch patients and populate initial feed
  useEffect(() => {
    const fetchPatients = async () => {
      try {
        const response = await fetch('/api/patients?limit=50')
        const data = await response.json()
        const patientList = data.patients || []
        setPatients(patientList)

        // Populate feed with recent patients on initial load (only once)
        if (!initialFeedLoaded.current && patientList.length > 0) {
          initialFeedLoaded.current = true
          const recentEvents = patientList.slice(0, 10).map(patient => ({
            type: 'patient_created',
            timestamp: patient.created_at || new Date().toISOString(),
            data: {
              first_name: patient.first_name,
              last_name: patient.last_name,
              is_new_patient: patient.is_new_patient,
              changes_detected: patient.changes_detected,
              processing_time_seconds: patient.processing_time_seconds,
              filename: patient.source_file,
            }
          }))
          setEvents(recentEvents)
        }
      } catch (error) {
        console.error('Error fetching patients:', error)
      }
    }

    fetchPatients()
    const interval = setInterval(fetchPatients, 10000) // Refresh every 10s
    return () => clearInterval(interval)
  }, [])

  // Fetch alerts from database (filter out pending acknowledgments)
  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const response = await fetch('/api/alerts?unacknowledged_only=true')
        const data = await response.json()
        // Filter out any alerts that are pending acknowledgment
        const alerts = (data.alerts || []).filter(a => !pendingAcks.has(a.id))
        setDbAlerts(alerts)
      } catch (error) {
        console.error('Error fetching alerts:', error)
      }
    }

    fetchAlerts()
    const interval = setInterval(fetchAlerts, 10000)
    return () => clearInterval(interval)
  }, [pendingAcks])

  // Acknowledge alert
  const acknowledgeAlert = async (alertId) => {
    // Immediately add to pending and remove from display
    setPendingAcks(prev => new Set([...prev, alertId]))
    setDbAlerts(prev => prev.filter(a => a.id !== alertId))

    try {
      await fetch(`/api/alerts/${alertId}/acknowledge`, { method: 'POST' })
      // Successfully acknowledged - remove from pending after a delay
      // (allows time for DB to sync before next fetch)
      setTimeout(() => {
        setPendingAcks(prev => {
          const next = new Set(prev)
          next.delete(alertId)
          return next
        })
      }, 5000)
    } catch (error) {
      console.error('Error acknowledging alert:', error)
      // Failed - remove from pending so it can reappear
      setPendingAcks(prev => {
        const next = new Set(prev)
        next.delete(alertId)
        return next
      })
    }
  }

  // Mark patient as reviewed (approve changes)
  const markPatientReviewed = async (patientId) => {
    // Optimistically update UI - remove changes_detected from patient
    setPatients(prev => prev.map(p =>
      p.id === patientId ? { ...p, changes_detected: null } : p
    ))

    // Also update events to remove changes_detected marker
    setEvents(prev => prev.map(e =>
      e.data?.id === patientId ? { ...e, data: { ...e.data, changes_detected: null } } : e
    ))

    try {
      const response = await fetch(`/api/patients/${patientId}/mark-reviewed`, { method: 'POST' })
      if (!response.ok) {
        throw new Error('Failed to mark as reviewed')
      }
      console.log(`Patient ${patientId} marked as reviewed`)
    } catch (error) {
      console.error('Error marking patient as reviewed:', error)
      // Revert on failure - refetch patients
      fetch('/api/patients?limit=50')
        .then(r => r.json())
        .then(d => setPatients(d.patients || []))
    }
  }

  // SSE for real-time updates
  useEffect(() => {
    const eventSource = new EventSource('/api/events')

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        if (data.type === 'patient_created') {
          // Add to events feed, removing any existing event for the same patient or filename
          // This prevents duplicate entries when the same patient is processed again
          setEvents(prev => {
            const newId = data.data?.id
            const newFilename = data.data?.source_file || data.data?.filename
            const filtered = prev.filter(e => {
              if (e.type !== 'patient_created') return true
              // Remove if same patient ID
              if (newId && e.data?.id === newId) return false
              // Remove if same filename
              if (newFilename && (e.data?.source_file === newFilename || e.data?.filename === newFilename)) return false
              return true
            })
            return [data, ...filtered].slice(0, 20)
          })

          // Refresh patient list, stats, alerts, and watch folder
          fetch('/api/patients?limit=50')
            .then(r => r.json())
            .then(d => setPatients(d.patients || []))

          fetch('/api/alerts?unacknowledged_only=true')
            .then(r => r.json())
            .then(d => setDbAlerts(d.alerts || []))

          fetch('/api/stats')
            .then(r => r.json())
            .then(d => setStats(d))

          // Refresh watch folder to update file status
          refreshWatchFolder()

        } else if (data.type === 'processing_started') {
          setEvents(prev => [data, ...prev].slice(0, 20))
          // Initialize progress tracking with start time
          setProcessingProgress({
            filename: data.data?.filename,
            step_num: 0,
            total_steps: 7,
            step_name: 'Starting...',
            status: 'running',
            startTime: Date.now(),
          })
          // Refresh watch folder to show processing status
          refreshWatchFolder()
        } else if (data.type === 'processing_step') {
          // Update progress (preserve startTime)
          setProcessingProgress(prev => ({
            ...prev,
            filename: data.data?.filename,
            step_num: data.data?.step_num || 0,
            total_steps: data.data?.total_steps || 7,
            step_name: data.data?.step_name || 'Processing...',
            status: data.data?.status || 'running',
          }))
        } else if (data.type === 'processing_completed') {
          // Only add to feed if it's a duplicate or failure (success is shown via patient_created)
          // Also deduplicate by filename to avoid multiple entries for the same file
          if (data.data?.is_duplicate || !data.data?.success) {
            setEvents(prev => {
              const filename = data.data?.filename
              const filtered = prev.filter(e => {
                // Remove any existing processing events for the same filename
                if (filename && e.data?.filename === filename &&
                    ['processing_started', 'processing_completed'].includes(e.type)) {
                  return false
                }
                return true
              })
              return [data, ...filtered].slice(0, 20)
            })
          }
          // Clear progress on completion
          setProcessingProgress(null)
          // Refresh stats and watch folder after processing completes
          fetch('/api/stats')
            .then(r => r.json())
            .then(d => setStats(d))
          refreshWatchFolder()
        } else if (data.type === 'processing_error') {
          // Add to events feed and mark progress as error
          setEvents(prev => [data, ...prev].slice(0, 20))
          setProcessingProgress(prev => prev ? { ...prev, status: 'error' } : null)
        } else if (data.type === 'status_update') {
          // Add status updates to events feed for visibility
          setEvents(prev => [data, ...prev].slice(0, 20))
        } else if (data.type === 'database_cleared') {
          // Clear events feed and reset state when database is cleared
          setEvents([])
          setPatients([])
          setDbAlerts([])
          setStats(null)
          initialFeedLoaded.current = false
          refreshWatchFolder()
        }
      } catch (error) {
        console.error('Error processing SSE event:', error)
      }
    }

    eventSource.onerror = (error) => {
      console.error('SSE connection error:', error)
    }

    return () => eventSource.close()
  }, [])

  return (
    <div className="app">
      <Header
        alertCount={dbAlerts.length}
        alerts={dbAlerts}
        onAcknowledgeAlert={acknowledgeAlert}
      />

      <Routes>
        <Route
          path="/"
          element={
            <Dashboard
              stats={stats}
              patients={patients}
              events={events}
              dbAlerts={dbAlerts}
              onAcknowledgeAlert={acknowledgeAlert}
              onMarkReviewed={markPatientReviewed}
              processingProgress={processingProgress}
              config={config}
              watchFolderData={watchFolderData}
              onRefreshWatchFolder={refreshWatchFolder}
            />
          }
        />
        <Route
          path="/patients"
          element={
            <PatientDatabase
              patients={patients}
              stats={stats}
              onMarkReviewed={markPatientReviewed}
            />
          }
        />
        <Route
          path="/patient/:id"
          element={<PatientDetail />}
        />
        <Route
          path="/settings"
          element={<Settings />}
        />
        <Route
          path="/chat"
          element={<Chat />}
        />
      </Routes>

      {/* Footer */}
      <footer className="footer">
        <p>
          Powered by <span className="footer-amd">AMD Ryzen AI</span> | GAIA Framework | Running 100% Locally - No Cloud Required
        </p>
        <p style={{ marginTop: '0.5rem' }}>
          HIPAA Compliant by Design | &copy; 2024-2025 Advanced Micro Devices, Inc.
        </p>
      </footer>
    </div>
  )
}

export default App
