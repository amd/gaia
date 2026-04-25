# Component Framework UI Implementation - Work Breakdown Structure

**Document Purpose:** Detailed work breakdown for implementing View Source/Edit modals for 44 Component Framework files in the GAIA Agent UI.

**Reference Implementation:** Agent Registry (`AgentRegistry.tsx`) with View Source/Edit modals for `config/agents/*.yaml|*.md` files.

**Component Framework Scope:**
- **44 MD files** across 9 categories
- Categories: `memory`, `knowledge`, `tasks`, `commands`, `documents`, `checklists`, `personas`, `workflows`, `templates`
- Files stored on disk in `component-framework/{category}/{name}.md`
- YAML frontmatter schema: `template_id`, `template_type`, `version`, `description`, `created`, `maintainer`, `schema_version`

---

## Implementation Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     COMPONENT FRAMEWORK UI FLOW                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  [ComponentRegistry.tsx] ──────┐                                        │
│  - Lists all 44 components     │                                        │
│  - Groups by 9 categories      │                                        │
│  - Search/filter by type       │                                        │
│                                │                                        │
│  User clicks "View Source" ────┼────────┐                               │
│  or "Edit" button              │        │                               │
│                                ▼        ▼                               │
│                     [ComponentFileModal.tsx]                            │
│                     - View mode (read-only <pre>)                       │
│                     - Edit mode (textarea)                              │
│                     - Frontmatter parsing                               │
│                     - Save/Cancel actions                               │
│                                                                         │
│  ─────────────────────────────────────────────────────────────────      │
│                           API Layer                                     │
│  ─────────────────────────────────────────────────────────────────      │
│                                                                         │
│  [api.ts] Extended with:         [types/index.ts] Extended with:        │
│  - listComponents()              - ComponentRegistryEntry interface     │
│  - getComponentRaw()             - ComponentFileContent interface       │
│  - saveComponentRaw()            - ComponentCategory type               │
│                                │                                        │
│  ─────────────────────────────────────────────────────────────────      │
│                           Backend (FastAPI)                             │
│  ─────────────────────────────────────────────────────────────────      │
│                                                                         │
│  [src/gaia/ui/routers/pipeline.py]                                      │
│  - GET  /api/v1/pipeline/components/list          (list all)            │
│  - GET  /api/v1/pipeline/components/{cat}/{name}/raw  (get content)     │
│  - PUT  /api/v1/pipeline/components/{cat}/{name}/raw  (save content)    │
│                                                                         │
│  Security: Path traversal protection (reuse SEC-003 from component_loader.py)  │
│                                                                         │
│  ─────────────────────────────────────────────────────────────────      │
│                           Data Layer                                    │
│  ─────────────────────────────────────────────────────────────────      │
│                                                                         │
│  [src/gaia/utils/component_loader.py]                                   │
│  - load_component() - Read MD file with frontmatter parsing             │
│  - save_component() - Write MD file with path traversal protection      │
│  - list_components() - Enumerate all 44 components                      │
│                                                                         │
│  Files stored on disk (NOT in SQLite - Chroma is for embeddings only)   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Backend API Endpoints

**Goal:** Add 3 REST endpoints to `src/gaia/ui/routers/pipeline.py` for component file operations.

**Dependencies:** None (foundational layer)

**Estimated Effort:** 2-3 hours

### Task 1.1: Add Component List Endpoint

**File:** `src/gaia/ui/routers/pipeline.py`

**Endpoint:** `GET /api/v1/pipeline/components/list`

**Purpose:** Return metadata for all 44 components grouped by category.

**Response Schema:**
```typescript
{
  components: ComponentRegistryEntry[],
  categories: Record<string, string[]>,  // category -> [component_paths]
  total: number
}
```

**Implementation Details:**
- Use `ComponentLoader.list_components()` to enumerate all MD files
- For each component, call `get_component_metadata()` to extract frontmatter
- Group components by their `template_type` (9 categories)
- Return sorted lists (alphabetical by path)

