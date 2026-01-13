// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

// File type icons based on extension
const getFileIcon = (extension) => {
  switch (extension?.toLowerCase()) {
    case '.pdf':
      return '📄'
    case '.jpg':
    case '.jpeg':
    case '.png':
    case '.tiff':
    case '.bmp':
      return '🖼️'
    default:
      return '📁'
  }
}

// Supported file extensions
const SUPPORTED_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.pdf', '.tiff', '.bmp']

function WatchFolderPanel({ watchFolderData, onRefresh }) {
  const navigate = useNavigate()
  const [isDragOver, setIsDragOver] = useState(false)
  const [pendingFiles, setPendingFiles] = useState([]) // Files being uploaded/processed

  // Prevent default browser/Electron behavior of opening dropped files
  useEffect(() => {
    const preventDefaults = (e) => {
      e.preventDefault()
      e.stopPropagation()
    }

    // Prevent default drag behaviors on the whole document
    document.addEventListener('dragover', preventDefaults)
    document.addEventListener('drop', preventDefaults)

    return () => {
      document.removeEventListener('dragover', preventDefaults)
      document.removeEventListener('drop', preventDefaults)
    }
  }, [])

  // Handle drag over
  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }

  // Handle drag leave
  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    // Only set to false if leaving the panel entirely, not child elements
    if (!e.currentTarget.contains(e.relatedTarget)) {
      setIsDragOver(false)
    }
  }

  // Helper to update a pending file's status
  const updatePendingFile = (filename, updates) => {
    setPendingFiles(prev => prev.map(f =>
      f.name === filename ? { ...f, ...updates } : f
    ))
  }

  // Helper to remove a pending file
  const removePendingFile = (filename) => {
    setPendingFiles(prev => prev.filter(f => f.name !== filename))
  }

  // Format file size
  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  // Handle file drop (supports both browser and Electron)
  const handleDrop = async (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)

    const files = Array.from(e.dataTransfer.files)
    console.log('Drop event received, files:', files.length)

    // In Electron, files have a 'path' property with the full filesystem path
    const isElectron = files.length > 0 && files[0].path

    // Filter valid files
    const validFiles = files.filter(file => {
      const ext = '.' + file.name.split('.').pop().toLowerCase()
      return SUPPORTED_EXTENSIONS.includes(ext)
    })

    if (validFiles.length === 0) {
      console.warn('No valid files dropped')
      return
    }

    // Immediately add files to pending list with "uploading" status
    const newPendingFiles = validFiles.map(file => ({
      name: file.name,
      extension: '.' + file.name.split('.').pop().toLowerCase(),
      size: file.size,
      size_formatted: formatSize(file.size),
      status: 'uploading',
      path: file.path || null,
    }))
    setPendingFiles(prev => [...newPendingFiles, ...prev])

    if (isElectron) {
      console.log('Electron mode - using file paths')

      for (const file of validFiles) {
        const filename = file.name
        try {
          // Update to processing status
          updatePendingFile(filename, { status: 'processing' })

          const response = await fetch('/api/upload-path', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path: file.path }),
          })

          if (!response.ok) {
            throw new Error('Upload failed')
          }

          const result = await response.json()

          if (result.is_duplicate) {
            updatePendingFile(filename, { status: 'duplicate', patient_name: result.patient_name })
          } else if (result.success) {
            updatePendingFile(filename, { status: 'processed', patient_name: result.patient_name })
          } else {
            updatePendingFile(filename, { status: 'failed' })
          }

          // Remove from pending after delay and refresh
          setTimeout(() => {
            removePendingFile(filename)
            if (onRefresh) onRefresh()
          }, 1500)

        } catch (error) {
          console.error('Upload error:', error)
          updatePendingFile(filename, { status: 'failed' })
          setTimeout(() => removePendingFile(filename), 2000)
        }
      }
    } else {
      // Browser mode - upload file content
      console.log('Browser mode - uploading file content')

      for (const file of validFiles) {
        const filename = file.name
        try {
          // Update to processing status
          updatePendingFile(filename, { status: 'processing' })

          const formData = new FormData()
          formData.append('file', file)

          const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
          })

          if (!response.ok) {
            throw new Error('Upload failed')
          }

          const result = await response.json()

          if (result.is_duplicate) {
            updatePendingFile(filename, { status: 'duplicate', patient_name: result.patient_name })
          } else if (result.success) {
            updatePendingFile(filename, { status: 'processed', patient_name: result.patient_name })
          } else {
            updatePendingFile(filename, { status: 'failed' })
          }

          // Remove from pending after delay and refresh
          setTimeout(() => {
            removePendingFile(filename)
            if (onRefresh) onRefresh()
          }, 1500)

        } catch (error) {
          console.error('Upload error:', error)
          updatePendingFile(filename, { status: 'failed' })
          setTimeout(() => removePendingFile(filename), 2000)
        }
      }
    }
  }

  if (!watchFolderData) {
    return (
      <div className="card watch-folder-panel">
        <div className="watch-folder-toolbar">
          <span className="wf-title">📁 Watch Folder</span>
          <span className="wf-loading">Loading...</span>
        </div>
      </div>
    )
  }

  const {
    watch_dir,
    files = [],
    total = 0,
    processed_count = 0,
    queued_count = 0,
    processing_count = 0,
    failed_count = 0,
    current_processing
  } = watchFolderData

  // Status label helper
  const getStatusLabel = (status) => {
    switch (status) {
      case 'uploading': return 'Uploading...'
      case 'processing': return 'Processing...'
      case 'queued': return 'Queued'
      case 'processed': return 'Processed'
      case 'duplicate': return 'Duplicate'
      case 'failed': return 'Failed'
      default: return status
    }
  }

  // Combine pending files with server files (pending first, exclude duplicates)
  const pendingNames = new Set(pendingFiles.map(f => f.name))
  const combinedFiles = [
    ...pendingFiles,
    ...files.filter(f => !pendingNames.has(f.name))
  ]

  return (
    <div
      className={`card watch-folder-panel ${isDragOver ? 'drag-over' : ''}`}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragOver && (
        <div className="drag-overlay">
          <span>📥 Drop files here</span>
        </div>
      )}

      {/* Single Compact Toolbar */}
      <div className="watch-folder-toolbar">
        <span className="wf-title">📁 Watch Folder</span>
        <span className="wf-path" title={watch_dir}>{watch_dir}</span>
        <div className="wf-stats">
          <span className="wf-stat total">{total} files</span>
          <span className="wf-stat processed" title="Processed">
            <span className="wf-dot processed"></span> {processed_count}
          </span>
          {processing_count > 0 && (
            <span className="wf-stat processing" title="Processing">
              <span className="wf-dot processing"></span> {processing_count}
            </span>
          )}
          {failed_count > 0 && (
            <span className="wf-stat failed" title="Failed">
              <span className="wf-dot failed"></span> {failed_count}
            </span>
          )}
          {queued_count > 0 && (
            <span className="wf-stat queued" title="Queued">
              <span className="wf-dot queued"></span> {queued_count}
            </span>
          )}
        </div>
        <button className="wf-refresh" onClick={onRefresh} title="Refresh">↻</button>
      </div>

      {/* File List */}
      <div className="watch-folder-content">
        {combinedFiles.length > 0 ? (
          <div className="file-list">
            {combinedFiles.map((file, idx) => (
              <div
                key={file.name + idx}
                className={`file-row ${file.status}`}
                onClick={() => file.patient_id && navigate(`/patients`)}
                style={{ cursor: file.patient_id ? 'pointer' : 'default' }}
              >
                <span className={`file-dot ${file.status}`}></span>
                <span className="file-icon">{getFileIcon(file.extension)}</span>
                <span className="file-name" title={file.name}>{file.name}</span>
                <span className={`file-status-label ${file.status}`}>{getStatusLabel(file.status)}</span>
                <span className="file-size">{file.size_formatted}</span>
                {file.patient_name && <span className="file-patient">→ {file.patient_name}</span>}
              </div>
            ))}
          </div>
        ) : (
          <div className="watch-folder-empty">
            <span>Drop images/PDFs here to process</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default WatchFolderPanel
