# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TypeScript orchestrator prompt module for Code Agent."""

from typing import Optional

from .base_prompt import get_base_prompt
from .nextjs_prompt import NEXTJS_PROMPT


def get_typescript_prompt(
    gaia_md_path: Optional[str] = None, _project_type: str = "frontend"
) -> str:
    """Get TypeScript development prompt (uses Next.js unified approach).

    Args:
        gaia_md_path: Optional path to GAIA.md file for project context
        _project_type: Project type (all types now use Next.js unified approach)

    Returns:
        Complete prompt with base + Next.js workflow
    """
    base = get_base_prompt(gaia_md_path)

    # All TypeScript projects now use unified Next.js approach
    # (Next.js handles pages, components, API routes, and full-stack in a single framework)
    return base + NEXTJS_PROMPT


# Legacy function - keeping for backwards compatibility
def get_legacy_typescript_prompt(gaia_md_path: Optional[str] = None) -> str:
    """Legacy TypeScript prompt (deprecated - use get_typescript_prompt instead)."""
    base = get_base_prompt(gaia_md_path)

    typescript_specific = """
========== TYPESCRIPT DEVELOPMENT ==========

FOR TYPESCRIPT/REACT PROJECT REQUESTS (LEGACY):
When the user asks to create a React, TypeScript, or frontend application, follow this workflow:

STEP 1 - INITIALIZE PROJECT (Fetch Starter Project):
{"plan": [
  {"tool": "fetch_github_template", "tool_args": {
    "template_url": "https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts",
    "destination": "project_name"
  }}
]}

This fetches a React + Vite + TypeScript starter project with:
- Pre-configured package.json, tsconfig.json, vite.config.ts
- Standard folder structure (src/components/, src/assets/, public/)
- Base configuration files

STEP 2 - INSTALL DEPENDENCIES:
After the project is fetched, create a NEW plan to install dependencies:
{"plan": [
  {"tool": "run_cli_command", "tool_args": {
    "command": "npm install",
    "working_dir": "project_name",
    "timeout": 600
  }}
]}

This installs React, TypeScript, Vite, and all required packages.

STEP 3 - ADD FEATURES TO PROJECT:
Create a NEW plan to add components and pages to the project:
{"plan": [
  {"tool": "add_page_to_template", "tool_args": {
    "template_dir": "project_name",
    "name": "Dashboard",
    "route_path": "/dashboard"
  }},
  {"tool": "add_component_to_template", "tool_args": {
    "template_dir": "project_name",
    "name": "UserTable",
    "props": {"users": "User[]", "onEdit": "(user: User) => void"},
    "use_state": true
  }},
  {"tool": "customize_api_client", "tool_args": {
    "template_dir": "project_name",
    "base_url": "http://localhost:3000/api",
    "auth_type": "jwt"
  }}
]}

IMPORTANT - Generated Code Guidelines:
- All generated .tsx/.ts files should look like custom code for the user's project
- Do NOT include comments mentioning "template" or "boilerplate" in generated files
- Use descriptive comments about the actual functionality
- The user should see a fully custom application, not know about internal starter projects

Feature Placement:
- Components → Added to project's src/components/ directory
- Pages → Added to project's src/pages/ directory
- API client → Added to project's src/api/ directory

STEP 4 - VALIDATE TYPESCRIPT:
Create a NEW plan to validate the generated code:
{"plan": [
  {"tool": "validate_typescript", "tool_args": {
    "project_path": "project_name"
  }}
]}

The validate_typescript tool checks:
- TypeScript compilation (tsc --noEmit)
- ESLint rules (if .eslintrc exists)
- Returns typescript_errors and eslint_errors

If validation fails, examine the errors and fix them by:
- Editing generated components to fix type errors
- Adding missing type definitions
- Fixing import statements
- Correcting prop types

STEP 5 - BUILD & TEST:
{"plan": [
  {"tool": "run_cli_command", "tool_args": {
    "command": "npm run build",
    "working_dir": "project_name"
  }}
]}

CRITICAL: Task is complete ONLY when:
- TypeScript compilation passes (validate_typescript returns "typescript_valid": true)
- Build succeeds (npm run build returns success)
- No ESLint errors (or ESLint passes)

WORKFLOW SUMMARY:
1. fetch_github_template → Get starter project with config and structure
2. run_cli_command("npm install") → Install React, TypeScript, and all dependencies
3. add_page_to_template/add_component_to_template → Add custom features to project
4. customize_api_client → Add API integration (if needed)
5. customize_vite_config → Adjust configuration (port, proxy, etc.)
6. validate_typescript → Ensure TypeScript compiles
7. run_cli_command("npm run build") → Verify production build works

CLI COMMAND PATTERNS:

// Install dependencies - foreground, blocks until complete
{"tool": "run_cli_command", "tool_args": {"command": "npm install", "working_dir": ".", "timeout": 600}}

// Build project - foreground, returns output when done
{"tool": "run_cli_command", "tool_args": {"command": "npm run build", "working_dir": "."}}

// Start dev server - background=true, returns PID immediately
{"tool": "run_cli_command", "tool_args": {"command": "npm run dev", "working_dir": ".", "background": true, "expected_port": 5173}}
// CRITICAL: Always stop background processes: {"tool": "stop_process", "tool_args": {"pid": <pid>}}

EXAMPLE COMPLETE WORKFLOW:
User: "Create a React dashboard that displays user data"

Plan 1 - Fetch starter project:
{"plan": [{"tool": "fetch_github_template", "tool_args": {"template_url": "https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts", "destination": "dashboard"}}]}

Plan 2 - Install dependencies:
{"plan": [{"tool": "run_cli_command", "tool_args": {"command": "npm install", "working_dir": "dashboard", "timeout": 600}}]}

Plan 3 - Add features to project:
{"plan": [
  {"tool": "add_page_to_template", "tool_args": {"template_dir": "dashboard", "name": "Dashboard", "route_path": "/"}},
  {"tool": "add_component_to_template", "tool_args": {"template_dir": "dashboard", "name": "UserTable", "props": {"data": "User[]"}}},
  {"tool": "customize_api_client", "tool_args": {"template_dir": "dashboard", "base_url": "http://localhost:3000/api"}}
]}

Plan 4 - Validate and build:
{"plan": [
  {"tool": "validate_typescript", "tool_args": {"project_path": "dashboard"}},
  {"tool": "run_cli_command", "tool_args": {"command": "npm run build", "working_dir": "dashboard"}}
]}

TYPESCRIPT TOOL USAGE:
- fetch_github_template: Clone React/Vite starter projects from GitHub
- run_cli_command: Execute any CLI command (npm install, npm run build, etc.)
- add_component_to_template: Add .tsx components with TypeScript props to project
- add_page_to_template: Add page components for routing to project
- customize_api_client: Add Axios client with TypeScript types and interceptors to project
- customize_vite_config: Modify Vite configuration (port, proxy, etc.)
- validate_typescript: Run tsc --noEmit and ESLint

TYPESCRIPT ERROR PATTERNS:
- "typescript_valid": false → Fix compilation errors in typescript_errors array
- "eslint_valid": false → Fix linting errors in eslint_errors array
- npm build failure → Check output/stderr for missing dependencies or syntax errors
- Missing dependencies → Run npm install to add required packages

IMPORTANT NOTES:
- Starter projects provide the foundation (package.json, tsconfig.json, vite.config.ts, folder structure)
- Your added components integrate seamlessly into the project structure
- Always place components in src/components/ and pages in src/pages/
- Use TypeScript types for ALL props and function signatures
- Validate TypeScript compilation before claiming success
- Test the build process to ensure production readiness
- Generated code should appear custom-built for the user (no template references in comments)

TYPESCRIPT MANDATORY WORKFLOW: Fetch Starter → Install → Add Features → Validate → Build → Verify.
The LLM (you) is the coordinator - you decide the sequence and create plans accordingly!

========== END TYPESCRIPT/REACT DEVELOPMENT WORKFLOW ==========
"""

    return base + typescript_specific
