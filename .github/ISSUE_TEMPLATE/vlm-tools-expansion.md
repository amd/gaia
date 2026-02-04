# Expand VLMToolsMixin with Practical Vision Tools

## Context

`VLMToolsMixin` currently provides 2 generic vision capabilities:
- `analyze_image(image_path, focus)` - Detailed image description
- `answer_question_about_image(image_path, question)` - Visual question answering

**Architectural principle:** VLMToolsMixin should provide GENERIC, reusable vision capabilities for practical use cases:
- Workflow automation
- Coding assistance
- Document analysis
- Computer use agents
- Knowledge work

Use-case-specific tools (like `create_story_from_image`) belong in user agents.

## Proposed New Tools (Prioritized by Utility)

### 1. `extract_text_from_image()` - OCR/Text Extraction [HIGH PRIORITY]
**Signature:**
```python
extract_text_from_image(
    image_path: str,
    preserve_layout: bool = True
) -> dict
```

**Returns:**
```python
{
    "status": "success",
    "text": "def calculate_total(items):\n    return sum(item.price for item in items)",
    "layout_preserved": true
}
```

**Use cases - CRITICAL FOR:**
- **Coding agents**: Extract code from screenshots, error messages, stack traces
- **Document analysis**: OCR scanned PDFs, handwritten notes, forms
- **Computer use agents**: Read text from UI elements, buttons, menus, dialogs
- **Knowledge assistants**: Extract text from diagrams, slides, whiteboards
- **Workflow automation**: Parse receipts, invoices, shipping labels

**Why it's essential:**
- Most fundamental operation for document/UI understanding
- Enables all text-based analysis downstream
- Currently requires external OCR tools - should be built-in

**Implementation:**
- VLM prompt: "Extract all visible text from this image. Preserve formatting, code blocks, and layout."
- Returns raw text with newlines/formatting preserved

---

### 2. `compare_images()` - Visual Diff [HIGH PRIORITY]
**Signature:**
```python
compare_images(
    image_path_1: str,
    image_path_2: str,
    focus: str = "changes"  # "changes", "similarities", "all"
) -> dict
```

**Returns:**
```python
{
    "status": "success",
    "differences": "Button moved from top-right to bottom-left. Color changed from blue to green. New search field added.",
    "similarities": "Same layout structure, same header text",
    "summary": "Minor UI updates with repositioned button and added search"
}
```

**Use cases - CRITICAL FOR:**
- **Computer use agents**: Detect UI changes after actions (click, type, navigate)
- **Testing/QA**: Visual regression testing - compare screenshots before/after code changes
- **Coding agents**: Verify UI updates match requirements
- **Workflow automation**: Detect when processes complete (comparing state before/after)
- **Document versioning**: Track changes in visual documents/designs

**Why it's essential:**
- Key capability for computer use agents (Claude, etc.)
- Automates visual verification workflows
- Replaces manual diff checking

**Implementation:**
- Send both images to VLM with comparison prompt
- Focus parameter controls what to emphasize

---

### 3. `describe_ui_elements()` - UI/Screenshot Understanding [HIGH PRIORITY]
**Signature:**
```python
describe_ui_elements(
    image_path: str,
    element_type: str = "all"  # "buttons", "inputs", "text", "interactive", "all"
) -> dict
```

**Returns:**
```python
{
    "status": "success",
    "elements": [
        {"type": "button", "text": "Submit", "location": "bottom-right", "state": "enabled"},
        {"type": "input", "label": "Email address", "location": "center", "placeholder": "user@example.com"},
        {"type": "text", "content": "Login to your account", "location": "top"}
    ],
    "layout": "Vertical form with email input and submit button",
    "actionable_elements": ["Submit button", "Email input field"]
}
```

**Use cases - CRITICAL FOR:**
- **Computer use agents**: Understand what UI elements are available for interaction
- **Testing automation**: Verify UI elements exist and are positioned correctly
- **Workflow automation**: Navigate UIs programmatically
- **Accessibility**: Describe UI for screen readers
- **Documentation**: Auto-generate UI documentation from screenshots

**Why it's essential:**
- Core capability for computer use agents (navigate, click, type)
- Enables UI automation without element selectors
- Critical for agents interacting with applications

**Implementation:**
- VLM prompt: "List all {element_type} UI elements in this screenshot. For each, describe: type, visible text/label, approximate location, and state (enabled/disabled)."

---

### 4. `analyze_image_sequence()` - Multi-Image Workflow Analysis [MEDIUM PRIORITY]
**Signature:**
```python
analyze_image_sequence(
    image_paths: list[str],
    sequence_type: str = "steps"  # "steps", "progression", "comparison"
) -> dict
```

**Returns:**
```python
{
    "status": "success",
    "sequence_type": "steps",
    "overview": "5-step software installation process from download to completion",
    "steps": [
        {"image": "step1.png", "description": "Download installer dialog", "action": "Click Download button"},
        {"image": "step2.png", "description": "Installer running", "action": "Wait for installation"},
        ...
    ],
    "key_observations": "Process requires admin privileges (UAC prompt in step 3)"
}
```

**Use cases - CRITICAL FOR:**
- **Workflow automation**: Understand multi-step processes from screenshots
- **Tutorial creation**: Analyze step-by-step instructions
- **Computer use**: Track state changes across multiple actions
- **Documentation**: Auto-document workflows from screen recordings
- **Testing**: Verify multi-step processes complete correctly

**Why it's useful:**
- Many workflows involve multiple steps/screens
- Enables understanding of processes, not just individual states

**Implementation:**
- Sends multiple images to VLM (if supported) or sequential calls
- Requires VLM multi-image support (Qwen3-VL-4B supports this)

---

---

