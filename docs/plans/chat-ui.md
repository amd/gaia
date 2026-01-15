# GAIA Chat UI - Implementation Plan

**Date:** 2025-01-11
**Status:** Draft
**Author:** Claude (with Kalin)
**Priority:** High

---

## Executive Summary

Build **GAIA Chat** - a privacy-first desktop chat application for AI PCs that runs **100% locally** on AMD Ryzen AI hardware. Unlike cloud-based alternatives, your conversations and documents never leave your machine.

### Core Value Proposition

```
┌────────────────────────────────────────────────────────────────┐
│                        GAIA Chat                                │
│                                                                 │
│   🔒 Private      Your data stays on YOUR device               │
│   ⚡ Fast         AMD Ryzen AI NPU acceleration                │
│   📄 Smart        RAG-powered document Q&A                     │
│   💰 Free         No API costs, no subscriptions               │
│                                                                 │
│   "ChatGPT-like experience, but local and private"             │
└────────────────────────────────────────────────────────────────┘
```

### Analytics Justification

| Signal | Data | Implication |
|--------|------|-------------|
| `/quickstart` #1 | 38 views | Users want fast onboarding |
| `/playbooks/chat-agent/part-1` #2 | 17 views | Strong chat/RAG interest |
| Colombia = 60% traffic | 97 views | One power user deep-diving |
| Phoronix coverage | 4 referrals | Linux/privacy-conscious audience |
| GitHub referrals | 16 views | Developers discovering via repo |

**User profile:** Privacy-conscious developers/enthusiasts who want local AI without cloud dependencies.

---

## User Personas

### Primary: "Privacy Paul"
- Developer/power user
- Concerned about data privacy
- Comfortable with CLI but appreciates good UI
- Has technical PDFs (manuals, specs) to search
- Already has Lemonade installed

### Secondary: "Curious Carla"
- Heard about local AI, wants to try it
- Non-technical, needs hand-holding
- May not have Lemonade installed
- Wants "just works" experience

### Tertiary: "Enterprise Eric"
- Evaluating for company use
- Needs to ensure data stays local
- Wants audit trail / compliance features
- Will deploy to team

---

## First-Run Experience (Critical Gap Fix)

### State Machine

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│   Launch    │────▶│ Check State  │────▶│   Onboard   │────▶│   Chat   │
└─────────────┘     └──────────────┘     └─────────────┘     └──────────┘
                           │                    │
                           │ All Ready          │ Skip (power user)
                           ▼                    ▼
                    ┌──────────────┐     ┌─────────────┐
                    │  Direct to   │     │  Direct to  │
                    │    Chat      │     │    Chat     │
                    └──────────────┘     └─────────────┘
```

### State Checks on Launch

```python
def check_system_state():
    return {
        "lemonade_installed": check_lemonade_installed(),
        "lemonade_running": check_lemonade_running(),
        "model_available": check_model_loaded(),
        "embedding_model_available": check_embedding_model(),
        "first_run": not Path("~/.gaia/chat/initialized").exists(),
    }
