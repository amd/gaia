# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Code generation patterns for web applications.

This module contains reusable code patterns for generating functional
web application code. Patterns are framework-agnostic where possible,
with framework-specific variants where needed.

Patterns are stored as template strings that can be formatted with
resource-specific context (model names, fields, etc.).
"""

# ========== API Route Patterns (Next.js) ==========

API_ROUTE_GET = """export async function GET() {{
  try {{
    const {resource_plural} = await prisma.{resource}.findMany({{
      orderBy: {{ createdAt: 'desc' }},
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
        orderBy: {{ createdAt: 'desc' }}
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
        {{ error: 'Invalid request data', details: error.errors }},
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
        {{ error: 'Invalid update data', details: error.errors }},
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
    type_mapping = {
        "string": "z.string().min(1)",
        "text": "z.string()",
        "number": "z.number().int()",
        "float": "z.number()",
        "boolean": "z.boolean()",
        "date": "z.date()",
        "datetime": "z.date()",
        "timestamp": "z.date()",
        "email": "z.string().email()",
        "url": "z.string().url()",
    }
    return type_mapping.get(field_type.lower(), "z.string()")


# ========== React Component Patterns ==========

SERVER_COMPONENT_LIST = """import {{ prisma }} from "@/lib/prisma";
import Link from "next/link";

async function get{Resource}s() {{
  const {resource_plural} = await prisma.{resource}.findMany({{
    orderBy: {{ createdAt: "desc" }},
    take: 50
  }});
  return {resource_plural};
}}

export default async function {Resource}sPage() {{
  const {resource_plural} = await get{Resource}s();

  return (
    <div className="container mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">{Resource}s</h1>
        <Link
          href="/{resource}s/new"
          className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600"
        >
          Add New {Resource}
        </Link>
      </div>

      <div className="bg-white shadow-md rounded-lg overflow-hidden">
        {{{resource_plural}.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <p className="text-lg">No {resource}s found.</p>
            <p className="text-sm mt-2">Create your first {resource} to get started!</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-200">
            {{{resource_plural}.map((item) => (
              <div key={{item.id}} className="p-6 hover:bg-gray-50">
                <Link href={{`/{resource}s/${{item.id}}`}}>
                  {field_display}
                </Link>
              </div>
            ))}}
          </div>
        )}}
      </div>
    </div>
  );
}}"""

CLIENT_COMPONENT_FORM = """"use client";

import {{ useState }} from "react";
import {{ useRouter }} from "next/navigation";

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

      const response = await fetch(url, {{
        method,
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(formData)
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
    <form onSubmit={{handleSubmit}} className="space-y-6 max-w-2xl">
{form_fields}

      {{error && (
        <div className="bg-red-50 text-red-600 p-4 rounded-md">
          {{error}}
        </div>
      )}}

      <div className="flex gap-4">
        <button
          type="submit"
          disabled={{loading}}
          className="flex-1 bg-blue-500 text-white py-2 px-4 rounded hover:bg-blue-600 disabled:opacity-50"
        >
          {{loading ? "Saving..." : mode === "create" ? "Create {Resource}" : "Update {Resource}"}}
        </button>
        <button
          type="button"
          onClick={{() => router.back()}}
          className="px-4 py-2 border border-gray-300 rounded hover:bg-gray-50"
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
    <div className="container mx-auto p-8 max-w-2xl">
      <div className="mb-6">
        <Link href="/{resource}s" className="text-blue-500 hover:underline">
          ← Back to {Resource}s
        </Link>
      </div>

      <h1 className="text-3xl font-bold mb-6">Create New {Resource}</h1>

      <{Resource}Form mode="create" />
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
    """Generate a form field based on type."""
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
        <label htmlFor="{field_name}" className="block text-sm font-medium text-gray-700 mb-1">
          {label}
        </label>
        <textarea
          id="{field_name}"
          name="{field_name}"
          value={{formData.{field_name}}}
          onChange={{handleChange}}
          rows={{4}}
          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          required
        />
      </div>"""
    elif input_type == "checkbox":
        return f"""      <div className="flex items-center">
        <input
          type="checkbox"
          id="{field_name}"
          name="{field_name}"
          checked={{formData.{field_name}}}
          onChange={{handleChange}}
          className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
        />
        <label htmlFor="{field_name}" className="ml-2 block text-sm text-gray-700">
          {label}
        </label>
      </div>"""
    else:
        return f"""      <div>
        <label htmlFor="{field_name}" className="block text-sm font-medium text-gray-700 mb-1">
          {label}
        </label>
        <input
          type="{input_type}"
          id="{field_name}"
          name="{field_name}"
          value={{formData.{field_name}}}
          onChange={{handleChange}}
          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
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


def generate_field_display(fields: dict, max_fields: int = 2) -> str:
    """Generate JSX for displaying resource fields."""
    display_fields = []
    for i, field_name in enumerate(list(fields.keys())[:max_fields]):
        if field_name not in ["id", "createdAt", "updatedAt"]:
            if i == 0:
                display_fields.append(
                    f'<h3 className="font-semibold text-lg">{{item.{field_name}}}</h3>'
                )
            else:
                display_fields.append(
                    f'<p className="text-gray-600 text-sm mt-1">{{item.{field_name}}}</p>'
                )

    return (
        "\n                  ".join(display_fields)
        if display_fields
        else "<p>{{item.id}}</p>"
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
    """Generate a detail/edit page component.

    Args:
        resource_name: Name of the resource (e.g., "todo", "product")
        fields: Dictionary of field names to types

    Returns:
        Complete TypeScript/React page component code
    """
    resource = resource_name.lower()
    Resource = resource_name.capitalize()

    # Generate TypeScript interface fields
    type_fields = []
    for field_name, field_type in fields.items():
        if field_name not in ["id", "createdAt", "updatedAt"]:
            ts_type = _map_type_to_typescript(field_type)
            type_fields.append(f"  {field_name}: {ts_type};")

    type_fields_str = "\n".join(type_fields) if type_fields else "  // Add fields here"

    return CLIENT_COMPONENT_DETAIL_PAGE.format(
        resource=resource, Resource=Resource, type_fields=type_fields_str
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