### 5. `extract_code_from_image()` - Code Screenshot Analysis [MEDIUM PRIORITY]
**Signature:**
```python
extract_code_from_image(
    image_path: str,
    language: str = "auto"  # "auto", "python", "javascript", etc.
) -> dict
```

**Returns:**
```python
{
    "status": "success",
    "code": "def calculate_total(items):\n    return sum(item.price for item in items)",
    "language": "python",
    "has_syntax_errors": false,
    "context": "Function to calculate order total from list of items"
}
```

**Use cases - CRITICAL FOR:**
- **Coding agents**: Extract code from screenshots, tutorials, documentation
- **Knowledge assistants**: Parse code from slides, whiteboards, books
- **Workflow automation**: Convert visual code snippets to executable code
- **Learning platforms**: Extract code examples from educational content

**Difference from `extract_text_from_image()`:**
- Code-aware: Preserves indentation, syntax highlighting context
- Language detection
- Potential syntax validation

**Implementation:**
- VLM prompt: "Extract code from this image. Preserve exact indentation and syntax. Identify the programming language."

---

### 6. `analyze_diagram()` - Technical Diagram Understanding [MEDIUM PRIORITY]
**Signature:**
```python
analyze_diagram(
    image_path: str,
    diagram_type: str = "auto"  # "auto", "flowchart", "architecture", "erd", "uml"
) -> dict
```

**Returns:**
```python
{
    "status": "success",
    "diagram_type": "flowchart",
    "description": "User authentication flow with 5 decision points",
    "components": ["Login form", "Credential validation", "Session creation", "Error handling"],
    "flow": "User enters credentials → Validate → Create session if valid → Redirect to dashboard",
    "decision_points": ["Valid credentials?", "Has 2FA enabled?"]
}
```

**Use cases - CRITICAL FOR:**
- **Knowledge assistants**: Understand architecture diagrams, flowcharts, ERDs
- **Coding agents**: Analyze system design before implementation
- **Documentation**: Extract insights from technical diagrams
- **Workflow automation**: Understand process flows

**Implementation:**
- VLM prompt: "Analyze this {diagram_type} diagram. Describe: main components, relationships, flow/sequence, decision points."

---

## Design Considerations

### Generic vs. Specific
**Include in VLMToolsMixin (GENERIC UTILITY):**
- ✅ Text extraction (universal operation)
- ✅ Image comparison (generic diff operation)
- ✅ UI element description (computer use primitive)
- ✅ Multi-image sequence analysis (workflow understanding)
- ✅ Code extraction (coding assistant primitive)
- ✅ Diagram analysis (technical document primitive)

**Exclude (USE-CASE SPECIFIC):**
- ❌ Story generation
- ❌ Product descriptions
- ❌ Social media captions
- ❌ Medical diagnosis
- ❌ Game content generation
- ❌ Specific classification tasks

### Implementation Strategy
1. All tools use `self.vlm_client.extract_from_image()` primitive
2. Each tool provides domain-specific prompting
3. Consistent error handling and return format
4. Optional parameters for customization
5. Atomic tool registration

### Error Handling
```python
{
    "status": "error",
    "error": "Image not found: /path/to/image.jpg",
    "image_path": "/path/to/image.jpg"
}
```

### Testing Requirements
- Unit tests with mocked VLM client
- Integration tests with real Qwen3-VL-4B model
- Multi-image tests (for sequence analysis)
- Error cases (missing files, unsupported formats)

---

## Questions for Discussion

1. **Multi-image support:** Verify Qwen3-VL-4B supports multiple images in one call for `compare_images()` and `analyze_image_sequence()`. If not, use sequential calls.

2. **UI element localization:** Should `describe_ui_elements()` return pixel coordinates, or just relative positions ("top-left", "center")?

3. **Code extraction accuracy:** How to handle syntax errors in extracted code? Return as-is or attempt fixes?

4. **Performance:** What's acceptable latency for computer use agents? Need to optimize VLM calls for real-time UI interaction.

5. **Structured output:** Should tools return more structured data (JSON) vs. text descriptions? Example: `describe_ui_elements()` returning list of dicts vs. text description.

---

## Success Metrics

- Tools enable practical agent use cases: computer use, coding assistants, workflow automation, document analysis
- Computer use agents can interact with UIs using `describe_ui_elements()` and `compare_images()`
- Coding agents can extract code from screenshots and understand diagrams
- Users don't need external OCR tools - `extract_text_from_image()` handles it
- Each tool has clear, distinct purpose (minimal overlap with `answer_question_about_image()`)
- Performance acceptable for real-time computer use (<5s per VLM call)

---

## Implementation Priority

**Phase 1 (CRITICAL - Computer Use & Coding Agents):**
1. `extract_text_from_image()` - Foundation for document/UI/code analysis
2. `compare_images()` - Visual diff for computer use agents and QA
3. `describe_ui_elements()` - UI understanding for computer use agents

**Phase 2 (VALUABLE - Workflow & Knowledge):**
4. `analyze_image_sequence()` - Multi-step workflow understanding
5. `extract_code_from_image()` - Code screenshot parsing
6. `analyze_diagram()` - Technical documentation understanding

**Phase 3 (CONSIDER - Based on User Feedback):**
- Document layout analysis (detect headers, sections, tables)
- Error message extraction (from screenshots)
- Visual verification tools (check if UI matches design spec)

---

## Documentation Requirements

For each new tool:
1. Add to VLMToolsMixin docstring
2. Update SDK reference docs
3. Add example to playbook (showing composition into custom tool)
4. Add to VLM tools accordion in Part 1
5. Update tool count throughout docs

---

## Related Issues
- #XXX - VLM multi-image support
- #XXX - Vision model performance benchmarks
- #XXX - VLM client API improvements