**Code Pattern (follows existing `list_agents()` endpoint):**
```python
@router.get("/api/v1/pipeline/components/list")
async def list_components():
    """List all available Component Framework templates."""
    try:
        from gaia.utils.component_loader import ComponentLoader
        loader = ComponentLoader()
        all_paths = loader.list_components()
        
        components = []
        categories = {}
        
        for path in all_paths:
            metadata = loader.get_component_metadata(path)
            entry = {
                "path": path,
                "template_id": metadata["template_id"],
                "template_type": metadata["template_type"],
                "version": metadata["version"],
                "description": metadata["description"],
            }
            components.append(entry)
            
            # Group by category
            cat = metadata["template_type"]
            categories.setdefault(cat, []).append(path)
        
        return {
            "components": components,
            "categories": categories,
            "total": len(components),
        }
    except Exception as e:
        logger.error("Failed to list components: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="...")
```

**Acceptance Criteria:**
- [ ] Returns all 44 component paths
- [ ] Groups by 9 categories correctly
- [ ] Metadata includes template_id, template_type, version, description
- [ ] Empty categories dict if `component-framework/` directory doesn't exist
- [ ] HTTP 500 with helpful error message on failure

---

### Task 1.2: Add Get Component Raw Endpoint

**File:** `src/gaia/ui/routers/pipeline.py`

**Endpoint:** `GET /api/v1/pipeline/components/{category}/{name}/raw`

**Purpose:** Return raw MD content for a specific component file.

**Path Parameters:**
- `category`: One of 9 category directories (memory, knowledge, tasks, etc.)
- `name`: Filename without `.md` extension (e.g., `working-memory`)

**Response Schema:**
```json
{
  "path": "memory/working-memory.md",
  "content": "---\ntemplate_id: working-memory\n...\n---\n\n# Working Memory\n..."
}
```

**Implementation Details:**
- Validate `category` against `VALID_TEMPLATE_TYPES` from `ComponentLoader`
- Validate `name` format (alphanumeric, hyphens, underscores only) - path traversal protection
- Construct component path as `{category}/{name}.md`
- Use `ComponentLoader.load_component()` to read file
- Return full content (frontmatter + body)

**Security Considerations:**
- **CRITICAL:** Validate `category` is in allowed list (prevents directory traversal)
- **CRITICAL:** Validate `name` matches pattern `^[a-zA-Z0-9_-]+$`
- Use `ComponentLoader` which has built-in SEC-003 path traversal protection

**Code Pattern (follows existing `get_agent_raw()` endpoint):**
```python
@router.get("/api/v1/pipeline/components/{category}/{name}/raw")
async def get_component_raw(category: str, name: str):
    """Get raw Markdown content for a component file."""
    from gaia.utils.component_loader import ComponentLoader, ComponentLoaderError
    
    # Validate category
    if category not in ComponentLoader.VALID_TEMPLATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    
    # Validate name format (path traversal protection)
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(status_code=400, detail="Invalid component name format")
    
    component_path = f"{category}/{name}.md"
    
    try:
        loader = ComponentLoader()
        component = loader.load_component(component_path)
        return {
            "path": component_path,
            "content": component["frontmatter_yaml"] + "\n---\n" + component["content"]
        }
    except ComponentLoaderError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Failed to get component %s: %s", component_path, e, exc_info=True)
        raise HTTPException(status_code=500, detail="...")
```

**Acceptance Criteria:**
- [ ] Returns 404 for non-existent components
- [ ] Returns 400 for invalid category (not in 9 allowed types)
- [ ] Returns 400 for invalid name format (path traversal attempt)
- [ ] Returns full MD content with YAML frontmatter
- [ ] Content includes both frontmatter and body

---

### Task 1.3: Add Save Component Raw Endpoint

**File:** `src/gaia/ui/routers/pipeline.py`

**Endpoint:** `PUT /api/v1/pipeline/components/{category}/{name}/raw`

**Purpose:** Save edited MD content back to a component file.

**Request Body:**
```json
{
  "content": "---\ntemplate_id: working-memory\n...\n---\n\n# Updated content..."
}
```

**Response Schema:**
```json
{
  "path": "memory/working-memory.md",
  "updated": true
}
```

**Implementation Details:**
- Same validation as GET endpoint (category + name)
- Use `ComponentLoader.save_component()` to write file
- Invalidate loader cache after save
- Return success confirmation

