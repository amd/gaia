# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Code generation patterns for web applications.

This module contains reusable code patterns for generating functional
web application code. Patterns are framework-agnostic where possible,
with framework-specific variants where needed.

Patterns are stored as template strings that can be formatted with
resource-specific context (model names, fields, etc.).
"""

# ========== App-Wide Layout and Styling ==========

APP_LAYOUT = """import type {{ Metadata }} from "next";
import {{ Inter }} from "next/font/google";
import "./globals.css";

const inter = Inter({{ subsets: ["latin"] }});

export const metadata: Metadata = {{
  title: "{app_title}",
  description: "{app_description}",
}};

export default function RootLayout({{
  children,
}}: Readonly<{{
  children: React.ReactNode;
}}>) {{
  return (
    <html lang="en" className="dark">
      <body className={{`${{inter.className}} antialiased`}}>
        <div className="fixed inset-0 bg-gradient-to-br from-slate-900 via-purple-900/20 to-slate-900 -z-10" />
        <div className="fixed inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-900/20 via-transparent to-transparent -z-10" />
        {{children}}
      </body>
    </html>
  );
}}"""

APP_GLOBALS_CSS = """@tailwind base;
@tailwind components;
@tailwind utilities;

:root {{
  --background: #0f0f1a;
  --foreground: #e2e8f0;
}}

body {{
  color: var(--foreground);
  background: var(--background);
  min-height: 100vh;
}}

/* Custom scrollbar */
::-webkit-scrollbar {{
  width: 8px;
}}

::-webkit-scrollbar-track {{
  background: rgba(30, 41, 59, 0.3);
}}

::-webkit-scrollbar-thumb {{
  background: rgba(99, 102, 241, 0.5);
  border-radius: 4px;
}}

::-webkit-scrollbar-thumb:hover {{
  background: rgba(99, 102, 241, 0.7);
}}

@layer base {{
  /* Dark mode color scheme */
  html {{
    color-scheme: dark;
  }}

  /* Better form defaults for dark theme */
  input[type="checkbox"] {{
    color-scheme: dark;
  }}
}}

@layer components {{
  /* Glass card effect */
  .glass-card {{
    @apply bg-slate-800/50 backdrop-blur-xl border border-slate-700/50 rounded-2xl shadow-2xl;
  }}

  /* Button variants */
  .btn-primary {{
    @apply bg-gradient-to-r from-indigo-500 to-purple-500 text-white px-6 py-3 rounded-xl font-medium
           hover:from-indigo-600 hover:to-purple-600 transition-all duration-300
           hover:shadow-lg hover:shadow-indigo-500/25 active:scale-95 disabled:opacity-50;
  }}

  .btn-secondary {{
    @apply bg-slate-700/50 text-slate-200 px-6 py-3 rounded-xl font-medium border border-slate-600/50
           hover:bg-slate-700 transition-all duration-300 active:scale-95;
  }}

  .btn-danger {{
    @apply bg-gradient-to-r from-red-500 to-rose-500 text-white px-6 py-3 rounded-xl font-medium
           hover:from-red-600 hover:to-rose-600 transition-all duration-300
           hover:shadow-lg hover:shadow-red-500/25 active:scale-95 disabled:opacity-50;
  }}

  /* Input styling */
  .input-field {{
    @apply w-full px-4 py-3 bg-slate-900/50 border border-slate-700/50 rounded-xl
           text-slate-100 placeholder-slate-500
           focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50
           transition-all duration-300;
  }}

  /* Modern checkbox */
  .checkbox-modern {{
    @apply appearance-none w-6 h-6 rounded-lg border-2 border-slate-600 bg-slate-800/50
           checked:bg-gradient-to-r checked:from-indigo-500 checked:to-purple-500
           checked:border-transparent cursor-pointer transition-all duration-300
           hover:border-indigo-400 focus:ring-2 focus:ring-indigo-500/50;
  }}

  /* Page title with gradient */
  .page-title {{
    @apply text-4xl font-bold bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400
           bg-clip-text text-transparent;
  }}

  /* Back link styling */
  .link-back {{
    @apply inline-flex items-center gap-2 text-slate-400 hover:text-indigo-400
           transition-colors duration-300;
  }}
}}

@layer utilities {{
  .text-balance {{
    text-wrap: balance;
  }}
}}
"""

# ========== Landing Page Pattern ==========

LANDING_PAGE_WITH_LINKS = """import Link from "next/link";

