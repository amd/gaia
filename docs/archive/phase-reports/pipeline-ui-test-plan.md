# Pipeline Template UI & Metrics Dashboard - Test Plan

**Document Version:** 1.0
**Last Updated:** 2026-03-31
**Author:** Morgan Rodriguez, Senior QA Engineer & Test Automation Architect

---

## Overview

This test plan covers comprehensive manual testing procedures for the Pipeline Template UI and Metrics Dashboard features. It complements automated tests with exploratory, visual, and user experience validation.

---

## Table of Contents

1. [Template Management Workflows](#1-template-management-workflows)
2. [Metrics Dashboard Validation](#2-metrics-dashboard-validation)
3. [Cross-Browser Testing](#3-cross-browser-testing)
4. [Accessibility Testing](#4-accessibility-testing)
5. [Known Issues Checklist](#5-known-issues-checklist)

---

## 1. Template Management Workflows

### 1.1 Template List View

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| TM-001 | Verify empty state | 1. Navigate to Pipeline Templates<br>2. Ensure no templates exist | - Empty state message displayed<br>- "Create Template" button visible<br>- No error messages | ☐ |
| TM-002 | Verify template card display | 1. Create at least one template<br>2. View template list | - Template cards display name, description<br>- Stats shown: quality threshold %, iterations, categories, rules<br>- Quality weights visible (first 3 + "...more" if applicable) | ☐ |
| TM-003 | Verify card click navigation | 1. Click on any template card (not buttons) | - Template viewer dialog opens<br>- Correct template data displayed | ☐ |
| TM-004 | Verify View button | 1. Click "View" button on template card | - Template viewer dialog opens<br>- Template details visible | ☐ |
| TM-005 | Verify Edit button | 1. Click "Edit" button on template card | - Template editor dialog opens<br>- All fields populated with current values | ☐ |
| TM-006 | Verify Validate button | 1. Click "Validate" button on template card | - Validation dialog opens<br>- Validation result displayed (valid/invalid)<br>- Errors/warnings shown if any | ☐ |

### 1.2 Template Creation

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| TC-001 | Create minimal template | 1. Click "Create Template"<br>2. Enter name only<br>3. Save | - Template created successfully<br>- Added to template list<br>- Confirmation message shown | ☐ |
| TC-002 | Create template with all fields | 1. Click "Create Template"<br>2. Fill all fields:<br>   - Name, Description<br>   - Quality threshold (0.90)<br>   - Max iterations (10)<br>   - Agent categories (3+ categories)<br>   - Routing rules (2+ rules)<br>   - Quality weights (must sum to 1.0)<br>3. Save | - Template created with all data<br>- All nested structures preserved<br>- No data loss on round-trip | ☐ |
| TC-003 | Validate quality weights sum | 1. Click "Create Template"<br>2. Enter quality weights that don't sum to 1.0 (e.g., 0.3 + 0.3)<br>3. Save | - Validation error shown<br>- Error message indicates weights must sum to 1.0<br>- Template not created | ☐ |
| TC-004 | Validate quality threshold range | 1. Click "Create Template"<br>2. Enter quality_threshold = 1.5 (out of range)<br>3. Save | - Validation error shown<br>- Error indicates value must be 0-1<br>- Template not created | ☐ |
| TC-005 | Validate template name uniqueness | 1. Create template named "test"<br>2. Try to create another "test" template | - Duplicate name error shown<br>- Second template not created | ☐ |
| TC-006 | Validate template name format | 1. Try to create template with name "invalid@name!" | - Invalid name error shown<br>- Only alphanumeric, underscores, hyphens allowed | ☐ |
| TC-007 | Test complex routing conditions | 1. Create template with complex routing:<br>`(defect_type == 'security' or defect_type == 'privacy') and severity > 0.9`<br>2. Save and re-open | - Complex condition preserved exactly<br>- No escaping/parsing issues<br>- Condition readable in editor | ☐ |

### 1.3 Template Editing

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| TE-001 | Partial update preserves data | 1. Create template with all fields<br>2. Edit only description<br>3. Save | - Description updated<br>- All other fields unchanged<br>- Agent categories, routing rules, weights preserved | ☐ |
| TE-002 | Update nested structures | 1. Edit template<br>2. Add new agent category<br>3. Add new routing rule<br>4. Save | - New category added<br>- New rule added<br>- Existing data preserved | ☐ |
| TE-003 | Update quality weights | 1. Edit template<br>2. Modify weight distribution<br>3. Ensure sum = 1.0<br>4. Save | - Weights updated<br>- Sum validation passes | ☐ |
| TE-004 | Cancel edit | 1. Click Edit<br>2. Make changes<br>3. Click Cancel | - Changes discarded<br>- Original values preserved | ☐ |

### 1.4 Template Deletion

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| TD-001 | Delete template | 1. Select template<br>2. Click Delete<br>3. Confirm | - Template removed from list<br>- Confirmation message shown<br>- File deleted from server | ☐ |
| TD-002 | Cancel delete | 1. Select template<br>2. Click Delete<br>3. Cancel confirmation | - Template remains in list<br>- No changes made | ☐ |
| TD-003 | Delete selected template | 1. Open template in viewer<br>2. Delete the same template | - Viewer closes after delete<br>- Template removed from list | ☐ |

### 1.5 Template Validation

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| TV-001 | Validate valid template | 1. Click Validate on valid template | - "Valid" status shown<br>- No errors<br>- May have warnings | ☐ |
| TV-002 | Validate invalid YAML | 1. Corrupt a template file manually<br>2. Click Validate | - "Invalid" status shown<br>- YAML parse error displayed | ☐ |
| TV-003 | Validate with warnings | 1. Create template with edge-case values<br>2. Click Validate | - Warnings displayed for low thresholds, high iterations, etc. | ☐ |

---

## 2. Metrics Dashboard Validation

### 2.1 Dashboard Loading

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| ML-001 | Load dashboard without pipeline ID | 1. Navigate to Metrics Dashboard<br>2. Don't specify pipeline ID | - Shows aggregate metrics<br>- "Aggregate metrics across all pipelines" displayed | ☐ |
| ML-002 | Load dashboard with pipeline ID | 1. Navigate to Metrics Dashboard with pipelineId prop | - Shows specific pipeline metrics<br>- Pipeline ID displayed in header | ☐ |
| ML-003 | Loading state display | 1. Refresh metrics<br>2. Observe during loading | - Loading spinner shown<br>- "Loading metrics..." text displayed<br>- Refresh button disabled | ☐ |
| ML-004 | Error state display | 1. Simulate API error (disconnect network)<br>2. Refresh metrics | - Error banner displayed<br>- Alert icon shown<br>- Error message readable<br>- Dashboard still usable | ☐ |

### 2.2 Auto-Refresh Functionality

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| MA-001 | Toggle auto-refresh on | 1. Ensure auto-refresh is off<br>2. Click toggle button | - Button shows "Live"<br>- Pause icon displayed<br>- Metrics refresh automatically | ☐ |
| MA-002 | Toggle auto-refresh off | 1. Ensure auto-refresh is on<br>2. Click toggle button | - Button shows "Paused"<br>- Play icon displayed<br>- No automatic refresh | ☐ |
| MA-003 | Verify polling interval | 1. Enable auto-refresh<br>2. Observe network requests | - Metrics fetched immediately<br>- Subsequent fetches at 5-second intervals | ☐ |
| MA-004 | Manual refresh while paused | 1. Pause auto-refresh<br>2. Click refresh button | - Metrics fetched once<br>- Auto-refresh remains paused | ☐ |

### 2.3 Metrics Display

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| MD-001 | Summary cards display | 1. Load metrics with data | - Duration, tokens, TPS, TTFT shown<br>- Loop count, iteration count displayed<br>- Quality score visible<br>- Defect count shown | ☐ |
| MD-002 | Phase timing chart | 1. Load metrics with phase data<br>2. Ensure charts visible | - Phase breakdown chart rendered<br>- PLANNING, DEVELOPMENT phases shown<br>- Duration, TPS, TTFT per phase | ☐ |
| MD-003 | Quality over time chart | 1. Load metrics with quality history | - Line chart showing quality progression<br>- X-axis: iterations/time<br>- Y-axis: quality score (0-1) | ☐ |
| MD-004 | State transitions list | 1. Load metrics with transitions | - Transitions listed chronologically<br>- From → To arrows shown<br>- Reason for each transition displayed | ☐ |
| MD-005 | Agent selections display | 1. Load metrics with agent selections | - Each selection shows phase, agent ID<br>- Reason for selection displayed<br>- Alternatives listed if available | ☐ |
| MD-006 | Defects by type display | 1. Load metrics with defects | - Defect categories listed<br>- Count per category shown<br>- Total defects calculable | ☐ |
| MD-007 | Empty states | 1. Load metrics without certain data | - "No state transitions recorded" when empty<br>- "No agent selections recorded" when empty<br>- "No defects recorded" when empty | ☐ |

### 2.4 Charts Toggle

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| MC-001 | Toggle charts off | 1. Click settings icon<br>2. Verify charts hidden | - Phase timing chart hidden<br>- Quality over time chart hidden<br>- Settings icon indicates state | ☐ |
| MC-002 | Toggle charts on | 1. Click settings icon again<br>2. Verify charts visible | - Both charts rendered<br>- Data displayed correctly | ☐ |

---

## 3. Cross-Browser Testing

### 3.1 Browser Compatibility Matrix

| Browser | Version | Template Cards | Template Editor | Metrics Dashboard | Notes |
|---------|---------|----------------|-----------------|-------------------|-------|
| Chrome | Latest (122+) | ☐ | ☐ | ☐ | Primary dev browser |
| Firefox | Latest (123+) | ☐ | ☐ | ☐ | Gecko rendering |
| Safari | Latest (17+) | ☐ | ☐ | ☐ | WebKit rendering |
| Edge | Latest (122+) | ☐ | ☐ | ☐ | Chromium-based |

### 3.2 Cross-Browser Test Cases

| Test ID | Test Case | Browsers | Expected Result | Status |
|---------|-----------|----------|-----------------|--------|
| CB-001 | Template card layout | All | Cards render consistently, no layout shifts | ☐ |
| CB-002 | Dialog rendering | All | Modals/dialogs center correctly, overlay works | ☐ |
| CB-003 | Form inputs | All | Text inputs, selects, checkboxes work correctly | ☐ |
| CB-004 | Chart rendering | All | Recharts/SVG charts render without artifacts | ☐ |
| CB-005 | Button interactions | All | Click handlers fire, hover states work | ☐ |
| CB-006 | Responsive layout | All | UI adapts to window resizing | ☐ |
| CB-007 | Keyboard navigation | All | Tab order, Enter/Space activation work | ☐ |

### 3.3 Responsive Design Testing

| Viewport | Dimensions | Test Focus | Status |
|----------|------------|------------|--------|
| Desktop | 1920x1080 | Full layout, multi-column | ☐ |
| Laptop | 1366x768 | Standard layout | ☐ |
| Tablet | 768x1024 | Collapsed sidebar, stacked cards | ☐ |
| Mobile | 375x667 | Single column, touch-friendly | ☐ |

---

## 4. Accessibility Testing

### 4.1 Screen Reader Compatibility

| Test ID | Test Case | Screen Reader | Expected Result | Status |
|---------|-----------|---------------|-----------------|--------|
| A11Y-001 | Template card announcement | NVDA/JAWS | Card name, description, stats announced | ☐ |
| A11Y-002 | Button labels | NVDA/JAWS | "View template [name]", "Edit template [name]" announced | ☐ |
| A11Y-003 | Error announcements | NVDA/JAWS | Error banner announced with role="alert" | ☐ |
| A11Y-004 | Loading state | NVDA/JAWS | "Loading metrics..." announced | ☐ |
| A11Y-005 | Dialog announcements | NVDA/JAWS | Dialog title and content announced on open | ☐ |

### 4.2 Keyboard Navigation

| Test ID | Test Case | Steps | Expected Result | Status |
|---------|-----------|-------|-----------------|--------|
| KB-001 | Tab through template cards | Press Tab repeatedly | Each card receives focus, visual focus indicator shown | ☐ |
| KB-002 | Activate card with Enter | Focus card, press Enter | Card opens viewer dialog | ☐ |
| KB-003 | Activate card with Space | Focus card, press Space | Card opens viewer dialog | ☐ |
| KB-004 | Navigate action buttons | Tab to card, continue tabbing | View, Edit, Validate buttons receive focus in order | ☐ |
| KB-005 | Close dialog with Escape | Open dialog, press Escape | Dialog closes, focus returns to trigger | ☐ |
| KB-006 | Navigate metrics sections | Press Tab through dashboard | All interactive elements reachable | ☐ |

### 4.3 Visual Accessibility

| Test ID | Test Case | Criteria | Status |
|---------|-----------|----------|--------|
| VA-001 | Color contrast | All text meets WCAG AA (4.5:1 for normal, 3:1 for large) | ☐ |
| VA-002 | Focus indicators | Visible focus rings on all interactive elements | ☐ |
| VA-003 | Error visibility | Error banners have sufficient contrast, icon + text | ☐ |
| VA-004 | Chart accessibility | Charts have text alternatives or data tables | ☐ |
| VA-005 | Reduced motion | UI respects prefers-reduced-motion media query | ☐ |

### 4.4 ARIA Compliance

| Test ID | Test Case | Check | Status |
|---------|-----------|-------|--------|
| ARIA-001 | Button roles | All buttons have role="button" or are `<button>` elements | ☐ |
| ARIA-002 | Heading hierarchy | H1 → H2 → H3 structure maintained | ☐ |
| ARIA-003 | Live regions | Auto-refresh updates announced appropriately | ☐ |
| ARIA-004 | Dialog labeling | Dialogs have aria-labelledby pointing to title | ☐ |
| ARIA-005 | Loading states | aria-busy="true" during loading | ☐ |

---

## 5. Known Issues Checklist

### Critical Issues (Blockers)

- [ ] **API Path Duplication Bug** - Fixed: Changed `/api/v1/...` to `/v1/...` in frontend API client
- [ ] **YAML Data Loss** - Fixed: Ensured nested structures (agent_categories, routing_rules, quality_weights) survive round-trip
- [ ] **Missing Error Handling** - Fixed: Added error state handling in metrics store

### Known Limitations

- [ ] Charts may not render in IE11 (not supported)
- [ ] Very long template names may cause card overflow on mobile
- [ ] Quality weights editor could use visual sum indicator

### Test Coverage Gaps

- [ ] WebSocket real-time metrics (future enhancement)
- [ ] Metrics export functionality
- [ ] Template import/export bulk operations
- [ ] Template comparison view

---

## Test Execution Summary

### Automated Tests

| Test Suite | Count | Status |
|------------|-------|--------|
| templateStore.test.tsx | 35+ | ☐ Pass |
| metricsStore.test.tsx | 50+ | ☐ Pass |
| TemplateCard.test.tsx | 40+ | ☐ Pass |
| MetricsDashboard.test.tsx | 35+ | ☐ Pass |
| test_template_ui.py | 40+ | ☐ Pass |
| test_metrics_dashboard.py | 45+ | ☐ Pass |

### Manual Tests

| Category | Total | Pass | Fail | Blocked |
|----------|-------|------|------|---------|
| Template Management | 19 | ☐ | ☐ | ☐ |
| Metrics Dashboard | 15 | ☐ | ☐ | ☐ |
| Cross-Browser | 7 | ☐ | ☐ | ☐ |
| Accessibility | 13 | ☐ | ☐ | ☐ |

---

## Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| QA Lead | Morgan Rodriguez | | |
| Development Lead | | | |
| Product Owner | | | |
