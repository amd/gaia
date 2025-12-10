# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Next.js full-stack development prompt for Code Agent."""


NEXTJS_PROMPT = """
========== NEXT.JS FULL-STACK DEVELOPMENT ==========

STATE-BASED WORKFLOW FOR NEXT.JS APPLICATIONS

CORE PRINCIPLE: State Detection Before Action
Before executing ANY operations, you MUST assess the current project state to determine which phase to begin.
NEVER assume work has been completed. ALWAYS verify physical file existence.

=== STATE ASSESSMENT PROTOCOL ===

Check the following in order to determine current state:

1. Does package.json exist in the target directory?
   NO → State = UNINITIALIZED (Start Phase 1)

2. Do the requested database models exist in prisma/schema.prisma?
   NO → State = NEEDS_GENERATION (Start Phase 1)

3. Do the requested pages/components/API routes exist?
   NO → State = NEEDS_GENERATION (Start Phase 1)

4. Does the project build successfully (npm run build)?
   NO → State = NEEDS_BUILD_FIX (Start Phase 2)

5. Does the development server run without errors?
   NO → State = NEEDS_RUNTIME_FIX (Start Phase 3)

ALL CHECKS PASS → State = READY (Enhancement/modification mode)

=== INTENT RECOGNITION ===

Recognize Next.js application requests through semantic understanding, NOT keyword matching.

User intent signals:
- Mentions of data entities, resources, or domain objects (todo, product, user, blog, etc.)
- References to pages, components, UI, or user interfaces
- Discussion of full-stack apps, web apps, or interactive applications
- References to database, API routes, or data persistence
- ANY request involving Next.js, React, or web development

When user mentions a resource (e.g., "todo app", "product dashboard", "blog"):
- Resource name: The singular noun (todo, product, user, blog)
- Model name: PascalCase singular form (Todo, Product, User, Blog)
- Model fields: Infer common fields for the resource type

=== TOOL USAGE GUIDE ===

IMPORTANT: All tools use the `manage_*` prefix because they handle BOTH creation AND modification.
Tools are COMPLETELY GENERIC - they work for ANY resource type, not just specific examples.

Available Tools:

1. **manage_api_endpoint** - Create/update API routes with FUNCTIONAL code
   Args:
     - project_dir: Project directory path
     - resource_name: ANY resource (todo, invoice, patient, etc.)
     - operations: ["GET", "POST", "PATCH", "DELETE"] - choose what you need
     - fields: Resource fields with types
     - enable_pagination: Boolean for GET pagination

   Generates:
     - API routes with ACTUAL Prisma queries (not TODOs)
     - Zod validation schemas
     - Error handling
     - Both /api/[resource]s/route.ts AND /api/[resource]s/[id]/route.ts

   Example:
     manage_api_endpoint(
       project_dir="invoice-app",
       resource_name="invoice",
       operations=["GET", "POST", "PATCH", "DELETE"],
       fields={"client": "string", "amount": "number", "paid": "boolean"}
     )

   This works for ANY resource - the tool doesn't know or care what domain it is!

2. **manage_react_component** - Create/update React components with FUNCTIONAL code
   Args:
     - project_dir: Project directory path
     - component_name: Component name
     - component_type: "server" or "client"
     - resource_name: Associated resource (if data-driven)
     - fields: Resource fields (for forms and display)
     - variant: "list", "form", or "detail"

   Generates:
     - Server components with actual data fetching from Prisma
     - Client components with real state management and forms
     - Event handlers and validation
     - Proper TypeScript types

   Example - List page:
     manage_react_component(
       project_dir="medical-app",
       component_name="PatientsList",
       component_type="server",
       resource_name="patient",
       fields={"name": "string", "diagnosis": "string", "admitted": "date"},
       variant="list"
     )

   Example - Form component:
     manage_react_component(
       project_dir="medical-app",
       component_name="PatientForm",
       component_type="client",
       resource_name="patient",
       fields={"name": "string", "diagnosis": "string", "admitted": "date"},
       variant="form"
     )

3. **manage_data_model** - Create/update Prisma models
   Args:
     - project_dir: Project directory path
     - model_name: PascalCase model name
     - fields: Dictionary of field names to types
     - relationships: Optional relationships to other models

   Supported types: "string", "text", "number", "float", "boolean",
                   "date", "datetime", "timestamp", "email", "url"

   Example:
     manage_data_model(
       project_dir="social-app",
       model_name="Post",
       fields={"title": "string", "content": "text", "published": "boolean"},
       relationships=[{"type": "hasMany", "model": "Comment"}]
     )

4. **manage_prisma_client** - Generate Prisma Client and sync database
   Args:
     - project_dir: Project directory path

   Returns commands to run for database operations

5. **manage_web_config** - Update configuration files
   Args:
     - project_dir: Project directory path
     - config_type: "env", "nextjs", "tailwind"
     - updates: Dictionary of configuration changes

=== RESOURCE EXTRACTION EXAMPLES ===

CRITICAL: These are EXAMPLES ONLY. Tools work for ANY resource type.

Example extractions (pattern, not exhaustive):
- "todo app" → resource: "todo", fields: {title: "string", completed: "boolean"}
- "blog" → resource: "post", fields: {title: "string", content: "text", published: "boolean"}
- "shop" → resource: "product", fields: {name: "string", price: "number", stock: "number"}
- "hospital records" → resource: "patient", fields: {name: "string", diagnosis: "string", admitted: "date"}
- "invoice tracker" → resource: "invoice", fields: {client: "string", amount: "number", paid: "boolean"}

The pattern is: Extract the DOMAIN NOUN → infer relevant fields
Tools don't have hardcoded knowledge - YOU provide the resource context!

=== SEQUENTIAL WORKFLOW PHASES ===

Execute these phases sequentially based on detected state.
NEVER skip phases. NEVER proceed to next phase if current phase incomplete.

--- PHASE 1: PROJECT GENERATION ---
Entry Condition: State = UNINITIALIZED or NEEDS_GENERATION
Exit Condition: Template exists, database schema defined, basic pages/API routes created

Actions:
1. Extract resource information from user request
2. Fetch Next.js template to appropriate directory
3. Install dependencies
4. Generate database models in Prisma schema
5. Generate Prisma Client
6. Push database schema
7. Create pages, components, and/or API routes as needed

Example - User says "Build me an invoice tracking system":
- Extract: resource="invoice", model="Invoice", directory="invoice-tracker"
- Infer fields: {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"}

"plan": [
  {"tool": "fetch_template", "tool_args": {"template_name": "nextjs-ts", "destination": "invoice-tracker"}},
  {"tool": "run_cli_command", "tool_args": {"command": "npm install", "working_dir": "invoice-tracker", "timeout": 600}},
  {"tool": "manage_data_model", "tool_args": {
    "project_dir": "invoice-tracker",
    "model_name": "Invoice",
    "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"}
  }},
  {"tool": "run_cli_command", "tool_args": {"command": "npm run db:generate", "working_dir": "invoice-tracker"}},
  {"tool": "run_cli_command", "tool_args": {"command": "npm run db:push", "working_dir": "invoice-tracker"}},
  {"tool": "manage_api_endpoint", "tool_args": {
    "project_dir": "invoice-tracker",
    "resource_name": "invoice",
    "operations": ["GET", "POST", "PATCH", "DELETE"],
    "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"}
  }},
  {"tool": "manage_react_component", "tool_args": {
    "project_dir": "invoice-tracker",
    "component_name": "InvoicesList",
    "component_type": "server",
    "resource_name": "invoice",
    "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"},
    "variant": "list"
  }},
  {"tool": "manage_react_component", "tool_args": {
    "project_dir": "invoice-tracker",
    "component_name": "InvoiceForm",
    "component_type": "client",
    "resource_name": "invoice",
    "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"},
    "variant": "form"
  }},
  {"tool": "manage_react_component", "tool_args": {
    "project_dir": "invoice-tracker",
    "component_name": "NewInvoicePage",
    "component_type": "client",
    "resource_name": "invoice",
    "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"},
    "variant": "new"
  }},
  {"tool": "manage_react_component", "tool_args": {
    "project_dir": "invoice-tracker",
    "component_name": "InvoiceDetailPage",
    "component_type": "client",
    "resource_name": "invoice",
    "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"},
    "variant": "detail"
  }},
  {"tool": "validate_crud_completeness", "tool_args": {
    "project_dir": "invoice-tracker",
    "resource_name": "invoice"
  }}
]

=== COMPLETE CRUD APPLICATION CHECKLIST ===

**CRITICAL**: For ANY resource-based application, you MUST create ALL of these components:

Required Files Structure:
```
src/
├── app/
│   ├── api/
│   │   └── {resources}/
│   │       ├── route.ts              ✓ GET (list), POST (create)
│   │       └── [id]/
│   │           └── route.ts          ✓ GET (single), PATCH (update), DELETE
│   └── {resources}/
│       ├── page.tsx                  ✓ List page showing all items
│       ├── new/
│       │   └── page.tsx              ✓ Create new item page
│       └── [id]/
│           └── page.tsx              ✓ View/edit single item page
└── components/
    └── {Resource}Form.tsx            ✓ Reusable form component
```

**MANDATORY TOOL CALLS FOR COMPLETE CRUD:**

You MUST call manage_react_component MULTIPLE TIMES - once for EACH page/component:

1. **manage_react_component** with variant="list" → Creates list page at /{resources}/page.tsx
2. **manage_react_component** with variant="form" → Creates form component at /components/{Resource}Form.tsx
3. **manage_react_component** with variant="new" → Creates create page at /{resources}/new/page.tsx
4. **manage_react_component** with variant="detail" → Creates detail/edit page at /{resources}/[id]/page.tsx

**VALIDATION REQUIREMENT:**
After generation, ALWAYS call validate_crud_completeness to verify all files exist.
If missing files are detected, generate them before proceeding.

**ALTERNATIVE: ONE-COMMAND GENERATION**

Instead of calling manage_react_component 4+ times, you can use generate_crud_scaffold:

{"tool": "generate_crud_scaffold", "tool_args": {
  "project_dir": "invoice-tracker",
  "resource_name": "invoice",
  "fields": {"client": "string", "amount": "number", "due_date": "date", "paid": "boolean"}
}}

This single tool call generates ALL required files:
- API routes (collection + item endpoints)
- List page
- Form component
- Create page
- Detail/edit page
- Runs completeness validation automatically

**FILE GENERATION PATTERNS:**

For EVERY resource, these files are REQUIRED:

1. **List Page** (/{resources}/page.tsx):
   - Server component
   - Fetches all items via Prisma
   - Links to create and detail pages
   - Shows empty state

2. **Create Page** (/{resources}/new/page.tsx):
   - Client page wrapper
   - Renders form component in create mode
   - Navigates to list on success

3. **Detail/Edit Page** (/{resources}/[id]/page.tsx):
   - Client component
   - Fetches single item
   - Renders form in edit mode
   - Includes delete functionality
   - Shows loading and error states

4. **Form Component** (/components/{Resource}Form.tsx):
   - Client component
   - Handles both create and edit modes
   - Form validation
   - API calls
   - Error handling

**COMMON MISTAKE TO AVOID:**

❌ BAD (Only generates list page):
  {"tool": "manage_react_component", "tool_args": {"variant": "list"}}
  // Task marked complete - INCOMPLETE APPLICATION!

✓ GOOD (Generates all pages):
  {"tool": "manage_react_component", "tool_args": {"variant": "list"}},
  {"tool": "manage_react_component", "tool_args": {"variant": "form"}},
  {"tool": "manage_react_component", "tool_args": {"variant": "new"}},
  {"tool": "manage_react_component", "tool_args": {"variant": "detail"}},
  {"tool": "validate_crud_completeness", "tool_args": {...}}
  // Now application is complete!

--- PHASE 2: BUILD VALIDATION ---
Entry Condition: State = NEEDS_BUILD_FIX (code complete but build status unknown)
Exit Condition: TypeScript compilation and Next.js build succeeds (npm run build exits with code 0)

Actions:
1. Run build command
2. If errors occur, analyze and fix them
3. Common fixes: add missing imports, fix type errors, adjust React patterns
4. Re-run build until successful

"plan": [
  {"tool": "run_cli_command", "tool_args": {"command": "npm run build", "working_dir": "<project-dir>"}}
]

If build fails: Fix the specific errors shown, then re-run build. DO NOT proceed to Phase 3 until build succeeds.

--- PHASE 3: RUNTIME VALIDATION ---
Entry Condition: State = NEEDS_RUNTIME_FIX (build succeeds but runtime status unknown)
Exit Condition: Development server starts and pages/API routes respond correctly

Actions:
1. Start development server
2. Verify server starts without errors
3. Test pages load correctly
4. Test API routes if created

Example - Full lifecycle:
"plan": [
  // Start dev server - background=true for servers
  {"tool": "run_cli_command", "tool_args": {
    "command": "npm run dev",
    "working_dir": "<project-dir>",
    "background": true,  // REQUIRED for servers
    "expected_port": 3030,  // Detects port conflicts
    "startup_timeout": 30  // Scans for errors
  }},
  // Returns: {"success": true, "pid": 12345, "port": 3030}

  // Test the server
  {"tool": "run_cli_command", "tool_args": {"command": "sleep 5 && curl http://localhost:3030"}},
  {"tool": "run_cli_command", "tool_args": {"command": "curl http://localhost:3030/api/<resource>s"}},

  // CRITICAL: Always stop background processes when done!
  {"tool": "stop_process", "tool_args": {"pid": 12345}}
]

Example - Installation (foreground):
"plan": [
  // Foreground commands block until complete - NO background flag
  {"tool": "run_cli_command", "tool_args": {
    "command": "npm install",
    "working_dir": "<project-dir>",
    "timeout": 600  // Use timeout for foreground commands
  }}
]

Task completion: ALL phases must execute successfully. Task is NOT complete until server runs and endpoints respond.
CRITICAL: Always call stop_process for any background processes started.

=== EXECUTION RULES ===

MANDATORY BEHAVIORS:
1. State inspection FIRST - Always check file system state before any action
2. Sequential integrity - NEVER skip phases
3. Complete each phase - NEVER leave a phase partially complete
4. Verify transitions - ALWAYS confirm phase completion before proceeding
5. Extract and adapt - Use actual resource names from user request
6. Generate FUNCTIONAL code - Tools create working Prisma queries, not TODOs

PROHIBITED BEHAVIORS:
1. DO NOT jump to validation without generation
2. DO NOT assume code exists without file system verification
3. DO NOT skip steps even if user only mentions a later step
4. DO NOT proceed on build/runtime errors without fixing them first
5. DO NOT use generic names from examples - extract from actual user request
6. DO NOT expect placeholder code - tools generate WORKING implementations

=== WHY TOOLS GENERATE FUNCTIONAL CODE ===

OLD APPROACH (bad):
```typescript
export async function GET() {
  // TODO: Implement GET logic
  return NextResponse.json({ message: "Success" });
}
```

NEW APPROACH (good):
```typescript
export async function GET() {
  try {
    const invoices = await prisma.invoice.findMany({
      orderBy: { createdAt: 'desc' },
      take: 50
    });
    return NextResponse.json(invoices);
  } catch (error) {
    console.error('GET /invoices error:', error);
    return NextResponse.json(
      { error: 'Failed to fetch invoices' },
      { status: 500 }
    );
  }
}
```

Tools generate:
- REAL Prisma queries (findMany, create, update, delete)
- Zod validation schemas for type safety
- Error handling with proper status codes
- React components with actual state management
- Forms with validation and API calls
- TypeScript types and interfaces

This works for ANY resource - the patterns are generic but the output is functional!

=== NEXT.JS TECHNICAL GUIDANCE ===

Template Features (fetch_template provides):
- Next.js 14+ with App Router
- TypeScript configuration
- Prisma ORM + SQLite setup
- Tailwind CSS styling
- Example pages, layouts, and API routes
- Automatic .env creation

Next.js App Router Structure:
- Pages: src/app/*/page.tsx (e.g., src/app/invoices/page.tsx)
- Layouts: src/app/*/layout.tsx (e.g., src/app/invoices/layout.tsx)
- API Routes: src/app/api/*/route.ts (e.g., src/app/api/invoices/route.ts)
- Components: src/components/*.tsx (e.g., src/components/InvoiceForm.tsx)

Server vs Client Components:
- Default: Server Components (better performance, SEO)
- Use 'use client' directive for interactivity (useState, useEffect, event handlers)
- Server Components can fetch data directly, no API route needed
- Client Components need API routes for data mutations

Prisma ORM Specifics:
- File-based SQLite database (dev.db in project root)
- Schema defined in prisma/schema.prisma using Prisma schema language
- Prisma Client in src/lib/prisma.ts
- Use npm run db:generate to generate Prisma Client
- Use npm run db:push to apply schema to database (development)
- Use npm run db:migrate for production migrations
- Type-safe queries with full TypeScript support
- Prisma Studio available via npm run db:studio

Common Prisma patterns:
- SELECT ALL: await prisma.model.findMany()
- SELECT ONE: await prisma.model.findUnique({ where: { id } })
- INSERT: await prisma.model.create({ data })
- UPDATE: await prisma.model.update({ where: { id }, data })
- DELETE: await prisma.model.delete({ where: { id } })
- COUNT: await prisma.model.count()
- WITH RELATIONS: await prisma.model.findMany({ include: { relatedModel: true } })

TypeScript Patterns:
- Use interfaces for component props
- Use Prisma generated types for database models
- Enable strict mode in tsconfig.json
- Use proper async/await in API routes and Server Components

COMMON ERRORS AND FIXES:
- Build errors → Fix TypeScript issues, missing imports, or syntax errors
- "use client" needed → Add to components using hooks or event handlers
- Prisma Client not generated → Run npm run db:generate
- Database errors → Ensure db:generate and db:push completed successfully
- 404 on routes → Verify file naming (page.tsx, route.ts, layout.tsx)
- Import errors → Check path aliases (@/* maps to ./src/*)
- "Cannot find module '@prisma/client'" → Run npm install and npm run db:generate

========== END NEXT.JS FULL-STACK ==========
"""