export default function Home() {{
  return (
    <main className="min-h-screen">
      <div className="container mx-auto px-4 py-12 max-w-4xl">
        <h1 className="page-title mb-8">Welcome</h1>

        <div className="grid gap-6">
          <Link
            href="/{resource_plural}"
            className="glass-card p-6 block hover:border-indigo-500/50 transition-all duration-300 group"
          >
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-2xl font-semibold text-slate-100 mb-2 group-hover:text-indigo-400 transition-colors">{Resource}s</h2>
                <p className="text-slate-400">{link_description}</p>
              </div>
              <svg className="w-6 h-6 text-slate-500 group-hover:text-indigo-400 group-hover:translate-x-1 transition-all" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M9 5l7 7-7 7" />
              </svg>
            </div>
          </Link>
        </div>
      </div>
    </main>
  );
}}
"""

# ========== API Route Patterns (Next.js) ==========

API_ROUTE_GET = """export async function GET() {{
  try {{
    const {resource_plural} = await prisma.{resource}.findMany({{
      orderBy: {{ id: 'desc' }},
      take: 50
    }});
    return NextResponse.json({resource_plural});
  }} catch (error) {{
    console.error('GET /{resource}s error:', error);
    return NextResponse.json(
      {{ error: 'Failed to fetch {resource}s' }},
      {{ status: 500 }}
    );
  }}
}}"""

API_ROUTE_GET_PAGINATED = """export async function GET(request: Request) {{
  try {{
    const {{ searchParams }} = new URL(request.url);
    const page = parseInt(searchParams.get('page') || '1');
    const limit = parseInt(searchParams.get('limit') || '10');
    const skip = (page - 1) * limit;

    const [{resource_plural}, total] = await Promise.all([
      prisma.{resource}.findMany({{
        skip,
        take: limit,
        orderBy: {{ id: 'desc' }}
      }}),
      prisma.{resource}.count()
    ]);

    return NextResponse.json({{
      {resource_plural},
      pagination: {{
        page,
        limit,
        total,
        pages: Math.ceil(total / limit)
      }}
    }});
  }} catch (error) {{
    console.error('GET /{resource}s error:', error);
    return NextResponse.json(
      {{ error: 'Failed to fetch {resource}s' }},
      {{ status: 500 }}
    );
  }}
}}"""

API_ROUTE_POST = """export async function POST(request: Request) {{
  try {{
    const body = await request.json();

    // Validate request body
    const validatedData = {Resource}Schema.parse(body);

    const {resource} = await prisma.{resource}.create({{
      data: validatedData
    }});

    return NextResponse.json({resource}, {{ status: 201 }});
  }} catch (error) {{
    if (error instanceof z.ZodError) {{
      return NextResponse.json(
        {{ error: 'Invalid request data', details: error.issues }},
        {{ status: 400 }}
      );
    }}

    console.error('POST /{resource}s error:', error);
    return NextResponse.json(
      {{ error: 'Failed to create {resource}' }},
      {{ status: 500 }}
    );
  }}
}}"""

API_ROUTE_DYNAMIC_GET = """export async function GET(
  request: Request,
  {{ params }}: {{ params: {{ id: string }} }}
) {{
  try {{
    const id = parseInt(params.id);

    const {resource} = await prisma.{resource}.findUnique({{
      where: {{ id }}
    }});

    if (!{resource}) {{
      return NextResponse.json(
        {{ error: '{Resource} not found' }},
        {{ status: 404 }}
      );
    }}

    return NextResponse.json({resource});
  }} catch (error) {{
    console.error('GET /{resource}/[id] error:', error);
    return NextResponse.json(
      {{ error: 'Failed to fetch {resource}' }},
      {{ status: 500 }}
    );
  }}
}}"""

API_ROUTE_DYNAMIC_PATCH = """export async function PATCH(
  request: Request,
  {{ params }}: {{ params: {{ id: string }} }}
) {{
  try {{
    const id = parseInt(params.id);
    const body = await request.json();

    const validatedData = {Resource}UpdateSchema.parse(body);

    const {resource} = await prisma.{resource}.update({{
      where: {{ id }},
      data: validatedData
    }});

    return NextResponse.json({resource});
  }} catch (error) {{
    if (error instanceof z.ZodError) {{
      return NextResponse.json(
        {{ error: 'Invalid update data', details: error.issues }},
        {{ status: 400 }}
      );
    }}

    console.error('PATCH /{resource}/[id] error:', error);
    return NextResponse.json(
      {{ error: 'Failed to update {resource}' }},
      {{ status: 500 }}
    );
  }}
}}"""

API_ROUTE_DYNAMIC_DELETE = """export async function DELETE(
  request: Request,
  {{ params }}: {{ params: {{ id: string }} }}
) {{
  try {{
    const id = parseInt(params.id);

    await prisma.{resource}.delete({{
      where: {{ id }}
    }});

    return NextResponse.json({{ success: true }});
  }} catch (error) {{
    console.error('DELETE /{resource}/[id] error:', error);
    return NextResponse.json(
      {{ error: 'Failed to delete {resource}' }},
      {{ status: 500 }}
    );
  }}
}}"""

# ========== Validation Schema Patterns ==========


def generate_zod_schema(resource_name: str, fields: dict) -> str:
    """Generate Zod validation schema for a resource.

    Args:
        resource_name: Name of the resource (e.g., "todo", "user")
        fields: Dictionary of field names to types

    Returns:
        TypeScript code for Zod schema
    """
    schema_fields = []
    for field_name, field_type in fields.items():
        if field_name in ["id", "createdAt", "updatedAt"]:
            continue  # Skip auto-generated fields

        zod_type = _map_type_to_zod(field_type)
        schema_fields.append(f"  {field_name}: {zod_type}")

    resource_capitalized = resource_name.capitalize()

    return f"""const {resource_capitalized}Schema = z.object({{
{','.join(schema_fields)}
}});

const {resource_capitalized}UpdateSchema = {resource_capitalized}Schema.partial();

type {resource_capitalized} = z.infer<typeof {resource_capitalized}Schema>;"""


def _map_type_to_zod(field_type: str) -> str:
    """Map field type to Zod validation type."""
    # Normalize to lowercase for consistent lookup
    normalized = field_type.lower()

    type_mapping = {
        "string": "z.string().min(1)",
        "text": "z.string()",
        "int": "z.number().int()",
        "number": "z.number().int()",
        "float": "z.number()",
        "boolean": "z.boolean()",
        "date": "z.coerce.date()",
        "datetime": "z.coerce.date()",
        "timestamp": "z.coerce.date()",
        "email": "z.string().email()",
        "url": "z.string().url()",
    }
    return type_mapping.get(normalized, "z.string()")


# ========== React Component Patterns ==========

SERVER_COMPONENT_LIST = """import {{ prisma }} from "@/lib/prisma";
import Link from "next/link";

async function get{Resource}s() {{
  const {resource_plural} = await prisma.{resource}.findMany({{
    orderBy: {{ id: "desc" }},
    take: 50
  }});
  return {resource_plural};
}}

export default async function {Resource}sPage() {{
  const {resource_plural} = await get{Resource}s();

  return (
    <div className="min-h-screen">
      <div className="container mx-auto px-4 py-12 max-w-4xl">
        {{/* Header */}}
        <div className="mb-10">
          <h1 className="text-4xl font-bold bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent mb-2">
            {Resource}s
          </h1>
          <p className="text-slate-400">
            {{{resource_plural}.length === 0
              ? "No items yet. Create your first one!"
              : `${{({resource_plural} as any[]).filter(t => !(t as any).completed).length}} pending items`}}
          </p>
        </div>

        {{/* Add Button */}}
        <div className="mb-8">
          <Link
            href="/{resource}s/new"
            className="inline-flex items-center gap-2 bg-gradient-to-r from-indigo-500 to-purple-500 text-white px-6 py-3 rounded-xl font-medium hover:from-indigo-600 hover:to-purple-600 transition-all duration-300 hover:shadow-lg hover:shadow-indigo-500/25"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M12 4v16m8-8H4" />
            </svg>
            Add New {Resource}
          </Link>
        </div>

        {{/* List */}}
        <div className="bg-slate-800/50 backdrop-blur-xl border border-slate-700/50 rounded-2xl shadow-2xl p-6">
          {{{resource_plural}.length === 0 ? (
            <div className="text-center py-16">
              <div className="w-20 h-20 mx-auto mb-6 rounded-full bg-slate-800/50 flex items-center justify-center">
                <svg className="w-10 h-10 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{1.5}} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
              </div>
              <h3 className="text-xl font-medium text-slate-300 mb-2">No {resource}s yet</h3>
              <p className="text-slate-500 mb-6">Create your first item to get started</p>
              <Link
                href="/{resource}s/new"
                className="inline-flex items-center gap-2 bg-gradient-to-r from-indigo-500 to-purple-500 text-white px-6 py-3 rounded-xl font-medium hover:from-indigo-600 hover:to-purple-600 transition-all duration-300"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M12 4v16m8-8H4" />
                </svg>
                Create {Resource}
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              {{{resource_plural}.map((item) => (
                <Link
                  key={{item.id}}
                  href={{`/{resource}s/${{item.id}}`}}
                  className="block p-5 rounded-xl bg-slate-800/30 border border-slate-700/30 hover:bg-slate-800/50 hover:border-indigo-500/30 transition-all duration-300"
                >
                  {field_display}
                </Link>
              ))}}
            </div>
          )}}
        </div>
      </div>
    </div>
  );
}}"""

CLIENT_COMPONENT_FORM = """"use client";

