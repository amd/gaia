# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""System prompt for RoutingAgent - analyzes requests and determines agent configuration."""

ROUTING_ANALYSIS_PROMPT = """Analyze this user request and determine the configuration parameters.

User Request: "{query}"

AGENT: CODE
You are analyzing a code generation request. Determine:
1. Programming language (python or typescript)
2. Project type (fullstack or script)

LANGUAGE DETECTION RULES:
- Next.js, React, Vue, Angular, Svelte → "typescript"
- Express, NestJS, Koa, Fastify → "typescript"
- MongoDB, Mongoose (with Express/Node.js) → "typescript"
- Node.js, node, npm, TypeScript → "typescript"
- Vite, Webpack (frontend build tools) → "typescript"
- Django, Flask, FastAPI → "python"
- Pandas, NumPy, SciPy → "python"
- Generic terms only (API, backend, REST, todo, CRUD, app, web) without specific framework → "unknown"

IMPORTANT: If no specific framework is mentioned, you MUST return "unknown".
Do NOT guess based on "commonly used" frameworks or assumptions.

PROJECT TYPE DETECTION RULES FOR TYPESCRIPT:
IMPORTANT: All TypeScript web applications now use Next.js unified approach.
- API, REST, backend, server, database → "fullstack" (Next.js handles API routes)
- React, Vue, Angular, dashboard, UI, webpage, website, app, web → "fullstack" (Next.js handles pages)
- Frontend + Backend, Full-stack → "fullstack" (Next.js handles both)
- CLI, tool, script, utility → "script"
- Generic "app" or "application" → "fullstack" (default for web apps)

PROJECT TYPE DETECTION RULES FOR PYTHON:
- API, REST, backend, server → "api"
- Web app, website, dashboard → "web"
- CLI, tool, script, utility, calculator → "script"
- Generic "app" without specifics → "unknown"

RESPOND WITH ONLY THIS JSON (no markdown, no extra text):
{{
    "agent": "code",
    "parameters": {{
        "language": "typescript|python|unknown",
        "project_type": "fullstack|script|api|web|unknown"
    }},
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of detection"
}}

EXAMPLES:

Input: "Create a Next.js blog"
Output: {{"agent": "code", "parameters": {{"language": "typescript", "project_type": "fullstack"}}, "confidence": 0.95, "reasoning": "Next.js is the TypeScript full-stack framework"}}

Input: "Build a todo app with React"
Output: {{"agent": "code", "parameters": {{"language": "typescript", "project_type": "fullstack"}}, "confidence": 0.9, "reasoning": "React app uses Next.js full-stack framework"}}

Input: "Create an API with Express"
Output: {{"agent": "code", "parameters": {{"language": "typescript", "project_type": "fullstack"}}, "confidence": 0.9, "reasoning": "Express API uses Next.js API routes"}}

Input: "Build a Django REST API"
Output: {{"agent": "code", "parameters": {{"language": "python", "project_type": "api"}}, "confidence": 0.95, "reasoning": "Django is a Python API framework"}}

Input: "Create a dashboard"
Output: {{"agent": "code", "parameters": {{"language": "unknown", "project_type": "fullstack"}}, "confidence": 0.6, "reasoning": "Dashboard is a web app but no framework specified"}}

Input: "Build a REST API"
Output: {{"agent": "code", "parameters": {{"language": "unknown", "project_type": "fullstack"}}, "confidence": 0.5, "reasoning": "REST API mentioned but no framework specified, assume web app"}}

Input: "Create a calculator script"
Output: {{"agent": "code", "parameters": {{"language": "unknown", "project_type": "script"}}, "confidence": 0.7, "reasoning": "Script/CLI tool mentioned"}}

Now analyze the user request above and respond with JSON only."""