**Code Pattern (follows existing `update_agent_raw()` endpoint):**
```python
class ComponentFileUpdate(BaseModel):
    """Request body for updating a component file."""
    content: str = Field(..., description="Raw Markdown content to save")

@router.put("/api/v1/pipeline/components/{category}/{name}/raw")
async def update_component_raw(category: str, name: str, update: ComponentFileUpdate):
    """Update raw Markdown content for a component file."""
    from gaia.utils.component_loader import ComponentLoader, ComponentLoaderError
    
    # Validate category
    if category not in ComponentLoader.VALID_TEMPLATE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    
    # Validate name format (path traversal protection)
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(status_code=400, detail="Invalid component name format")
    
    component_path = f"{category}/{name}.md"
    
    try:
        loader = ComponentLoader()
        
        # Parse frontmatter from content for validation
        # (save_component will validate required fields)
        loader.save_component(component_path, content=update.content)
        
        return {
            "path": component_path,
            "updated": True
        }
    except ComponentLoaderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to update component %s: %s", component_path, e, exc_info=True)
        raise HTTPException(status_code=500, detail="...")
```

**Acceptance Criteria:**
- [ ] Returns 400 for invalid frontmatter (missing required fields)
- [ ] Returns 400 for invalid category/name format
- [ ] Returns 404 for non-existent component path (can't create new files)
- [ ] Successfully writes content to disk
- [ ] Cache invalidated after save (subsequent GET returns updated content)

---

### Task 1.4: Import Consolidation

**File:** `src/gaia/ui/routers/pipeline.py`

**Action:** Add necessary imports at top of file:
```python
import re  # Already imported
from pydantic import BaseModel, Field  # Already imported
# Add ComponentLoader import where ComponentLoaderError is imported
from gaia.utils.component_loader import ComponentLoader, ComponentLoaderError
```

**Note:** `ComponentLoader` is already imported in this file, verify and reuse existing import.

---

## Phase 2: Frontend TypeScript Types

**Goal:** Add TypeScript interfaces for Component Framework data structures.

**Dependencies:** Phase 1 complete (API endpoints defined)

**Estimated Effort:** 30 minutes

### Task 2.1: Extend types/index.ts

**File:** `src/gaia/apps/webui/src/types/index.ts`

**Add at end of file (after Pipeline types):**

```typescript
// ── Component Framework Types ─────────────────────────────────────────────

/** Valid component categories (template types). */
export type ComponentCategory =
    | 'memory'
    | 'knowledge'
    | 'tasks'
    | 'commands'
    | 'documents'
    | 'checklists'
    | 'personas'
    | 'workflows'
    | 'templates';

/** Component registry entry returned by list endpoint. */
export interface ComponentRegistryEntry {
    path: string;  // e.g., "memory/working-memory.md"
    template_id: string;
    template_type: ComponentCategory;
    version: string;
    description: string;
    created?: string;
    maintainer?: string;
    schema_version?: string;
}

/** Component file content returned by raw endpoint. */
export interface ComponentFileContent {
    path: string;
    content: string;  // Full MD with YAML frontmatter
}

/** Component list response. */
export interface ComponentListResponse {
    components: ComponentRegistryEntry[];
    categories: Record<string, string[]>;  // category -> [paths]
    total: number;
}
```

**Acceptance Criteria:**
- [ ] TypeScript compiles without errors
- [ ] Interfaces match backend response schemas
- [ ] `ComponentCategory` type matches 9 VALID_TEMPLATE_TYPES from backend

---

## Phase 3: Frontend API Client

**Goal:** Extend `api.ts` with component methods.

**Dependencies:** Phase 1 (backend endpoints) + Phase 2 (types)

**Estimated Effort:** 45 minutes

### Task 3.1: Extend api.ts

**File:** `src/gaia/apps/webui/src/services/api.ts`

**Import Addition (line 6):**
```typescript
import type { ..., ComponentRegistryEntry, ComponentFileContent, ComponentListResponse } from '../types';
```

**Add at end of file (after Pipeline Execution section):**

```typescript
// ── Component Framework Management ─────────────────────────────────

/** List all available Component Framework templates. */
export async function listComponents(): Promise<ComponentListResponse> {
    return apiFetch('GET', '/v1/pipeline/components/list');
}

/** Get raw Markdown content for a component file. */
export async function getComponentRaw(category: string, name: string): Promise<ComponentFileContent> {
    return apiFetch('GET', `/v1/pipeline/components/${category}/${name}/raw`);
}

/** Save edited Markdown content back to a component file. */
export async function saveComponentRaw(category: string, name: string, content: string): Promise<{ path: string; updated: boolean }> {
    return apiFetch('PUT', `/v1/pipeline/components/${category}/${name}/raw`, { content });
}
```

**Acceptance Criteria:**
- [ ] TypeScript compiles without errors
- [ ] Methods follow existing API patterns (listAgents, getAgentRaw, saveAgentRaw)
- [ ] Error handling consistent with existing methods
- [ ] Logging consistent with `log.api.info/error` patterns

---

## Phase 4: Frontend Component - ComponentRegistry.tsx

**Goal:** Create component browser UI (category-grouped display).

**Dependencies:** Phase 2 (types) + Phase 3 (API client)

**Estimated Effort:** 3-4 hours

### Task 4.1: Create ComponentRegistry.tsx

**File:** `src/gaia/apps/webui/src/components/registry/ComponentRegistry.tsx`

**New File:** Create based on `AgentRegistry.tsx` pattern

**Key Differences from AgentRegistry:**
- Components grouped by 9 categories (not flat list)
- Simpler metadata (no capabilities, tools, phases)
- Frontmatter fields: template_id, template_type, version, description, created, maintainer
- No "templates_using" cross-reference needed

**Component Structure:**
```tsx
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * ComponentRegistry - Browse all 44 Component Framework templates
 * grouped by category with View Source/Edit modals.
 */

import { useState, useEffect, useMemo } from 'react';
import { 
    Search, 
    FileText, 
    Tag, 
    Code2, 
    Edit3, 
    Save, 
    X, 
    Copy, 
    Check,
    ChevronDown, 
    ChevronRight 
} from 'lucide-react';
import * as api from '../../services/api';
import type { ComponentRegistryEntry, ComponentFileContent, ComponentCategory } from '../../types';
import './ComponentRegistry.css';

// Category icons mapping
const CATEGORY_ICONS: Record<string, React.ReactNode> = {
    memory: <...>,
    knowledge: <...>,
    tasks: <...>,
    // ... all 9 categories
};

const CATEGORY_LABELS: Record<string, string> = {
    memory: 'Memory',
    knowledge: 'Knowledge',
    // ... human-readable labels
};

export function ComponentRegistry() {
    const [components, setComponents] = useState<ComponentRegistryEntry[]>([]);
    const [categories, setCategories] = useState<Record<string, string[]>>({});
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [activeCategory, setActiveCategory] = useState<string>('all');
    const [expandedComponent, setExpandedComponent] = useState<string | null>(null);

    // Modal state (same as AgentRegistry)
    const [editingComponent, setEditingComponent] = useState<string | null>(null);
    const [fileContent, setFileContent] = useState<string>('');
    const [isLoadingFile, setIsLoadingFile] = useState(false);
    const [isEditing, setIsEditing] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);

    useEffect(() => {
        api.listComponents()
            .then((data) => {
                setComponents(data.components || []);
                setCategories(data.categories || {});
                setTotal(data.total || 0);
            })
            .catch((_err) => {
                console.error('Failed to load component registry:', _err);
            })
            .finally(() => setLoading(false));
    }, []);

    const filteredComponents = useMemo(() => {
        // Filter by category and search
        // ...
    }, [components, activeCategory, search]);

    const categoryList = useMemo(() => {
        return Object.keys(categories).sort();
    }, [categories]);

    const toggleComponent = (path: string) => {
        setExpandedComponent((prev) => (prev === path ? null : path));
    };

    // Modal functions (loadComponentFile, saveComponentFile, cancelEdit, copyToClipboard, closeModal)
    // ... same pattern as AgentRegistry ...

    if (loading) {
        return <LoadingState />;
    }

    return (
        <div className="component-registry">
            {/* Header */}
            {/* Search and Filter Bar */}
            {/* Component List (grouped by category) */}
            {/* Source Modal */}
        </div>
    );
}
```

**UI Layout:**
```
┌─────────────────────────────────────────────────────────────┐
│ Component Registry                                          │
│ 44 components across 9 categories                            │
├─────────────────────────────────────────────────────────────┤
│ [Search: _________________] [All (44)] [Memory (4)] ...    │
├─────────────────────────────────────────────────────────────┤
│ ▼ Memory (4)                                                 │
│   ┌──────────────────────────────────────────────────────┐  │
│   │ > working-memory                    [Memory] [1.0.0]  │  │
│   │   Active problem-solving scratchpad                   │  │
│   │   [View Source] [Edit]                                │  │
│   └──────────────────────────────────────────────────────┘  │
│   ┌──────────────────────────────────────────────────────┐  │
│   │ > short-term-memory                 [Memory] [1.0.0]  │  │
│   └──────────────────────────────────────────────────────┘  │
│   ...                                                        │
├─────────────────────────────────────────────────────────────┤
│ ▼ Knowledge (4)                                              │
│   ...                                                        │
└─────────────────────────────────────────────────────────────┘
```

**Acceptance Criteria:**
- [ ] Displays all 44 components grouped by category
- [ ] Category filter buttons work (All, Memory, Knowledge, etc.)
- [ ] Search filters by template_id, description, path
- [ ] Expandable cards show full description and metadata
- [ ] View Source button opens modal with file content
- [ ] Edit button opens modal in edit mode
- [ ] Loading state shows while fetching
- [ ] Empty state shown if no components found

---

### Task 4.2: Create ComponentRegistry.css

**File:** `src/gaia/apps/webui/src/components/registry/ComponentRegistry.css`

**New File:** Create based on `AgentRegistry.css` pattern

**Key Styles:**
- `.component-registry` - Container
- `.cr-header` - Header section
- `.cr-toolbar` - Search and filter bar
- `.cr-category-filter` - Category buttons
- `.cr-component-card` - Component card (expandable)
- `.cr-component-header` - Clickable header
- `.cr-component-details` - Expanded details
- `.cr-source-actions` - View Source/Edit buttons
- `.cr-modal-overlay` - Modal backdrop
- `.cr-source-modal` - Modal container
- `.cr-source-editor` - Textarea for edit mode
- `.cr-source-viewer` - Pre/code for view mode

**Estimated Effort:** 1 hour

---

## Phase 5: Frontend Component - ComponentFileModal.tsx

**Goal:** Extract modal logic into reusable component.

**Dependencies:** Task 4.1 complete

**Estimated Effort:** 2 hours

### Task 5.1: Create ComponentFileModal.tsx

**File:** `src/gaia/apps/webui/src/components/registry/ComponentFileModal.tsx`

**New File:** Create modal component

**Props:**
```typescript
interface ComponentFileModalProps {
    componentPath: string;  // e.g., "memory/working-memory.md"
    category: string;       // e.g., "memory"
    name: string;           // e.g., "working-memory"
    isOpen: boolean;
    isEditing: boolean;
    isLoading: boolean;
    content: string;
    saveError: string | null;
    copied: boolean;
    onContentChange: (content: string) => void;
    onSave: () => void;
    onCancel: () => void;
    onEdit: () => void;
    onClose: () => void;
    onCopy: () => void;
}
```

**Implementation:**
- Extract modal JSX from `ComponentRegistry.tsx`
- Reuse same structure as AgentRegistry modal
- Props-driven state management

**Acceptance Criteria:**
- [ ] Modal displays correctly in view mode
- [ ] Modal displays textarea in edit mode
- [ ] Save/Cancel buttons functional
- [ ] Copy to clipboard works
- [ ] Close on overlay click
- [ ] Error messages displayed

---

## Phase 6: Integration Tests

**Goal:** Add integration tests for component endpoints.

**Dependencies:** Phase 1 complete (backend endpoints)

**Estimated Effort:** 2 hours

### Task 6.1: Add Component API Tests

**File:** `tests/integration/test_pipeline_ui_integration.py`

**Add new test class:**

```python
class TestComponentFrameworkEndpoints:
    """Test Component Framework file editing endpoints."""

    def test_list_components_returns_all(self):
        """GET /api/v1/pipeline/components/list returns 44 components."""
        from gaia.ui.routers.pipeline import router
        # ... test implementation ...

    def test_list_components_groups_by_category(self):
        """Components grouped by 9 categories."""
        # ... test implementation ...

    def test_get_component_raw_success(self):
        """GET /api/v1/pipeline/components/{cat}/{name}/raw returns content."""
        # Test with known component: memory/working-memory.md
        # ... test implementation ...

    def test_get_component_raw_not_found(self):
        """GET returns 404 for non-existent component."""
        # ... test implementation ...

    def test_get_component_raw_invalid_category(self):
        """GET returns 400 for invalid category (path traversal protection)."""
        # Test with category="../../../etc"
        # ... test implementation ...

    def test_get_component_raw_invalid_name(self):
        """GET returns 400 for invalid name format."""
        # Test with name="../../../etc/passwd"
        # ... test implementation ...

    def test_update_component_raw_success(self):
        """PUT /api/v1/pipeline/components/{cat}/{name}/raw saves content."""
        # ... test implementation ...

    def test_update_component_raw_invalid_frontmatter(self):
        """PUT returns 400 for missing required frontmatter fields."""
        # ... test implementation ...

    def test_update_component_raw_path_traversal_blocked(self):
        """PUT rejects path traversal attempts."""
        # Test with category="../../../tmp", name="exploit"
        # ... test implementation ...
```

**Test Fixtures Needed:**
- Temporary component-framework directory with test MD files
- Mock ComponentLoader for isolation

**Acceptance Criteria:**
- [ ] 8-10 test methods covering success and error paths
- [ ] Path traversal protection tested
- [ ] Frontmatter validation tested
- [ ] Tests follow existing patterns in test_pipeline_ui_integration.py
- [ ] Tests pass with `pytest tests/integration/test_pipeline_ui_integration.py`

---

## Phase 7: Integration and Routing

**Goal:** Wire up ComponentRegistry in the UI router.

**Dependencies:** Phases 4-6 complete

**Estimated Effort:** 1 hour

### Task 7.1: Update UI Router

**File:** `src/gaia/ui/routers/pipeline.py`

**Check:** Verify router is already registered in FastAPI app.

**Action:** No changes needed if router already included.

---

### Task 7.2: Update Frontend Router/Navigation

**File:** `src/gaia/apps/webui/src/App.tsx` (or main routing component)

**Action:** Add route for Component Registry page

**Example:**
```typescript
// Add route similar to AgentRegistry
<Route path="/components" element={<ComponentRegistry />} />
```

**Navigation Menu:**
- Add "Component Registry" link to sidebar/navigation
- Icon: `FileText` or `Layers`

**Acceptance Criteria:**
- [ ] `/components` route displays ComponentRegistry
- [ ] Navigation link accessible from main menu
- [ ] Breadcrumbs/history work correctly

---

## Phase 8: Documentation

**Goal:** Document the Component Framework UI feature.

**Dependencies:** All implementation phases complete

**Estimated Effort:** 1-2 hours

### Task 8.1: Update Agent UI Documentation

**File:** `docs/guides/agent-ui.mdx`

**Add Section:**
```mdx
## Component Registry

The Component Registry provides a UI for browsing and editing Component Framework templates.

### Accessing the Component Registry

1. Launch the Agent UI: `gaia chat --ui`
2. Navigate to **Components** in the sidebar
3. Browse 44 templates across 9 categories

### Editing Components

1. Click **Edit** on any component card
2. Modify the Markdown content (including YAML frontmatter)
3. Click **Save Changes** to persist to disk
4. Click **Cancel** to discard changes

### Component Categories

| Category | Description | Count |
|----------|-------------|-------|
| Memory | Working, short-term, long-term, episodic memory | 4 |
| Knowledge | Domain, procedural, declarative, knowledge graph | 4 |
| Tasks | Breakdown, dependency, priority, tracking | 4 |
| Commands | Shell, git, build, test commands | 4 |
| Documents | Design doc, API spec, meeting notes, status report | 4 |
| Checklists | Analysis, modeling, review, deployment checklists | 4 |
| Personas | Pipeline, specialist, coordinator, validator agents | 4 |
| Workflows | Waterfall, agile, spiral, V-model, pipeline | 5 |
| Templates | 11 template definitions for all component types | 11 |

### Security

Component file editing includes path traversal protection:
- Category must be one of 9 valid types
- Component name must match alphanumeric pattern
- Files cannot be created outside `component-framework/` directory
```

### Task 8.2: Update CLI Reference

**File:** `docs/reference/cli.mdx`

**Note:** No CLI command needed (UI-only feature), but mention in Agent UI section.

---

## Task Summary and Dependencies

```
PHASE 1: Backend API (2-3 hours)
├── 1.1 List Components Endpoint
├── 1.2 Get Component Raw Endpoint
├── 1.3 Save Component Raw Endpoint
└── 1.4 Import Consolidation

PHASE 2: TypeScript Types (30 min)
└── 2.1 Extend types/index.ts
    └── Depends: Phase 1 API contracts defined

PHASE 3: API Client (45 min)
├── 3.1 Extend api.ts
└── Depends: Phase 1 + Phase 2

PHASE 4: ComponentRegistry Component (3-4 hours)
├── 4.1 Create ComponentRegistry.tsx
└── 4.2 Create ComponentRegistry.css
    └── Depends: Phase 2 + Phase 3

PHASE 5: Modal Component (2 hours)
├── 5.1 Create ComponentFileModal.tsx
└── Depends: Phase 4

PHASE 6: Integration Tests (2 hours)
├── 6.1 Add Component API Tests
└── Depends: Phase 1

PHASE 7: UI Integration (1 hour)
├── 7.1 Verify Router Registration
├── 7.2 Add Frontend Route
└── Depends: Phase 4 + Phase 5

PHASE 8: Documentation (1-2 hours)
├── 8.1 Update Agent UI Guide
└── 8.2 Update CLI Reference
    └── Depends: All phases complete

TOTAL ESTIMATED EFFORT: 11-14 hours
```

---

## Risk Assessment

### Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Path traversal vulnerability | Low | High | Reuse SEC-003 from ComponentLoader, validate category against whitelist |
| Frontmatter parsing errors | Medium | Medium | Reuse existing ComponentLoader parsing logic |
| Concurrent edit conflicts | Low | Medium | No locking implemented (same as Agent Registry) |
| Large file handling | Low | Low | Component files typically <10KB |

### Blockers

| Blocker | Resolution |
|---------|------------|
| ComponentLoader.save_component() may need frontmatter reconstruction | Parse frontmatter from content, validate, rebuild |
| Component framework directory structure may differ | Verify with `find component-framework -type f` |
| Existing agent modal CSS may need refactoring | Extract shared modal styles if duplication excessive |

### Outstanding Questions

1. **Should components be creatable via UI?** Current plan: No (edit-only, like agents)
2. **Should frontmatter be edited in UI or raw only?** Current plan: Raw MD (same as agents)
3. **Should there be validation before save?** Current plan: Yes (ComponentLoader.validate_component)

---

## File Inventory

### Files to Create (New)
| File | Purpose | Phase |
|------|---------|-------|
| `src/gaia/apps/webui/src/components/registry/ComponentRegistry.tsx` | Component browser UI | 4.1 |
| `src/gaia/apps/webui/src/components/registry/ComponentRegistry.css` | Component styles | 4.2 |
| `src/gaia/apps/webui/src/components/registry/ComponentFileModal.tsx` | Reusable modal | 5.1 |

### Files to Modify (Existing)
| File | Changes | Phase |
|------|---------|-------|
| `src/gaia/ui/routers/pipeline.py` | Add 3 endpoints | 1.1-1.4 |
| `src/gaia/apps/webui/src/types/index.ts` | Add 3 interfaces | 2.1 |
| `src/gaia/apps/webui/src/services/api.ts` | Add 3 methods | 3.1 |
| `tests/integration/test_pipeline_ui_integration.py` | Add test class | 6.1 |
| `docs/guides/agent-ui.mdx` | Add documentation | 8.1 |
| `src/gaia/apps/webui/src/App.tsx` | Add route (if exists) | 7.2 |

### Files for Reference (Read-Only)
| File | Purpose |
|------|---------|
| `src/gaia/apps/webui/src/components/registry/AgentRegistry.tsx` | Pattern reference |
| `src/gaia/apps/webui/src/components/registry/AgentRegistry.css` | Style reference |
| `src/gaia/utils/component_loader.py` | Reuse for file operations |
| `src/gaia/apps/webui/src/types/index.ts` | Existing type patterns |
| `src/gaia/apps/webui/src/services/api.ts` | Existing API patterns |

---

## Definition of Done

A task is considered complete when:
- [ ] Code compiles/lints without errors
- [ ] Tests pass (for backend changes)
- [ ] UI renders without console errors
- [ ] Feature matches acceptance criteria
- [ ] Documentation updated (for user-facing features)

**Overall Feature Complete When:**
- [ ] All 8 phases complete
- [ ] Can list all 44 components in UI
- [ ] Can view source for any component
- [ ] Can edit and save component files
- [ ] Changes persist to disk
- [ ] Path traversal protection verified
- [ ] Integration tests pass

---

**Document Created:** 2026-04-13
**Status:** Ready for Implementation
**Next Action:** Begin Phase 1 (Backend API Endpoints)