import {{ useState, useEffect }} from "react";
import {{ useRouter }} from "next/navigation";
import type {{ {Resource} }} from "@prisma/client";

interface {Resource}FormProps {{
  initialData?: Partial<{Resource}>;
  mode?: "create" | "edit";
}}

export function {Resource}Form({{ initialData, mode = "create" }}: {Resource}FormProps) {{
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [formData, setFormData] = useState({{
{form_state_fields}
  }});
  const dateFields = {date_fields};

  const normalizePayload = (data: typeof formData) => {{
    if (dateFields.length === 0) {{
      return data;
    }}

    const normalized = {{ ...data }};

    dateFields.forEach((field) => {{
      const value = normalized[field as keyof typeof normalized];
      if (!value) {{
        return;
      }}

      const parsedValue = new Date(value as string | number | Date);
      if (!Number.isNaN(parsedValue.getTime())) {{
        (normalized as any)[field] = parsedValue.toISOString();
      }}
    }});

    return normalized;
  }};

  // Initialize form with initialData when in edit mode
  useEffect(() => {{
    if (initialData && mode === "edit") {{
      setFormData(prev => ({{
        ...prev,
        ...Object.fromEntries(
          Object.entries(initialData).filter(([key]) =>
            !["id", "createdAt", "updatedAt"].includes(key)
          )
        )
      }}));
    }}
  }}, [initialData, mode]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {{
    const {{ name, value, type }} = e.target;
    const checked = (e.target as HTMLInputElement).checked;

    setFormData(prev => ({{
      ...prev,
      [name]: type === "checkbox" ? checked : type === "number" ? parseFloat(value) : value
    }}));
  }};

  const handleSubmit = async (e: React.FormEvent) => {{
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {{
      const url = mode === "create"
        ? "/api/{resource}s"
        : `/api/{resource}s/${{initialData?.id}}`;

      const method = mode === "create" ? "POST" : "PATCH";
      const payload = normalizePayload(formData);

      const response = await fetch(url, {{
        method,
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload)
      }});

      if (!response.ok) {{
        const data = await response.json();
        throw new Error(data.error || "Operation failed");
      }}

      router.push("/{resource}s");
      router.refresh();
    }} catch (err) {{
      setError(err instanceof Error ? err.message : "An error occurred");
    }} finally {{
      setLoading(false);
    }}
  }};

  return (
    <form onSubmit={{handleSubmit}} className="space-y-6">
{form_fields}

      {{error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl">
          {{error}}
        </div>
      )}}

      <div className="flex gap-4 pt-4">
        <button
          type="submit"
          disabled={{loading}}
          className="flex-1 bg-gradient-to-r from-indigo-500 to-purple-500 text-white py-3 px-6 rounded-xl font-medium hover:from-indigo-600 hover:to-purple-600 transition-all duration-300 hover:shadow-lg hover:shadow-indigo-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{loading ? "Saving..." : mode === "create" ? "Create {Resource}" : "Save Changes"}}
        </button>
        <button
          type="button"
          onClick={{() => router.back()}}
          className="px-6 py-3 bg-slate-700/50 text-slate-200 rounded-xl font-medium border border-slate-600/50 hover:bg-slate-700 transition-all duration-300"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}}"""

CLIENT_COMPONENT_NEW_PAGE = """"use client";

import {{ {Resource}Form }} from "@/components/{Resource}Form";
import Link from "next/link";

export default function New{Resource}Page() {{
  return (
    <div className="min-h-screen">
      <div className="container mx-auto px-4 py-12 max-w-2xl">
        <div className="mb-8">
          <Link
            href="/{resource}s"
            className="link-back group"
          >
            <svg className="w-5 h-5 transition-transform duration-300 group-hover:-translate-x-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M15 19l-7-7 7-7" />
            </svg>
            Back to {Resource}s
          </Link>
        </div>

        <div className="glass-card p-8">
          <h1 className="page-title mb-8">
            Create New {Resource}
          </h1>

          <{Resource}Form mode="create" />
        </div>
      </div>
    </div>
  );
}}"""

SERVER_COMPONENT_DETAIL = """"use client";

import {{ useRouter }} from "next/navigation";
import {{ useState, useEffect }} from "react";
import Link from "next/link";

interface {Resource}Data {{
  id: number;
{interface_fields}
  createdAt: string;
  updatedAt: string;
}}

export default function {Resource}EditPage({{
  params
}}: {{
  params: {{ id: string }}
}}) {{
  const router = useRouter();
  const id = parseInt(params.id);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [{resource}, set{Resource}] = useState<{Resource}Data | null>(null);

  // Form state - populated from API
{form_state}

  // Fetch data on mount
  useEffect(() => {{
    async function fetchData() {{
      try {{
        const response = await fetch(`/api/{resource}s/${{id}}`);
        if (!response.ok) {{
          if (response.status === 404) {{
            router.push("/{resource}s");
            return;
          }}
          throw new Error("Failed to fetch {resource}");
        }}
        const data = await response.json();
        set{Resource}(data);
        // Populate form fields
{populate_fields}
        setLoading(false);
      }} catch (err) {{
        setError(err instanceof Error ? err.message : "An error occurred");
        setLoading(false);
      }}
    }}
    fetchData();
  }}, [id, router]);

  const handleSave = async (e: React.FormEvent) => {{
    e.preventDefault();
    setSaving(true);
    setError(null);

    try {{
      const response = await fetch(`/api/{resource}s/${{id}}`, {{
        method: "PATCH",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
{save_body}
        }}),
      }});

      if (!response.ok) {{
        const data = await response.json();
        throw new Error(data.error || "Failed to update {resource}");
      }}

      router.push("/{resource}s");
      router.refresh();
    }} catch (err) {{
      setError(err instanceof Error ? err.message : "An error occurred");
      setSaving(false);
    }}
  }};

  const handleDelete = async () => {{
    if (!confirm("Are you sure you want to delete this {resource}?")) {{
      return;
    }}

    setDeleting(true);
    setError(null);

    try {{
      const response = await fetch(`/api/{resource}s/${{id}}`, {{
        method: "DELETE"
      }});

      if (!response.ok) {{
        throw new Error("Failed to delete {resource}");
      }}

      router.push("/{resource}s");
      router.refresh();
    }} catch (err) {{
      setError(err instanceof Error ? err.message : "An error occurred");
      setDeleting(false);
    }}
  }};

  if (loading) {{
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-slate-400">Loading...</div>
      </div>
    );
  }}

  if (!{resource}) {{
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-slate-400">{Resource} not found</div>
      </div>
    );
  }}

  return (
    <div className="min-h-screen">
      <div className="container mx-auto px-4 py-12 max-w-2xl">
        <div className="mb-8">
          <Link
            href="/{resource}s"
            className="link-back group"
          >
            <svg className="w-5 h-5 transition-transform duration-300 group-hover:-translate-x-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M15 19l-7-7 7-7" />
            </svg>
            Back to {Resource}s
          </Link>
        </div>

        <div className="glass-card p-8">
          <h1 className="page-title mb-8">
            Edit {Resource}
          </h1>

          {{error && (
            <div className="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400">
              {{error}}
            </div>
          )}}

          <form onSubmit={{handleSave}}>
{form_fields}

            <div className="flex gap-4 mt-8">
              <button
                type="submit"
                disabled={{saving}}
                className="btn-primary flex-1"
              >
                {{saving ? "Saving..." : "Save Changes"}}
              </button>
              <button
                type="button"
                onClick={{handleDelete}}
                disabled={{deleting}}
                className="btn-danger"
              >
                {{deleting ? "Deleting..." : "Delete"}}
              </button>
            </div>
          </form>

          <div className="mt-8 pt-6 border-t border-slate-700/50 text-sm text-slate-500">
            <p><strong className="text-slate-400">Created:</strong> {{new Date({resource}.createdAt).toLocaleString()}}</p>
            <p className="mt-1"><strong className="text-slate-400">Updated:</strong> {{new Date({resource}.updatedAt).toLocaleString()}}</p>
          </div>
        </div>
      </div>
    </div>
  );
}}"""

CLIENT_COMPONENT_ACTIONS = """"use client";

import {{ useRouter }} from "next/navigation";
import {{ useState }} from "react";
import {{ {Resource}Form }} from "@/components/{Resource}Form";
import type {{ {Resource} }} from "@prisma/client";

interface {Resource}ActionsProps {{
  {resource}Id: number;
  {resource}Data?: {Resource};
}}

export function {Resource}Actions({{ {resource}Id, {resource}Data }}: {Resource}ActionsProps) {{
  const router = useRouter();
  const [isEditing, setIsEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {{
    if (!confirm("Are you sure you want to delete this {resource}?")) {{
      return;
    }}

    setDeleting(true);
    setError(null);

    try {{
      const response = await fetch(`/api/{resource}s/${{{resource}Id}}`, {{
        method: "DELETE"
      }});

      if (!response.ok) {{
        throw new Error("Failed to delete {resource}");
      }}

      router.push("/{resource}s");
      router.refresh();
    }} catch (err) {{
      setError(err instanceof Error ? err.message : "An error occurred");
      setDeleting(false);
    }}
  }};

  if (isEditing && {resource}Data) {{
    return (
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
        <div className="bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
          <div className="p-6 border-b border-slate-700">
            <div className="flex justify-between items-center">
              <h2 className="text-xl font-bold text-slate-100">Edit {Resource}</h2>
              <button
                onClick={{() => setIsEditing(false)}}
                className="text-slate-400 hover:text-slate-200 transition-colors"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          <div className="p-6">
            <{Resource}Form initialData={{{resource}Data}} mode="edit" />
          </div>
        </div>
      </div>
    );
  }}

  return (
    <div className="flex items-center gap-3">
      {{error && (
        <div className="absolute top-full right-0 mt-2 bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl text-sm whitespace-nowrap">
          {{error}}
        </div>
      )}}
      <button
        onClick={{() => setIsEditing(true)}}
        className="inline-flex items-center gap-2 bg-slate-700/50 text-slate-200 px-4 py-2 rounded-xl font-medium border border-slate-600/50 hover:bg-slate-700 transition-all duration-300"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
        </svg>
        Edit
      </button>
      <button
        onClick={{handleDelete}}
        disabled={{deleting}}
        className="inline-flex items-center gap-2 bg-gradient-to-r from-red-500 to-rose-500 text-white px-4 py-2 rounded-xl font-medium hover:from-red-600 hover:to-rose-600 transition-all duration-300 hover:shadow-lg hover:shadow-red-500/25 disabled:opacity-50"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{2}} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
        {{deleting ? "Deleting..." : "Delete"}}
      </button>
    </div>
  );
}}"""

CLIENT_COMPONENT_DETAIL_PAGE = """"use client";

import {{ useState, useEffect }} from "react";
import {{ useRouter }} from "next/navigation";
import {{ {Resource}Form }} from "@/components/{Resource}Form";
import Link from "next/link";

interface {Resource} {{
  id: number;
{type_fields}
  createdAt: Date;
  updatedAt: Date;
}}

