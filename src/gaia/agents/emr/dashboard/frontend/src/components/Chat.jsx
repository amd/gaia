// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useState, useRef, useEffect } from 'react'

function Chat() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  useEffect(() => {
    // Focus input on mount
    inputRef.current?.focus()
  }, [])

  const sendMessage = async (messageText) => {
    const text = messageText || input.trim()
    if (!text || isLoading) return

    // Add user message
    const userMessage = {
      id: Date.now(),
      type: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    }
    setMessages(prev => [...prev, userMessage])
    setInput('')
    setIsLoading(true)

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })

      const data = await response.json()

      // Add agent response
      const agentMessage = {
        id: Date.now() + 1,
        type: 'agent',
        content: data.response || 'No response received.',
        timestamp: data.timestamp,
        success: data.success,
      }
      setMessages(prev => [...prev, agentMessage])
    } catch (error) {
      // Add error message
      const errorMessage = {
        id: Date.now() + 1,
        type: 'agent',
        content: `Error: ${error.message}`,
        timestamp: new Date().toISOString(),
        success: false,
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const queryCategories = [
    {
      title: "📊 Statistics & Overview",
      queries: [
        "How many patients were processed today?",
        "What are the current statistics?",
        "Show me the time saved summary",
        "How many new patients vs returning?",
      ]
    },
    {
      title: "🔍 Search Patients",
      queries: [
        "Find patient John Smith",
        "Show me patients with allergies",
        "Find returning patients",
        "List patients processed this week",
      ]
    },
    {
      title: "⚠️ Alerts & Reviews",
      queries: [
        "Are there any pending alerts?",
        "Show critical allergies",
        "Which patients need review?",
        "List unacknowledged alerts",
      ]
    },
    {
      title: "📋 Patient Details",
      queries: [
        "What insurance does patient #1 have?",
        "Show me the reason for visit for recent patients",
        "List emergency contacts",
        "Find patients with missing information",
      ]
    },
  ]

  return (
    <div className="chat-container">
      <div className="chat-header">
        <div className="chat-title">
          <span className="chat-icon">💬</span>
          <div>
            <h2>Chat with Agent</h2>
            <p>Ask questions about patients, statistics, or request actions using natural language</p>
          </div>
        </div>
      </div>

      <div className="chat-messages">
        {messages.length === 0 ? (
          <div className="chat-welcome">
            <div className="welcome-icon">🤖</div>
            <h3>Welcome to GAIA Medical Intake Agent</h3>
            <p>I can help you query patient data, check statistics, and find information. Click any example below to try it:</p>

            <div className="query-categories">
              {queryCategories.map((category, catIndex) => (
                <div key={catIndex} className="query-category">
                  <h4>{category.title}</h4>
                  <div className="suggested-queries">
                    {category.queries.map((query, queryIndex) => (
                      <button
                        key={queryIndex}
                        className="suggested-query"
                        onClick={() => sendMessage(query)}
                      >
                        {query}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="chat-tips">
              <h4>💡 Tips</h4>
              <ul>
                <li>Use natural language - ask questions like you would to a colleague</li>
                <li>Be specific when searching - include names, dates, or conditions</li>
                <li>I can answer follow-up questions about the same topic</li>
                <li>All processing happens locally on your AMD Ryzen AI PC</li>
              </ul>
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`chat-message ${msg.type === 'user' ? 'user-message' : 'agent-message'}`}
              >
                <div className="message-avatar">
                  {msg.type === 'user' ? '👤' : '🤖'}
                </div>
                <div className="message-content">
                  <div className="message-header">
                    <span className="message-sender">
                      {msg.type === 'user' ? 'You' : 'GAIA Agent'}
                    </span>
                    <span className="message-time">
                      {new Date(msg.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="message-text">
                    {msg.content}
                  </div>
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="chat-message agent-message">
                <div className="message-avatar">🤖</div>
                <div className="message-content">
                  <div className="message-header">
                    <span className="message-sender">GAIA Agent</span>
                  </div>
                  <div className="message-text typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      <div className="chat-input-container">
        <div className="chat-input-wrapper">
          <input
            ref={inputRef}
            type="text"
            className="chat-input"
            placeholder="Ask about patients, statistics, or request actions..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
          />
          <button
            className="chat-send-button"
            onClick={() => sendMessage()}
            disabled={!input.trim() || isLoading}
          >
            {isLoading ? (
              <span className="send-loading">⏳</span>
            ) : (
              <span>Send</span>
            )}
          </button>
        </div>
        <div className="chat-hint">
          Press Enter to send • Powered by AMD Ryzen AI
        </div>
      </div>
    </div>
  )
}

export default Chat