```

### Onboarding Flow (New Users)

```
┌────────────────────────────────────────────────────────────────┐
│                    Welcome to GAIA Chat                         │
│                                                                 │
│   Your private AI assistant that runs 100% locally.            │
│   No data ever leaves your device.                             │
│                                                                 │
│   Let's get you set up:                                        │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │ ✓ Step 1: Lemonade Server                    [Running]  │  │
│   │ ○ Step 2: Download Chat Model                [2.3 GB]   │  │
│   │ ○ Step 3: Download Embedding Model           [522 MB]   │  │
│   │ ○ Step 4: Ready to Chat!                                │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│   [Start Setup]                          [Skip - I know what I'm doing] │
└────────────────────────────────────────────────────────────────┘
```

### Error States (Gap Fix)

| State | UI Treatment |
|-------|--------------|
| Lemonade not installed | Link to download + install instructions |
| Lemonade not running | "Start Lemonade" button + system tray hint |
| Model not loaded | Progress bar during model load |
| Model download failed | Retry button + disk space check |
| Out of memory | "Try smaller model" suggestion |
| Network error (during download) | Offline mode suggestion |

---

## Document Support (Already Implemented!)

### RAG SDK Supported Formats

The RAG SDK (`src/gaia/rag/sdk.py`) already supports **50+ formats**:

#### Documents
| Format | Extensions | Notes |
|--------|------------|-------|
| PDF | `.pdf` | Full support + VLM for images |
| Text | `.txt`, `.log` | Multi-encoding support (UTF-8, Latin-1, etc.) |
| Markdown | `.md`, `.markdown` | Preserves structure |
| ReStructuredText | `.rst` | Documentation format |
| CSV | `.csv` | Tabular with headers |
| JSON | `.json` | Structured data |

#### Code Files
| Category | Extensions |
|----------|------------|
| Backend | `.py`, `.java`, `.cpp`, `.c`, `.cs`, `.go`, `.rs`, `.rb`, `.php`, `.swift`, `.kt`, `.scala` |
| Web | `.js`, `.jsx`, `.ts`, `.tsx`, `.vue`, `.svelte`, `.astro` |
| Styling | `.css`, `.scss`, `.sass`, `.less` |
| Markup | `.html`, `.htm`, `.svg` |
| Scripting | `.sh`, `.bash`, `.ps1`, `.r` |
| Database | `.sql` |
| Config | `.yaml`, `.yml`, `.xml`, `.toml`, `.ini`, `.env`, `.properties` |
| Build | `.gradle`, `.cmake`, `.mk` |

#### Not Yet Supported (Future)
- `.docx` - Requires python-docx
- `.xlsx` - Requires openpyxl
- `.pptx` - Requires python-pptx
- Images (`.png`, `.jpg`) - Direct VLM (currently only in PDFs)

### UI Implications

```jsx
// Comprehensive format support in drop zone
<DropZone>
  <Icon type="document" />
  <Text>Drop files here</Text>
  <Text muted>PDF, TXT, MD, code files, and more</Text>
</DropZone>

// Show supported formats on hover/click
const SUPPORTED_EXTENSIONS = [
  '.pdf', '.txt', '.md', '.markdown', '.rst', '.log',
  '.csv', '.json',
  '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.go', '.rs',
  '.html', '.css', '.scss', '.yaml', '.yml', '.xml', '.toml', '.sql',
  // ... full list from SDK
];
```

---

## Related Plans

- **[GAIA Installer Plan](./gaia-installer-plan.md)** - Lightweight installer system (one-liner install, winget, `gaia update`, etc.)

---

## Document/Session Relationship (Gap Fix)

### Mental Model Options

**Option A: Global Document Library**
- Documents indexed once, available to all sessions
- Simpler UX, more storage efficient
- Risk: Irrelevant context in new chats

**Option B: Per-Session Documents**
- Each chat has its own document set
- Clearer context boundaries
- Risk: Re-indexing same docs multiple times

**Option C: Hybrid (Recommended)**
- Global document library
- Sessions can "attach" documents from library
- New sessions start with no documents attached
- User explicitly selects which docs to use

### Recommended UX Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  New Chat                                                        │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Select documents for this conversation:                   │   │
│  │                                                           │   │
│  │ ☑ manual.pdf (indexed 2 days ago)                        │   │
│  │ ☐ report.pdf (indexed today)                             │   │
│  │ ☐ spec.pdf (indexed 1 week ago)                          │   │
│  │                                                           │   │
│  │ [+ Add new document]                                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  [Start Chat with 1 document]        [Start without documents]   │
└─────────────────────────────────────────────────────────────────┘
```

### Database Schema (Revised)

```sql
-- Global document library
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    file_hash TEXT UNIQUE NOT NULL,
    file_size INTEGER,
    chunk_count INTEGER,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP,
    -- Index data stored as blob for persistence
    index_data BLOB,
    chunks_json TEXT  -- JSON array of chunk texts
);

-- Sessions (conversations)
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model TEXT NOT NULL,
    system_prompt TEXT
);

-- Many-to-many: which docs are attached to which session
CREATE TABLE session_documents (
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    attached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, document_id)
);

-- Messages
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT CHECK(role IN ('user', 'assistant', 'system')) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- RAG metadata
    rag_sources TEXT,  -- JSON: [{doc_id, chunk_idx, score}]
    tokens_prompt INTEGER,
    tokens_completion INTEGER
);

-- Indexes
CREATE INDEX idx_messages_session ON messages(session_id, created_at);
CREATE INDEX idx_documents_hash ON documents(file_hash);
CREATE INDEX idx_session_docs ON session_documents(session_id);
```

---

## Privacy-First UX (Gap Fix)

### Visual Privacy Indicators

```
┌────────────────────────────────────────────────────────────────┐
│  🔒 GAIA Chat                              [Local Mode Active] │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 🏠 Running 100% locally on your device                   │  │
│  │ 📡 Network: Offline OK | No data sent to cloud           │  │
│  │ 🔐 Model: Qwen3-Coder-30B (local)                        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
```

### Privacy Features to Highlight

1. **Status bar indicator**: "🔒 Local" always visible
2. **Network monitor**: Show that no outbound connections made
3. **Data location**: Settings shows where data is stored
4. **Export/Delete**: Easy data export and secure deletion
5. **No telemetry by default**: Opt-in only analytics

### Settings > Privacy Panel

```
┌────────────────────────────────────────────────────────────────┐
│  Privacy & Data                                                 │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Data Storage                                                   │
│  ├─ Chat history: ~/.gaia/chat/sessions.db                     │
│  ├─ Document index: ~/.gaia/chat/documents/                    │
│  └─ Size: 234 MB                                               │
│                                                                 │
│  [Export All Data]  [Clear All Data]                           │
│                                                                 │
│  ──────────────────────────────────────────────────────────────│
│                                                                 │
│  Analytics (helps improve GAIA)                                │
│  ☐ Send anonymous usage statistics                             │
│     • No conversation content, ever                            │
│     • Only: app version, crash reports, feature usage counts   │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

---

## CLI Integration (Enhanced)

### New Commands

```bash
# Initialize (like gaia-emr init)
gaia chat init                    # Download models, verify setup

# Launch UI
gaia chat ui                      # Desktop app (Electron)
gaia chat ui --browser            # Browser mode
gaia chat ui --port 8081          # Custom port

# CLI chat (existing, enhanced)
gaia chat                         # Interactive CLI
gaia chat --query "Hello"         # One-shot
gaia chat --index doc.pdf         # With document

# Document management (new)
gaia chat docs list               # List indexed documents
gaia chat docs add file.pdf       # Add to global library
gaia chat docs remove <id>        # Remove from library
gaia chat docs clear              # Clear all

# Session management (new)
gaia chat sessions list           # List sessions
gaia chat sessions export <id>    # Export to markdown
gaia chat sessions delete <id>    # Delete session
```

### Shared State Between CLI and UI

```python
# Both CLI and UI use same SQLite database
DEFAULT_DB_PATH = Path.home() / ".gaia" / "chat" / "gaia_chat.db"
DEFAULT_DOCS_PATH = Path.home() / ".gaia" / "chat" / "documents"

# Session started in CLI can continue in UI and vice versa
```

---

## Architecture (Revised)

```
┌─────────────────────────────────────────────────────────────────┐
│                        GAIA Chat Desktop                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────────────┐      ┌─────────────────────────────────┐ │
│   │  Electron Shell  │      │      React Frontend             │ │
│   │  (main.js)       │─────▶│  ┌─────────────────────────────┐│ │
│   │                  │      │  │ OnboardingWizard.jsx        ││ │
│   │  • Window mgmt   │      │  │ ChatView.jsx                ││ │
│   │  • Tray icon     │      │  │ DocumentLibrary.jsx         ││ │
│   │  • Auto-update   │      │  │ SessionList.jsx             ││ │
│   └──────────────────┘      │  │ SettingsPanel.jsx           ││ │
│                             │  │ PrivacyIndicator.jsx        ││ │
│                             │  └─────────────────────────────┘│ │
│                             └─────────────────────────────────┘ │
│                                          │                       │
│                                          ▼                       │
│   ┌─────────────────────────────────────────────────────────────┐│
│   │                    FastAPI Backend                          ││
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   ││
│   │  │ /api/chat/* │ │ /api/docs/* │ │ /api/sessions/*     │   ││
│   │  └─────────────┘ └─────────────┘ └─────────────────────┘   ││
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   ││
│   │  │ /api/system │ │ /api/events │ │ /api/models/*       │   ││
│   │  └─────────────┘ └─────────────┘ └─────────────────────┘   ││
│   └─────────────────────────────────────────────────────────────┘│
│                                          │                       │
│                                          ▼                       │
│   ┌─────────────────────────────────────────────────────────────┐│
│   │                    GAIA Core Layer                          ││
│   │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐   ││
│   │  │ ChatSDK     │ │ RAGSDK      │ │ LemonadeClient      │   ││
│   │  └─────────────┘ └─────────────┘ └─────────────────────┘   ││
│   │  ┌─────────────┐ ┌─────────────┐                           ││
│   │  │ SQLite DB   │ │ FAISS Index │                           ││
│   │  └─────────────┘ └─────────────┘                           ││
│   └─────────────────────────────────────────────────────────────┘│
│                                          │                       │
│                                          ▼                       │
│   ┌─────────────────────────────────────────────────────────────┐│
│   │                  Lemonade Server (External)                 ││
│   │  • Model serving (Qwen3, Nomic-Embed)                       ││
│   │  • NPU/iGPU acceleration                                    ││
│   │  • Runs as separate process                                 ││
│   └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## API Endpoints (Enhanced)

### System & Health

```python
@app.get("/api/system/status")
async def system_status():
    """Check system readiness for onboarding."""
    return {
        "lemonade_running": check_lemonade(),
        "model_loaded": get_loaded_model(),
        "embedding_model_loaded": check_embedding_model(),
        "disk_space_gb": get_available_disk_space(),
        "memory_available_gb": get_available_memory(),
        "initialized": Path("~/.gaia/chat/initialized").expanduser().exists(),
    }

@app.post("/api/system/init")
async def initialize_system(request: InitRequest):
    """Run initialization (model download, etc.)."""
    # Stream progress via SSE
    pass

@app.get("/api/models")
async def list_models():
    """List available models from Lemonade."""
    pass

@app.post("/api/models/{model_id}/load")
async def load_model(model_id: str):
    """Load a specific model."""
    pass
```

### Chat (Enhanced with RAG context)

```python
@app.post("/api/chat/send")
async def send_message(request: ChatRequest):
    """
    Send message with optional document context.

    Request:
        {
            "session_id": "abc123",
            "message": "What does the manual say about X?",
            "document_ids": ["doc1", "doc2"],  # Optional: scope RAG
            "stream": false
        }

    Response:
        {
            "message_id": "msg123",
            "content": "According to the manual...",
            "sources": [
                {"document_id": "doc1", "chunk": "...", "score": 0.85, "page": 12}
            ],
            "tokens": {"prompt": 1234, "completion": 567}
        }
    """
    pass

@app.get("/api/chat/stream")
async def stream_chat(session_id: str, message: str, document_ids: str = ""):
    """SSE streaming endpoint."""
    async def generate():
        # Yield chunks as SSE events
        yield f"data: {json.dumps({'type': 'chunk', 'content': '...'})}\n\n"
        yield f"data: {json.dumps({'type': 'sources', 'data': [...]})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'tokens': {...}})}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### Documents (New)

```python
@app.get("/api/documents")
async def list_documents():
    """List all documents in library."""
    return {
        "documents": [
            {
                "id": "abc123",
                "filename": "manual.pdf",
                "size_bytes": 1234567,
                "chunk_count": 45,
                "indexed_at": "2025-01-11T10:00:00Z",
                "sessions_using": 3
            }
        ],
        "total_size_bytes": 12345678,
        "total_chunks": 450
    }

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile):
    """Upload and index a document."""
    # Return progress via SSE or polling
    pass

@app.post("/api/documents/upload-path")
async def upload_by_path(request: PathUploadRequest):
    """Index document by path (Electron mode)."""
    pass

@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Remove document from library."""
    pass

@app.get("/api/documents/{doc_id}/preview")
async def preview_document(doc_id: str, page: int = 1):
    """Get document preview (first page as image)."""
    pass
```

### Sessions (New)

```python
@app.get("/api/sessions")
async def list_sessions():
    """List all chat sessions."""
    pass

@app.post("/api/sessions")
async def create_session(request: CreateSessionRequest):
    """Create new session with optional document attachments."""
    pass

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session with messages and attached documents."""
    pass

@app.put("/api/sessions/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest):
    """Update session (title, attached docs)."""
    pass

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete session and its messages."""
    pass

@app.post("/api/sessions/{session_id}/documents")
async def attach_document(session_id: str, request: AttachDocRequest):
    """Attach document to session."""
    pass

@app.delete("/api/sessions/{session_id}/documents/{doc_id}")
async def detach_document(session_id: str, doc_id: str):
    """Detach document from session."""
    pass

@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str, format: str = "markdown"):
    """Export session to markdown/json."""
    pass
```

---

## Frontend Components (Revised)

### New Components

```
src/components/
├── onboarding/
│   ├── OnboardingWizard.jsx      # First-run setup wizard
│   ├── SystemCheck.jsx           # Lemonade/model status
│   ├── ModelDownload.jsx         # Download progress
│   └── SetupComplete.jsx         # Success screen
├── chat/
│   ├── ChatView.jsx              # Main chat interface
│   ├── MessageBubble.jsx         # Individual message
│   ├── MessageInput.jsx          # Input with file drop
│   ├── StreamingMessage.jsx      # Animated streaming text
│   ├── SourceCitation.jsx        # Clickable source refs
│   └── SuggestedPrompts.jsx      # Starter prompts
├── documents/
│   ├── DocumentLibrary.jsx       # Global doc library
│   ├── DocumentCard.jsx          # Single doc display
│   ├── DocumentPicker.jsx        # Attach docs to session
│   ├── UploadDropzone.jsx        # Drag-drop upload
│   └── IndexingProgress.jsx      # Chunking progress
├── sessions/
│   ├── SessionList.jsx           # Sidebar session list
│   ├── SessionItem.jsx           # Single session entry
│   ├── NewSessionDialog.jsx      # Create with doc picker
│   └── SessionSearch.jsx         # Search across sessions
├── layout/
│   ├── Header.jsx                # Top bar with privacy indicator
│   ├── Sidebar.jsx               # Collapsible sidebar
│   ├── StatusBar.jsx             # Bottom: model, tokens, status
│   └── PrivacyBadge.jsx          # "🔒 Local" indicator
├── settings/
│   ├── SettingsPanel.jsx         # Settings modal/page
│   ├── ModelSelector.jsx         # Choose LLM model
│   ├── RAGSettings.jsx           # Chunk size, etc.
│   ├── PrivacySettings.jsx       # Data management
│   └── AppearanceSettings.jsx    # Theme, etc.
└── common/
    ├── ErrorBoundary.jsx         # Error handling
    ├── LoadingSpinner.jsx
    ├── Toast.jsx                 # Notifications
    └── ConfirmDialog.jsx
```

---

## Error Handling (Gap Fix)

### Error Types & UI Responses

| Error | Detection | UI Response |
|-------|-----------|-------------|
| Lemonade not running | `/api/system/status` returns `lemonade_running: false` | Full-screen "Start Lemonade" prompt with instructions |
| Model not loaded | Health check shows no model | "Loading model..." with progress, or "Download model" if not available |
| Model crashed | SSE connection drops, API returns 500 | "Model error. Restart?" button + error details |
| Out of memory | Lemonade returns OOM error | "Not enough memory. Try smaller model or close other apps" |
| Document indexing failed | Upload returns error | Toast with error + retry button |
| Document too large | File size check | "File too large (max 100MB). Try splitting the document." |
| Unsupported format | Extension check | "Format not supported. Currently only PDF is supported." |
| Network error (download) | Fetch fails | "Download failed. Check connection." + Retry |
| Database locked | SQLite busy | Retry automatically with exponential backoff |

### Error UI Component

```jsx
function ErrorState({ error, onRetry, onDismiss }) {
  const errorConfigs = {
    LEMONADE_NOT_RUNNING: {
      icon: '🔌',
      title: 'Lemonade Server Not Running',
      description: 'GAIA Chat needs Lemonade Server to run AI models locally.',
      actions: [
        { label: 'Start Lemonade', onClick: startLemonade, primary: true },
        { label: 'How to install', href: '/docs/setup' }
      ]
    },
    MODEL_NOT_LOADED: {
      icon: '🧠',
      title: 'No Model Loaded',
      description: 'A language model needs to be loaded to chat.',
      actions: [
        { label: 'Load Model', onClick: loadDefaultModel, primary: true },
        { label: 'Choose Model', onClick: openModelPicker }
      ]
    },
    // ... more error types
  };

  const config = errorConfigs[error.type] || defaultErrorConfig;

  return (
    <div className="error-state">
      <span className="error-icon">{config.icon}</span>
      <h3>{config.title}</h3>
      <p>{config.description}</p>
      {error.details && <details><summary>Details</summary>{error.details}</details>}
      <div className="error-actions">
        {config.actions.map(action => (
          <Button key={action.label} {...action}>{action.label}</Button>
        ))}
      </div>
    </div>
  );
}
```

---

## Testing Strategy (Enhanced)

### Unit Tests

```python
# Backend tests
tests/unit/chat/ui/
├── test_server.py           # API endpoint tests
├── test_database.py         # SQLite operations
├── test_document_manager.py # Document indexing
└── test_session_manager.py  # Session CRUD
```

### Integration Tests

```python
# End-to-end tests
tests/integration/chat/ui/
├── test_chat_flow.py        # Full chat cycle
├── test_document_upload.py  # Upload → index → query
├── test_onboarding.py       # First-run flow
└── test_error_recovery.py   # Error states
```

### Frontend Tests

```javascript
// Component tests (Vitest + React Testing Library)
src/__tests__/
├── ChatView.test.jsx
├── DocumentLibrary.test.jsx
├── OnboardingWizard.test.jsx
└── ErrorState.test.jsx
```

### Manual Testing Checklist

**First Run:**
- [ ] Fresh install shows onboarding wizard
- [ ] Detects missing Lemonade correctly
- [ ] Model download shows progress
- [ ] Skip button works for power users
- [ ] Completes to chat view

**Chat:**
- [ ] Message sends and streams correctly
- [ ] Markdown renders properly
- [ ] Code blocks have copy button
- [ ] Long messages don't break layout
- [ ] Regenerate works
- [ ] Stop generation works

**Documents:**
- [ ] Drag-drop uploads PDF
- [ ] Non-PDF shows error toast
- [ ] Large file shows warning
- [ ] Indexing progress displays
- [ ] Document appears in library
- [ ] Can attach/detach from session
- [ ] RAG retrieval works
- [ ] Source citations link correctly

**Sessions:**
- [ ] New session creates correctly
- [ ] Session list shows all sessions
- [ ] Can rename session
- [ ] Can delete session
- [ ] Search finds messages
- [ ] Export produces valid markdown

**Error Recovery:**
- [ ] Lemonade stop → shows error → restart works
- [ ] Model crash → can recover
- [ ] Network loss during download → can retry

---

## Success Metrics (Revised)

| Metric | Target | Measurement |
|--------|--------|-------------|
| Time to first chat (new user) | < 5 minutes | Onboarding funnel |
| Time to first chat (returning) | < 10 seconds | App launch → first response |
| Document indexing | < 5 seconds per MB | Performance test |
| Streaming latency | < 200ms first token | Time from send to first chunk |
| Error recovery rate | > 90% | Users who recover from errors |
| Session retention | > 50% | Users who return within 7 days |
| Document usage | > 30% | Sessions that use RAG |

---

## Milestones

### Milestone 0: Foundation
- [ ] Project structure
- [ ] Database schema + migrations
- [ ] Basic FastAPI server
- [ ] System status endpoint
- [ ] CLI `gaia chat init` command

### Milestone 1: Core Chat
- [ ] Chat API endpoints
- [ ] Basic React chat UI
- [ ] SSE streaming
- [ ] Message persistence
- [ ] Session CRUD

### Milestone 2: Documents
- [ ] Document upload API
- [ ] RAG integration
- [ ] Document library UI
- [ ] Session document attachment
- [ ] Source citations

### Milestone 3: Onboarding
- [ ] System state detection
- [ ] Onboarding wizard UI
- [ ] Model download with progress
- [ ] Error state components

### Milestone 4: Polish
- [ ] Privacy indicators
- [ ] Settings panel
- [ ] Export functionality
- [ ] Keyboard shortcuts
- [ ] Performance optimization

### Milestone 5: Distribution
- [ ] Electron packaging
- [ ] Integration with GAIA installer (see [Installer Plan](./gaia-installer-plan.md))
- [ ] Documentation
- [ ] Launch announcement

---

## Open Questions (Updated)

### Resolved
- ✅ Document/session relationship → Hybrid (global library + per-session attachment)
- ✅ Supported formats → PDF only for MVP, expand later
- ✅ CLI/UI shared state → Yes, same SQLite database

### Still Open
1. **Auto-update mechanism** - Electron auto-updater vs manual?
2. **Conversation export** - Markdown only, or also HTML/PDF?
3. **Model switching** - Allow mid-conversation or only new sessions?
4. **Analytics opt-in** - What exactly to track?
5. **Multi-language UI** - Worth prioritizing given Colombia traffic?

---

## Appendix: Competitive Positioning

### vs ChatGPT Desktop

| Feature | ChatGPT | GAIA Chat |
|---------|---------|-----------|
| Privacy | Cloud-based | 100% local |
| Cost | $20/month | Free |
| Offline | No | Yes |
| Document Q&A | Limited | Full RAG |
| Custom models | No | Yes |
| Data ownership | OpenAI | You |

### vs Ollama + Open WebUI

| Feature | Ollama + WebUI | GAIA Chat |
|---------|----------------|-----------|
| Setup complexity | Moderate | One-click |
| AMD optimization | Generic | NPU/iGPU accelerated |
| RAG built-in | Requires config | Native |
| Windows support | Limited | Native |
| Enterprise ready | DIY | Designed for |

### Messaging

> "ChatGPT for your private documents, running entirely on your AMD AI PC. No cloud, no subscription, no compromises."

---

*Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.*
*SPDX-License-Identifier: MIT*