export default function {Resource}DetailPage({{ params }}: {{ params: {{ id: string }} }}) {{
  const router = useRouter();
  const [{resource}, set{Resource}] = useState<{Resource} | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {{
    fetch{Resource}();
  }}, [params.id]);

  const fetch{Resource} = async () => {{
    try {{
      const response = await fetch(`/api/{resource}s/${{params.id}}`);
      if (!response.ok) {{
        throw new Error("Failed to fetch {resource}");
      }}
      const data = await response.json();
      set{Resource}(data);
    }} catch (err) {{
      setError(err instanceof Error ? err.message : "An error occurred");
    }} finally {{
      setLoading(false);
    }}
  }};

  const handleDelete = async () => {{
    if (!confirm("Are you sure you want to delete this {resource}?")) return;

    setDeleting(true);
    try {{
      const response = await fetch(`/api/{resource}s/${{params.id}}`, {{
        method: "DELETE"
      }});

      if (!response.ok) {{
        throw new Error("Failed to delete {resource}");
      }}

      router.push("/{resource}s");
      router.refresh();
    }} catch (err) {{
      setError(err instanceof Error ? err.message : "An error occurred");
      setDeleting(false);
    }}
  }};

  if (loading) {{
    return (
      <div className="container mx-auto p-8 max-w-2xl">
        <div className="text-center py-12">Loading...</div>
      </div>
    );
  }}

  if (error || !{resource}) {{
    return (
      <div className="container mx-auto p-8 max-w-2xl">
        <div className="text-center py-12">
          <p className="text-red-500 mb-4">{{error || "{Resource} not found"}}</p>
          <Link href="/{resource}s" className="text-blue-500 hover:underline">
            Back to {Resource}s
          </Link>
        </div>
      </div>
    );
  }}

  return (
    <div className="container mx-auto p-8 max-w-2xl">
      <div className="mb-6">
        <Link href="/{resource}s" className="text-blue-500 hover:underline">
          ← Back to {Resource}s
        </Link>
      </div>

      <h1 className="text-3xl font-bold mb-6">Edit {Resource}</h1>

      <{Resource}Form initialData={{{resource}}} mode="edit" />

      <div className="mt-6 pt-6 border-t border-gray-200">
        <button
          onClick={{handleDelete}}
          disabled={{deleting}}
          className="bg-red-500 text-white px-6 py-2 rounded-md hover:bg-red-600 disabled:opacity-50"
        >
          {{deleting ? "Deleting..." : "Delete {Resource}"}}
        </button>
      </div>

      <div className="mt-6 bg-gray-50 rounded-lg p-4 text-sm text-gray-600">
        <p><strong>Created:</strong> {{new Date({resource}.createdAt).toLocaleString()}}</p>
        <p><strong>Updated:</strong> {{new Date({resource}.updatedAt).toLocaleString()}}</p>
      </div>
    </div>
  );
}}"""


def generate_form_field(field_name: str, field_type: str) -> str:
    """Generate a form field based on type with modern styling."""
    input_type = {
        "string": "text",
        "text": "textarea",
        "number": "number",
        "email": "email",
        "url": "url",
        "boolean": "checkbox",
        "date": "date",
    }.get(field_type.lower(), "text")

    label = field_name.replace("_", " ").title()

    if input_type == "textarea":
        return f"""      <div>
        <label htmlFor="{field_name}" className="block text-sm font-medium text-slate-300 mb-2">
          {label}
        </label>
        <textarea
          id="{field_name}"
          name="{field_name}"
          value={{formData.{field_name}}}
          onChange={{handleChange}}
          rows={{4}}
          className="w-full px-4 py-3 bg-slate-900/50 border border-slate-700/50 rounded-xl text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-all duration-300"
          required
        />
      </div>"""
    elif input_type == "checkbox":
        return f"""      <div className="flex items-center gap-3 p-4 bg-slate-900/30 rounded-xl border border-slate-700/30">
        <input
          type="checkbox"
          id="{field_name}"
          name="{field_name}"
          checked={{formData.{field_name}}}
          onChange={{handleChange}}
          className="w-5 h-5 rounded-lg border-2 border-slate-600 bg-slate-800/50 checked:bg-gradient-to-r checked:from-indigo-500 checked:to-purple-500 checked:border-transparent focus:ring-2 focus:ring-indigo-500/50 cursor-pointer transition-all duration-300"
        />
        <label htmlFor="{field_name}" className="text-slate-300 cursor-pointer">
          {label}
        </label>
      </div>"""
    else:
        return f"""      <div>
        <label htmlFor="{field_name}" className="block text-sm font-medium text-slate-300 mb-2">
          {label}
        </label>
        <input
          type="{input_type}"
          id="{field_name}"
          name="{field_name}"
          value={{formData.{field_name}}}
          onChange={{handleChange}}
          className="w-full px-4 py-3 bg-slate-900/50 border border-slate-700/50 rounded-xl text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500/50 transition-all duration-300"
          required
        />
      </div>"""


# ========== Import Generation ==========


def generate_api_imports(_operations: list, uses_validation: bool = True) -> str:
    """Generate appropriate imports for API routes."""
    imports = [
        'import { NextResponse } from "next/server";',
        'import { prisma } from "@/lib/prisma";',
    ]

    if uses_validation:
        imports.append('import { z } from "zod";')

    return "\n".join(imports)


def generate_component_imports(component_type: str, uses_data: bool = False) -> str:
    """Generate appropriate imports for React components."""
    imports = []

    if component_type == "client":
        imports.extend(
            [
                '"use client";',
                "",
                'import { useState } from "react";',
                'import { useRouter } from "next/navigation";',
            ]
        )
    elif uses_data:
        imports.append('import { prisma } from "@/lib/prisma";')

    imports.append('import Link from "next/link";')

    return "\n".join(imports)


# ========== Helper Functions ==========


def pluralize(word: str) -> str:
    """Simple pluralization (can be enhanced)."""
    if word.endswith("y"):
        return word[:-1] + "ies"
    elif word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    else:
        return word + "s"


def generate_field_display(fields: dict, max_fields: int = 3) -> str:
    """Generate JSX for displaying resource fields with type-aware rendering.

    Boolean fields render as checkboxes with strikethrough styling for completed items.
    String fields render as text with proper hierarchy. Uses modern dark theme styling.

    Args:
        fields: Dictionary mapping field names to their types (e.g., {"title": "string", "completed": "boolean"})
        max_fields: Maximum number of fields to display (default: 3)

    Returns:
        JSX string for field display
    """
    display_fields = []
    title_field = None
    boolean_field = None

    # Find primary title field and boolean field
    for field_name, field_type in fields.items():
        if field_name.lower() in {"id", "createdat", "updatedat"}:
            continue
        if field_name.lower() in {"title", "name"} and not title_field:
            title_field = field_name
        if field_type.lower() == "boolean" and not boolean_field:
            boolean_field = field_name

    # Generate checkbox + title combo for boolean fields (e.g., completed todo)
    if boolean_field and title_field:
        # Render checkbox with title that has strikethrough when boolean is true
        display_fields.append(
            f"""<div className="flex items-center gap-4">
                    <div className="relative">
                      <input
                        type="checkbox"
                        checked={{item.{boolean_field}}}
                        readOnly
                        className="w-6 h-6 rounded-lg border-2 border-slate-600 bg-slate-800/50 checked:bg-gradient-to-r checked:from-indigo-500 checked:to-purple-500 checked:border-transparent appearance-none cursor-pointer transition-all duration-300"
                      />
                      {{item.{boolean_field} && (
                        <svg className="absolute inset-0 w-6 h-6 text-white pointer-events-none p-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{3}} d="M5 13l4 4L19 7" />
                        </svg>
                      )}}
                    </div>
                    <h3 className={{`font-semibold text-lg ${{item.{boolean_field} ? "line-through text-slate-500" : "text-slate-100"}}`}}>
                      {{item.{title_field}}}
                    </h3>
                  </div>"""
        )
    elif title_field:
        # Just render title without checkbox
        display_fields.append(
            f'<h3 className="font-semibold text-lg text-slate-100">{{item.{title_field}}}</h3>'
        )

    # Add remaining non-boolean, non-title fields as secondary text
    for field_name, field_type in list(fields.items())[:max_fields]:
        if field_name.lower() in {"id", "createdat", "updatedat"}:
            continue
        if field_name == title_field or field_name == boolean_field:
            continue
        if field_type.lower() != "boolean":
            display_fields.append(
                f'<p className="text-slate-400 text-sm mt-1">{{item.{field_name}}}</p>'
            )

    return (
        "\n                  ".join(display_fields)
        if display_fields
        else '<p className="text-slate-400">{{item.id}}</p>'
    )


def generate_new_page(resource_name: str) -> str:
    """Generate a 'new' page component that uses the form component.

    Args:
        resource_name: Name of the resource (e.g., "todo", "product")

    Returns:
        Complete TypeScript/React page component code
    """
    resource = resource_name.lower()
    Resource = resource_name.capitalize()

    return CLIENT_COMPONENT_NEW_PAGE.format(resource=resource, Resource=Resource)


def generate_detail_page(resource_name: str, fields: dict) -> str:
    """Generate an edit page with pre-populated form fields.

    Args:
        resource_name: Name of the resource (e.g., "todo", "product")
        fields: Dictionary of field names to types

    Returns:
        Complete TypeScript/React page component code
    """
    resource = resource_name.lower()
    Resource = resource_name.capitalize()

    # Generate TypeScript interface fields
    interface_lines = []
    form_state_lines = []
    populate_lines = []
    save_body_lines = []
    form_field_lines = []

    for field_name, field_type in fields.items():
        if field_name.lower() in {"id", "createdat", "updatedat"}:
            continue

        # TypeScript types
        ts_type = _get_typescript_type(field_type)
        interface_lines.append(f"  {field_name}: {ts_type};")

        # useState declarations
        default_val = _get_default_value(field_type)
        form_state_lines.append(
            f"  const [{field_name}, set{field_name.capitalize()}] = useState<{ts_type}>({default_val});"
        )

        # Populate from API response
        populate_lines.append(
            f"        set{field_name.capitalize()}(data.{field_name});"
        )

        # Save body
        save_body_lines.append(f"          {field_name},")

        # Form field JSX
        label = field_name.replace("_", " ").title()
        form_field_lines.append(
            _generate_edit_form_field(field_name, field_type, label)
        )

    return SERVER_COMPONENT_DETAIL.format(
        resource=resource,
        Resource=Resource,
        interface_fields="\n".join(interface_lines),
        form_state="\n".join(form_state_lines),
        populate_fields="\n".join(populate_lines),
        save_body="\n".join(save_body_lines),
        form_fields="\n\n".join(form_field_lines),
    )


def _get_typescript_type(field_type: str) -> str:
    """Convert field type to TypeScript type."""
    type_lower = field_type.lower()
    if type_lower == "boolean":
        return "boolean"
    if type_lower in ["number", "int", "integer", "float"]:
        return "number"
    return "string"


def _get_default_value(field_type: str) -> str:
    """Get default value for useState based on field type."""
    type_lower = field_type.lower()
    if type_lower == "boolean":
        return "false"
    if type_lower in ["number", "int", "integer", "float"]:
        return "0"
    return '""'


def _generate_edit_form_field(field_name: str, field_type: str, label: str) -> str:
    """Generate a single form field for editing."""
    type_lower = field_type.lower()

    if type_lower == "boolean":
        return f"""            <div className="mb-6">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={{{field_name}}}
                  onChange={{(e) => set{field_name.capitalize()}(e.target.checked)}}
                  className="w-6 h-6 rounded-lg border-2 border-slate-600 bg-slate-800/50 checked:bg-gradient-to-r checked:from-indigo-500 checked:to-purple-500 checked:border-transparent appearance-none cursor-pointer transition-all duration-300"
                />
                <span className="text-slate-200 font-medium">{label}</span>
              </label>
            </div>"""
    elif type_lower in ["number", "int", "integer", "float"]:
        return f"""            <div className="mb-6">
              <label className="block text-sm font-medium text-slate-400 mb-2">{label}</label>
              <input
                type="number"
                value={{{field_name}}}
                onChange={{(e) => set{field_name.capitalize()}(parseFloat(e.target.value) || 0)}}
                className="input-field"
              />
            </div>"""
    else:
        # String/text fields
        return f"""            <div className="mb-6">
              <label className="block text-sm font-medium text-slate-400 mb-2">{label}</label>
              <input
                type="text"
                value={{{field_name}}}
                onChange={{(e) => set{field_name.capitalize()}(e.target.value)}}
                className="input-field"
                required
              />
            </div>"""


def generate_actions_component(resource_name: str) -> str:
    """Generate the actions component for delete functionality.

    Args:
        resource_name: Name of the resource (e.g., "todo", "product")

    Returns:
        Complete TypeScript/React client component code
    """
    resource = resource_name.lower()
    Resource = resource_name.capitalize()

    return CLIENT_COMPONENT_ACTIONS.format(resource=resource, Resource=Resource)


def _generate_detail_field_display(resource: str, fields: dict) -> str:
    """Generate JSX for displaying resource fields in detail view.

    Boolean fields render as visual checkboxes with strikethrough for completed items.
    Uses modern dark theme styling.

    Args:
        resource: Resource variable name (e.g., "todo")
        fields: Dictionary mapping field names to their types

    Returns:
        JSX string for detail field display
    """
    display_fields = []

    # Find title and boolean fields for special combined rendering
    title_field = None
    boolean_field = None
    for field_name, field_type in fields.items():
        if field_name.lower() in {"title", "name"} and not title_field:
            title_field = field_name
        if field_type.lower() == "boolean" and not boolean_field:
            boolean_field = field_name

    for field_name, field_type in fields.items():
        if field_name.lower() in {"id", "createdat", "updatedat"}:
            continue

        label = field_name.replace("_", " ").title()

        if field_type.lower() == "boolean":
            # Render checkbox with visual feedback
            display_fields.append(
                f'        <div className="mb-6 p-4 bg-slate-900/30 rounded-xl border border-slate-700/30">\n'
                f'          <label className="block text-sm font-medium text-slate-400 mb-3">{label}</label>\n'
                f'          <div className="flex items-center gap-3">\n'
                f'            <div className="relative">\n'
                f"              <input\n"
                f'                type="checkbox"\n'
                f"                checked={{{resource}.{field_name}}}\n"
                f"                readOnly\n"
                f'                className="w-6 h-6 rounded-lg border-2 border-slate-600 bg-slate-800/50 checked:bg-gradient-to-r checked:from-indigo-500 checked:to-purple-500 checked:border-transparent appearance-none cursor-default transition-all duration-300"\n'
                f"              />\n"
                f"              {{{resource}.{field_name} && (\n"
                f'                <svg className="absolute inset-0 w-6 h-6 text-white pointer-events-none p-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">\n'
                f'                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={{3}} d="M5 13l4 4L19 7" />\n'
                f"                </svg>\n"
                f"              )}}\n"
                f"            </div>\n"
                f'            <span className={{{resource}.{field_name} ? "text-emerald-400 font-medium" : "text-slate-500"}}>\n'
                f'              {{{resource}.{field_name} ? "Yes" : "No"}}\n'
                f"            </span>\n"
                f"          </div>\n"
                f"        </div>"
            )
        elif field_type.lower() in ["date", "datetime", "timestamp"]:
            display_fields.append(
                f'        <div className="mb-6">\n'
                f'          <label className="block text-sm font-medium text-slate-400 mb-2">{label}</label>\n'
                f'          <p className="text-lg text-slate-200">{{new Date({resource}.{field_name}).toLocaleDateString()}}</p>\n'
                f"        </div>"
            )
        else:
            # For title field with boolean, add strikethrough styling
            if field_name == title_field and boolean_field:
                class_expr = f'{{{resource}.{boolean_field} ? "text-xl text-slate-500 line-through" : "text-xl text-slate-100"}}'
                display_fields.append(
                    f'        <div className="mb-6">\n'
                    f'          <label className="block text-sm font-medium text-slate-400 mb-2">{label}</label>\n'
                    f"          <p className={class_expr}>{{{resource}.{field_name}}}</p>\n"
                    f"        </div>"
                )
            else:
                display_fields.append(
                    f'        <div className="mb-6">\n'
                    f'          <label className="block text-sm font-medium text-slate-400 mb-2">{label}</label>\n'
                    f'          <p className="text-xl text-slate-100">{{{resource}.{field_name}}}</p>\n'
                    f"        </div>"
                )

    return (
        "\n".join(display_fields)
        if display_fields
        else '        <p className="text-slate-400">No fields to display</p>'
    )


def _map_type_to_typescript(field_type: str) -> str:
    """Map field type to TypeScript type."""
    type_mapping = {
        "string": "string",
        "text": "string",
        "number": "number",
        "float": "number",
        "boolean": "boolean",
        "date": "Date",
        "datetime": "Date",
        "timestamp": "Date",
        "email": "string",
        "url": "string",
    }
    return type_mapping.get(field_type.lower(), "string")


# ========== Test Templates (Vitest) ==========

VITEST_CONFIG = """import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['**/__tests__/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
"""

TEST_SETUP = """import '@testing-library/jest-dom';
import {{ vi }} from 'vitest';

// Mock next/navigation
vi.mock('next/navigation', () => ({{
  useRouter: () => ({{
    push: vi.fn(),
    back: vi.fn(),
    refresh: vi.fn(),
    replace: vi.fn(),
  }}),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}}));

// Mock Prisma client
vi.mock('@/lib/prisma', () => ({{
  prisma: {{
    {resource}: {{
      findMany: vi.fn(),
      findUnique: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      count: vi.fn(),
    }},
  }},
}}));
"""


COMPONENT_TEST_FORM = """import {{ describe, it, expect, vi, beforeEach }} from 'vitest';
import {{ render, screen, fireEvent, waitFor }} from '@testing-library/react';
import {{ {Resource}Form }} from '../{Resource}Form';

// Mock fetch
global.fetch = vi.fn();

describe('{Resource}Form', () => {{
  beforeEach(() => {{
    vi.clearAllMocks();
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({{
      ok: true,
      json: () => Promise.resolve({{ id: 1 }}),
    }});
  }});

  it('renders all form fields', () => {{
    render(<{Resource}Form />);

    {form_field_assertions}
  }});

  it('submits form data correctly in create mode', async () => {{
    render(<{Resource}Form mode="create" />);

    {form_fill_actions}

    fireEvent.click(screen.getByRole('button', {{ name: /create/i }}));

    await waitFor(() => {{
      expect(fetch).toHaveBeenCalledWith('/api/{resource_plural}', expect.objectContaining({{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
      }}));
    }});
  }});

  it('submits form data correctly in edit mode', async () => {{
    const initialData = {{ id: 1, {test_data_fields} }};
    render(<{Resource}Form initialData={{initialData}} mode="edit" />);

    fireEvent.click(screen.getByRole('button', {{ name: /update/i }}));

    await waitFor(() => {{
      expect(fetch).toHaveBeenCalledWith('/api/{resource_plural}/1', expect.objectContaining({{
        method: 'PATCH',
      }}));
    }});
  }});

  it('displays error message on failed submission', async () => {{
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({{
      ok: false,
      json: () => Promise.resolve({{ error: 'Validation failed' }}),
    }});

    render(<{Resource}Form />);

    {form_fill_actions}

    fireEvent.click(screen.getByRole('button', {{ name: /create/i }}));

    await waitFor(() => {{
      expect(screen.getByText(/validation failed/i)).toBeInTheDocument();
    }});
  }});
}});
"""

COMPONENT_TEST_ACTIONS = """import {{ describe, it, expect, vi, beforeEach }} from 'vitest';
import {{ render, screen, fireEvent, waitFor }} from '@testing-library/react';
import {{ {Resource}Actions }} from '../{Resource}Actions';

// Mock fetch and confirm
global.fetch = vi.fn();
global.confirm = vi.fn();

describe('{Resource}Actions', () => {{
  beforeEach(() => {{
    vi.clearAllMocks();
    (global.confirm as ReturnType<typeof vi.fn>).mockReturnValue(true);
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({{
      ok: true,
      json: () => Promise.resolve({{ success: true }}),
    }});
  }});

  it('renders delete button', () => {{
    render(<{Resource}Actions {resource}Id={{1}} />);

    expect(screen.getByRole('button', {{ name: /delete/i }})).toBeInTheDocument();
  }});

  it('confirms before deleting', async () => {{
    render(<{Resource}Actions {resource}Id={{1}} />);

    fireEvent.click(screen.getByRole('button', {{ name: /delete/i }}));

    expect(confirm).toHaveBeenCalled();
  }});

  it('calls delete API on confirmation', async () => {{
    render(<{Resource}Actions {resource}Id={{1}} />);

    fireEvent.click(screen.getByRole('button', {{ name: /delete/i }}));

    await waitFor(() => {{
      expect(fetch).toHaveBeenCalledWith('/api/{resource_plural}/1', {{
        method: 'DELETE',
      }});
    }});
  }});

  it('does not call API when delete is cancelled', () => {{
    (global.confirm as ReturnType<typeof vi.fn>).mockReturnValue(false);

    render(<{Resource}Actions {resource}Id={{1}} />);

    fireEvent.click(screen.getByRole('button', {{ name: /delete/i }}));

    expect(fetch).not.toHaveBeenCalled();
  }});

  it('displays error message on failed deletion', async () => {{
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({{
      ok: false,
    }});

    render(<{Resource}Actions {resource}Id={{1}} />);

    fireEvent.click(screen.getByRole('button', {{ name: /delete/i }}));

    await waitFor(() => {{
      expect(screen.getByText(/failed to delete/i)).toBeInTheDocument();
    }});
  }});
}});
"""


def generate_test_data_fields(fields: dict, variant: int = 1) -> str:
    """Generate test data fields for test templates.

    Args:
        fields: Dictionary of field names to types
        variant: Variant number for different test data

    Returns:
        String of test data field assignments
    """
    test_values = []
    for field_name, field_type in fields.items():
        if field_name in ["id", "createdAt", "updatedAt"]:
            continue

        normalized_type = field_type.lower()
        if normalized_type == "boolean":
            value = "true" if variant == 1 else "false"
        elif normalized_type in ["number", "int", "float"]:
            value = str(variant * 10)
        elif normalized_type == "email":
            value = f'"test{variant}@example.com"'
        elif normalized_type == "url":
            value = f'"https://example{variant}.com"'
        else:
            value = f'"{field_name.title()} {variant}"'

        test_values.append(f"{field_name}: {value}")

    return ", ".join(test_values)


def generate_form_field_assertions(fields: dict) -> str:
    """Generate test assertions for form field presence.

    Args:
        fields: Dictionary of field names to types

    Returns:
        String of expect statements
    """
    assertions = []
    for field_name, field_type in fields.items():
        if field_name in ["id", "createdAt", "updatedAt"]:
            continue

        label = field_name.replace("_", " ").title()
        if field_type.lower() == "boolean":
            assertions.append(
                f"expect(screen.getByLabelText(/{label}/i)).toBeInTheDocument();"
            )
        else:
            assertions.append(
                f"expect(screen.getByLabelText(/{label}/i)).toBeInTheDocument();"
            )

    return "\n    ".join(assertions)


def generate_form_fill_actions(fields: dict) -> str:
    """Generate test actions to fill form fields.

    Args:
        fields: Dictionary of field names to types

    Returns:
        String of fireEvent calls
    """
    actions = []
    for field_name, field_type in fields.items():
        if field_name in ["id", "createdAt", "updatedAt"]:
            continue

        label = field_name.replace("_", " ").title()
        normalized_type = field_type.lower()

        if normalized_type == "boolean":
            actions.append(f"fireEvent.click(screen.getByLabelText(/{label}/i));")
        else:
            test_value = "Test Value"
            if normalized_type in ["number", "int", "float"]:
                test_value = "42"
            elif normalized_type == "email":
                test_value = "test@example.com"
            elif normalized_type == "url":
                test_value = "https://example.com"

            actions.append(
                f"fireEvent.change(screen.getByLabelText(/{label}/i), {{ target: {{ value: '{test_value}' }} }});"
            )

    return "\n    ".join(actions)


# ========== Style Test Templates (Issue #1002) ==========

STYLE_TEST_TEMPLATE = """import { describe, it, expect, beforeAll } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

describe('Global CSS Integrity', () => {
  const globalsPath = path.join(process.cwd(), 'src/app/globals.css');
  let cssContent: string;

  beforeAll(() => {
    cssContent = fs.readFileSync(globalsPath, 'utf-8');
  });

  describe('File Content Type (CRITICAL - Issue #1002)', () => {
    it('is valid CSS, not TypeScript/JavaScript', () => {
      // These patterns indicate wrong file content - always invalid
      expect(cssContent).not.toMatch(/^\\s*import\\s+.*from/m);
      expect(cssContent).not.toMatch(/^\\s*export\\s+(default|const|function|class)/m);
      expect(cssContent).not.toMatch(/"use client"|'use client'/);
      expect(cssContent).not.toMatch(/^\\s*interface\\s+\\w+/m);
      expect(cssContent).not.toMatch(/^\\s*type\\s+\\w+\\s*=/m);
      expect(cssContent).not.toMatch(/<[A-Z][a-zA-Z]*[\\s/>]/); // JSX tags
    });

    it('has balanced CSS braces', () => {
      const open = (cssContent.match(/\\{/g) || []).length;
      const close = (cssContent.match(/\\}/g) || []).length;
      expect(open).toBe(close);
    });
  });

  describe('Tailwind Framework', () => {
    it('includes Tailwind directives', () => {
      // At minimum, CSS should have Tailwind setup
      const hasTailwind =
        cssContent.includes('@tailwind') ||
        cssContent.includes('@import "tailwindcss');
      expect(hasTailwind).toBe(true);
    });
  });

  describe('Design System Classes', () => {
    it('defines glass-card class', () => {
      expect(cssContent).toContain('.glass-card');
    });

    it('defines btn-primary class', () => {
      expect(cssContent).toContain('.btn-primary');
    });

    it('defines page-title class', () => {
      expect(cssContent).toContain('.page-title');
    });
  });
});
"""

ROUTES_TEST_TEMPLATE = """import {{ describe, it, expect }} from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {{ glob }} from 'glob';

describe('Next.js App Router Structure', () => {{
  const appDir = path.join(process.cwd(), 'src/app');

  describe('Root Layout (Global Styles Entry Point)', () => {{
    it('layout.tsx exists', () => {{
      const layoutPath = path.join(appDir, 'layout.tsx');
      expect(fs.existsSync(layoutPath)).toBe(true);
    }});

    it('layout imports globals.css', () => {{
      const layoutPath = path.join(appDir, 'layout.tsx');
      const content = fs.readFileSync(layoutPath, 'utf-8');
      // Should import globals.css (various import patterns)
      expect(content).toMatch(/import\\s+['"]\\.\\/globals\\.css['"]|import\\s+['"]@\\/app\\/globals\\.css['"]/);
    }});
  }});

  describe('Page Structure', () => {{
    it('all page.tsx files are valid React components', () => {{
      const pages = glob.sync('**/page.tsx', {{ cwd: appDir }});

      for (const page of pages) {{
        const content = fs.readFileSync(path.join(appDir, page), 'utf-8');

        // Should have an export (default or named)
        expect(content).toMatch(/export\\s+(default\\s+)?(async\\s+)?function|export\\s+default/);

        // Should not be empty
        expect(content.trim().length).toBeGreaterThan(50);
      }}
    }});

    it('dynamic routes have params handling', () => {{
      const dynamicPages = glob.sync('**/\\\\[*\\\\]/**/page.tsx', {{ cwd: appDir }});

      for (const page of dynamicPages) {{
        const content = fs.readFileSync(path.join(appDir, page), 'utf-8');
        // Should reference params somewhere
        expect(content).toMatch(/params|searchParams/);
      }}
    }});
  }});

  describe('Styling Consistency', () => {{
    it('pages use className attributes (styled, not unstyled)', () => {{
      const pages = glob.sync('**/page.tsx', {{ cwd: appDir, ignore: '**/api/**' }});

      for (const page of pages) {{
        const content = fs.readFileSync(path.join(appDir, page), 'utf-8');
        // Each page should have some styling
        const classNameCount = (content.match(/className=/g) || []).length;
        expect(classNameCount).toBeGreaterThan(0);
      }}
    }});
  }});

  describe('API Routes Exist', () => {{
    it('has API routes for CRUD operations', () => {{
      const apiRoutes = glob.sync('**/route.ts', {{ cwd: path.join(appDir, 'api') }});
      expect(apiRoutes.length).toBeGreaterThan(0);
    }});

    it('API routes export HTTP methods', () => {{
      const apiDir = path.join(appDir, 'api');
      if (!fs.existsSync(apiDir)) return; // Skip if no API dir

      const apiRoutes = glob.sync('**/route.ts', {{ cwd: apiDir }});

      for (const route of apiRoutes) {{
        const content = fs.readFileSync(path.join(apiDir, route), 'utf-8');
        // Should export at least one HTTP method
        expect(content).toMatch(/export\\s+(async\\s+)?function\\s+(GET|POST|PUT|PATCH|DELETE)/);
      }}
    }});
  }});
}});
"""


def generate_style_test_content(_resource_name: str = "Item") -> str:
    """Generate the content for styles.test.ts.

    Args:
        _resource_name: Resource name for component checks (unused, kept for API compatibility)

    Returns:
        Complete test file content
    """
    return STYLE_TEST_TEMPLATE


def generate_routes_test_content(resource_name: str = "Item") -> str:
    """Generate the content for routes.test.ts.

    Args:
        resource_name: Resource name for route checks

    Returns:
        Complete test file content
    """
    return ROUTES_TEST_TEMPLATE.format(
        resource=resource_name.lower(),
        Resource=resource_name.capitalize(),
        resource_plural=pluralize(resource_name.lower()),
    )
