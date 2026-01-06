# GAIA Medical Intake Agent

AI-powered patient intake form processing using AMD Ryzen AI.

## Features

- **Automatic Form Processing** - Watch directory for new intake forms
- **VLM Extraction** - Extract patient data from images using Qwen2.5-VL
- **Database Storage** - SQLite database with patient records
- **Natural Language Interface** - Query patients using conversational AI
- **Web Dashboard** - Real-time monitoring with live feed
- **Local Processing** - 100% on-device, HIPAA-friendly

## Quick Start

### Prerequisites

1. Install GAIA with dependencies:
   ```bash
   pip install -e ".[api,rag]"  # rag includes pymupdf for PDF support
   ```

2. Start Lemonade Server with VLM model:
   ```bash
   lemonade server --model Qwen2.5-VL-7B-Instruct-GGUF
   ```

### CLI Usage

#### Watch Mode (Automatic Processing)

```bash
# Start watching for new intake forms
gaia-emr watch

# Custom directories
gaia-emr --watch-dir ./my_forms --db ./my_db.db watch
```

Drop intake forms in `./intake_forms/` and they'll be processed automatically.

#### Process Single File

```bash
gaia-emr process path/to/form.jpg
```

#### Query Patients

```bash
gaia-emr query "Find patient John Smith"
gaia-emr query "How many patients processed today?"
```

#### Statistics

```bash
gaia-emr stats
```

#### Web Dashboard

```bash
gaia-emr dashboard

# Custom host/port
gaia-emr dashboard --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080 in your browser.

## Python API

```python
from gaia.agents.emr import MedicalIntakeAgent

# Create agent
agent = MedicalIntakeAgent(
    watch_dir="./intake_forms",
    db_path="./data/patients.db",
)

# Query interactively
agent.process_query("How many patients were processed today?")
agent.process_query("Find patient with DOB 1990-05-15")

# Cleanup
agent.stop()

# Or use as context manager
with MedicalIntakeAgent() as agent:
    agent.process_query("Show recent patients")
```

## Dashboard Setup

### Build Frontend

```bash
cd src/gaia/agents/emr/dashboard/frontend
npm install
npm run build
```

### Run Dashboard

```bash
python -m gaia.agents.emr.cli dashboard
```

Visit http://localhost:8080 to see:
- Real-time intake processing feed
- Patient statistics
- Critical allergy alerts
- Patient list with search

## Testing

```bash
# Run unit tests
pytest tests/unit/test_emr_agent.py -v

# Test with mock VLM (no Lemonade server needed)
from gaia.testing import MockVLMClient, temp_directory

with temp_directory() as tmp_dir:
    agent = MedicalIntakeAgent(
        watch_dir=str(tmp_dir / "intake"),
        db_path=str(tmp_dir / "patients.db"),
        skip_lemonade=True,
        silent_mode=True,
    )

    # Mock VLM response
    agent._vlm = MockVLMClient(
        extracted_text='{"first_name": "Test", "last_name": "Patient"}'
    )

    # Test processing
    agent._process_intake_form("test.jpg")
```

## Architecture

```
MedicalIntakeAgent
├── DatabaseMixin       - SQLite patient records
├── FileWatcherMixin    - Automatic file detection
├── VLMClient           - Image data extraction
└── Tools
    ├── search_patients      - Search by name/DOB
    ├── get_patient          - Get by ID
    ├── list_recent_patients - Recent records
    ├── get_intake_stats     - Processing stats
    └── process_file         - Manual processing
```

## Database Schema

The agent creates three tables:

### patients
```sql
CREATE TABLE patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT,
    last_name TEXT,
    date_of_birth TEXT,
    gender TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip_code TEXT,
    insurance_provider TEXT,
    insurance_id TEXT,
    reason_for_visit TEXT,
    allergies TEXT,
    medications TEXT,
    emergency_contact_name TEXT,
    emergency_contact_phone TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    source_file TEXT,
    raw_extraction TEXT,
    is_new_patient BOOLEAN DEFAULT TRUE,
    processing_time_seconds REAL
);
```

### alerts
```sql
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id),
    alert_type TEXT NOT NULL,      -- 'allergy', 'missing_field'
    priority TEXT DEFAULT 'medium', -- 'critical', 'high', 'medium'
    message TEXT NOT NULL,
    data TEXT,                     -- JSON payload
    created_at TEXT DEFAULT (datetime('now')),
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by TEXT,
    acknowledged_at TEXT
);
```

### intake_sessions
```sql
CREATE TABLE intake_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id),
    source_file TEXT,
    processing_time_seconds REAL,
    is_new_patient BOOLEAN,
    changes_detected TEXT,         -- JSON of detected field changes
    created_at TEXT DEFAULT (datetime('now'))
);
```

## API Endpoints

When running the dashboard, these endpoints are available:

- `GET /api/patients` - List patients (with pagination/search)
- `GET /api/patients/{id}` - Get patient details
- `GET /api/stats` - Processing statistics with time savings
- `GET /api/events` - SSE stream for real-time updates
- `GET /api/alerts` - List alerts (unacknowledged by default)
- `POST /api/alerts/{id}/acknowledge` - Acknowledge an alert
- `GET /api/sessions` - Audit trail of intake sessions
- `GET /api/health` - Health check with connected clients count

## Configuration

Environment variables (optional):

- `LEMONADE_BASE_URL` - Lemonade server URL (default: http://localhost:8000/api/v1)

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT
