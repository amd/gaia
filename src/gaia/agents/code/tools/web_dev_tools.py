# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generic web development tools for Code Agent.

This mixin provides flexible, framework-agnostic tools for web development:
- API endpoint generation with actual Prisma queries
- React component generation (server and client)
- Database schema management
- Configuration updates

Tools use the manage_* prefix to indicate they handle both creation and modification.
All file I/O operations are delegated to FileIOToolsMixin for clean separation of concerns.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base.tools import tool
from gaia.agents.code.prompts.code_patterns import (
    API_ROUTE_DYNAMIC_DELETE,
    API_ROUTE_DYNAMIC_GET,
    API_ROUTE_DYNAMIC_PATCH,
    API_ROUTE_GET,
    API_ROUTE_GET_PAGINATED,
    API_ROUTE_POST,
    APP_GLOBALS_CSS,
    APP_LAYOUT,
    CLIENT_COMPONENT_FORM,
    COMPONENT_TEST_ACTIONS,
    COMPONENT_TEST_FORM,
    LANDING_PAGE_WITH_LINKS,
    SERVER_COMPONENT_LIST,
    generate_actions_component,
    generate_api_imports,
    generate_detail_page,
    generate_field_display,
    generate_form_field,
    generate_form_field_assertions,
    generate_form_fill_actions,
    generate_new_page,
    generate_test_data_fields,
    generate_zod_schema,
    pluralize,
)

logger = logging.getLogger(__name__)


def read_prisma_model(project_dir: str, model_name: str) -> Dict[str, Any]:
    """Read model definition from Prisma schema.

    Parses the Prisma schema file to extract field definitions and metadata
    for a specific model. This allows tools to adapt to the actual schema
    instead of relying on hardcoded assumptions.

    Args:
        project_dir: Path to the project directory
        model_name: Name of the model to read (case-insensitive)

    Returns:
        Dictionary with:
            success: Whether the model was found
            model_name: The model name (as defined in schema)
            fields: Dict of field names to types
            has_timestamps: Whether model has createdAt/updatedAt
            error: Error message if failed
    """
    schema_path = Path(project_dir) / "prisma" / "schema.prisma"
    if not schema_path.exists():
        return {
            "success": False,
            "error": "Schema file not found at prisma/schema.prisma",
        }

    try:
        content = schema_path.read_text()
        model_pattern = rf"model\s+{model_name}\s*\{{([^}}]+)\}}"
        match = re.search(model_pattern, content, re.DOTALL | re.IGNORECASE)

        if not match:
            return {
                "success": False,
                "error": f"Model {model_name} not found in schema",
            }

        # Parse fields from model body
        fields = {}
        for line in match.group(1).strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("@@"):
                continue
            # Skip field decorators like @id, @default, etc.
            if line.startswith("@") and not line.startswith("@@"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                field_name = parts[0]
                field_type = parts[1].rstrip("?[]")  # Remove optional/array markers
                fields[field_name] = field_type

        return {
            "success": True,
            "model_name": model_name,
            "fields": fields,
            "has_timestamps": "createdAt" in fields and "updatedAt" in fields,
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to parse schema: {str(e)}"}


class WebToolsMixin:
    """Mixin providing generic web development tools for the Code Agent.

    Tools are designed to be framework-agnostic where possible, with
    framework-specific logic in prompts rather than hardcoded in tools.

    All tools delegate file operations to FileIOToolsMixin to maintain
    clean separation of concerns.
    """

    def register_web_tools(self) -> None:
        """Register generic web development tools with the agent."""

        @tool
        def setup_app_styling(
            project_dir: str,
            app_title: str = "My App",
            app_description: str = "A modern web application",
        ) -> Dict[str, Any]:
            """Set up app-wide styling with modern design system.

            Creates/updates the root layout and globals.css with a modern dark theme
            that all pages will inherit. This should be run early in the project
            setup, after create-next-app.

            The design system includes:
            - Dark gradient background at the layout level
            - Glass morphism card effects (.glass-card)
            - Modern button variants (.btn-primary, .btn-secondary, .btn-danger)
            - Input field styling (.input-field)
            - Custom checkbox styling (.checkbox-modern)
            - Gradient text for titles (.page-title)
            - Back link styling (.link-back)
            - Custom scrollbar styling

            Args:
                project_dir: Path to the Next.js project directory
                app_title: Application title for metadata
                app_description: Application description for metadata

            Returns:
                Dictionary with success status and created/updated files
            """
            try:
                project_path = Path(project_dir)
                app_dir = project_path / "src" / "app"

                if not app_dir.exists():
                    return {
                        "success": False,
                        "error": f"App directory not found: {app_dir}",
                        "hint": "Run create-next-app first to initialize the project",
                    }

                files_created = []

                # Generate layout.tsx
                layout_path = app_dir / "layout.tsx"
                layout_content = APP_LAYOUT.format(
                    app_title=app_title,
                    app_description=app_description,
                )

                # Write layout file using FileIOToolsMixin
                if hasattr(self, "write_file"):
                    result = self.write_file(str(layout_path), layout_content)
                    if result.get("success"):
                        files_created.append(str(layout_path))
                else:
                    layout_path.write_text(layout_content)
                    files_created.append(str(layout_path))

                # Generate globals.css
                globals_path = app_dir / "globals.css"
                globals_content = APP_GLOBALS_CSS

                # Write globals file
                if hasattr(self, "write_file"):
                    result = self.write_file(str(globals_path), globals_content)
                    if result.get("success"):
                        files_created.append(str(globals_path))
                else:
                    globals_path.write_text(globals_content)
                    files_created.append(str(globals_path))

                logger.info(f"Set up app-wide styling: {files_created}")
                return {
                    "success": True,
                    "message": "App-wide styling configured successfully",
                    "files": files_created,
                    "design_system": [
                        ".glass-card - Glass morphism card effect",
                        ".btn-primary - Primary gradient button",
                        ".btn-secondary - Secondary button",
                        ".btn-danger - Danger/delete button",
                        ".input-field - Styled form input",
                        ".checkbox-modern - Modern checkbox styling",
                        ".page-title - Gradient title text",
                        ".link-back - Back navigation link",
                    ],
                }
            except Exception as e:
                logger.exception("Failed to set up app styling")
                return {"success": False, "error": str(e)}

        @tool
        def manage_api_endpoint(
            project_dir: str,
            resource_name: str,
            operations: List[str] = None,
            fields: Optional[Dict[str, str]] = None,
            enable_pagination: bool = False,
        ) -> Dict[str, Any]:
            """Manage API endpoints with actual Prisma operations.

            Creates or updates API routes with functional CRUD operations,
            validation, and error handling. Works for ANY resource type.

            REQUIREMENTS (Tier 2 - Prerequisites):
            - Must be called AFTER manage_data_model (needs Prisma model to exist)
            - Ensure 'prisma generate' was run (manage_data_model does this automatically)
            - API routes always import: NextResponse, prisma, z (zod)
            - Use try/catch with appropriate status codes (200, 201, 400, 500)

            Args:
                project_dir: Path to the web project directory
                resource_name: Resource name (e.g., "todo", "user", "product")
                operations: HTTP methods to implement (default: ["GET", "POST"])
                fields: Resource fields with types (for validation schema)
                enable_pagination: Whether to add pagination to GET endpoint

            Returns:
                Dictionary with success status and created files
            """
            try:
                operations = operations or ["GET", "POST"]

                project_path = Path(project_dir)

                # Phase 1 Fix (Issue #885): Read from Prisma schema instead of
                # using dangerous defaults. This makes tools schema-aware.
                if not fields:
                    model_info = read_prisma_model(
                        project_dir, resource_name.capitalize()
                    )
                    if model_info["success"]:
                        # Convert Prisma types to our field types and filter out auto-fields
                        prisma_to_field_type = {
                            "String": "string",
                            "Int": "number",
                            "Float": "float",
                            "Boolean": "boolean",
                            "DateTime": "datetime",
                        }
                        fields = {}
                        for field_name, prisma_type in model_info["fields"].items():
                            # Skip auto-generated fields
                            if field_name.lower() in {"id", "createdat", "updatedat"}:
                                continue
                            fields[field_name] = prisma_to_field_type.get(
                                prisma_type, "string"
                            )

                        if not fields:
                            return {
                                "success": False,
                                "error": f"Model {resource_name} has no user-facing fields in Prisma schema",
                                "hint": "Run manage_data_model first to create the model with fields",
                            }
                        logger.info(
                            f"Auto-read fields from Prisma schema for {resource_name}: {fields}"
                        )
                    else:
                        return {
                            "success": False,
                            "error": f"Cannot find model {resource_name} in Prisma schema. {model_info.get('error', '')}",
                            "hint": "Run manage_data_model first to create the Prisma model",
                        }

                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                # Sanitize resource_name: remove path components, brackets, slashes
                # This prevents malformed paths like "todos/[id]s/route.ts"
                clean_resource = resource_name.strip()
                # Remove common path patterns that shouldn't be in resource names
                clean_resource = clean_resource.replace("/[id]", "").replace("[id]", "")
                clean_resource = clean_resource.rstrip("/").lstrip("/")
                # Extract just the base resource name if path-like
                if "/" in clean_resource:
                    clean_resource = clean_resource.split("/")[0]
                # Remove any remaining special characters
                clean_resource = re.sub(r"[^\w]", "", clean_resource)

                if not clean_resource:
                    return {
                        "success": False,
                        "error": f"Invalid resource_name: '{resource_name}' - must be a simple name like 'todo' or 'product'",
                        "hint": "Use singular form without paths, e.g., 'todo' not 'todos/[id]'",
                    }

                # Safety net: Ensure Prisma singleton exists before creating routes
                singleton_path = project_path / "src" / "lib" / "prisma.ts"
                if not singleton_path.exists():
                    from gaia.agents.code.tools.prisma_tools import (
                        PRISMA_SINGLETON_TEMPLATE,
                    )

                    singleton_path.parent.mkdir(parents=True, exist_ok=True)
                    singleton_path.write_text(
                        PRISMA_SINGLETON_TEMPLATE, encoding="utf-8"
                    )
                    logger.info(f"Auto-created Prisma singleton at {singleton_path}")

                # Generate resource variants from cleaned name
                resource = clean_resource.lower()
                Resource = clean_resource.capitalize()
                resource_plural = pluralize(resource)

                # Build API route content
                # Phase 4 Fix (Issue #885): Check all operations that need validation
                needs_validation = any(
                    op in operations for op in ["POST", "PATCH", "PUT"]
                )
                imports = generate_api_imports(
                    operations, uses_validation=needs_validation
                )

                # Generate validation schema if needed
                validation_schema = ""
                if needs_validation:
                    print("WE ARE IN THE DUDUUUUUUDE")
                    validation_schema = generate_zod_schema(Resource, fields)

                # Generate handlers based on operations
                handlers = []
                for op in operations:
                    if op == "GET":
                        pattern = (
                            API_ROUTE_GET_PAGINATED
                            if enable_pagination
                            else API_ROUTE_GET
                        )
                        handlers.append(
                            pattern.format(
                                resource=resource,
                                Resource=Resource,
                                resource_plural=resource_plural,
                            )
                        )
                    elif op == "POST":
                        handlers.append(
                            API_ROUTE_POST.format(
                                resource=resource,
                                Resource=Resource,
                                resource_plural=resource_plural,
                            )
                        )

                # Combine into complete file - use \n\n to separate handlers
                full_content = f"{imports}\n\n{validation_schema}\n\n{(chr(10) + chr(10)).join(handlers)}"

                # Write API route file
                api_file_path = Path(
                    f"{project_dir}/src/app/api/{resource_plural}/route.ts"
                )
                api_file_path.parent.mkdir(parents=True, exist_ok=True)

                # Only write collection route if POST is in operations OR route doesn't exist
                # This prevents dynamic route calls from overwriting collection route
                created_files = []
                if "POST" in operations or not api_file_path.exists():
                    api_file_path.write_text(full_content, encoding="utf-8")
                    created_files.append(str(api_file_path))
                result = {"success": True}

                # Create dynamic route if PATCH or DELETE requested
                if (
                    "PATCH" in operations
                    or "DELETE" in operations
                    or "PUT" in operations
                ):
                    dynamic_handlers = []
                    if "GET" in operations:
                        dynamic_handlers.append(
                            API_ROUTE_DYNAMIC_GET.format(
                                resource=resource, Resource=Resource
                            )
                        )
                    if "PATCH" in operations or "PUT" in operations:
                        dynamic_handlers.append(
                            API_ROUTE_DYNAMIC_PATCH.format(
                                resource=resource, Resource=Resource
                            )
                        )
                    if "DELETE" in operations:
                        dynamic_handlers.append(
                            API_ROUTE_DYNAMIC_DELETE.format(resource=resource)
                        )

                    dynamic_content = f"{imports}\n\n{validation_schema}\n\n{(chr(10) + chr(10)).join(dynamic_handlers)}"
                    dynamic_file_path = Path(
                        f"{project_dir}/src/app/api/{resource_plural}/[id]/route.ts"
                    )
                    dynamic_file_path.parent.mkdir(parents=True, exist_ok=True)
                    dynamic_file_path.write_text(dynamic_content, encoding="utf-8")
                    created_files.append(str(dynamic_file_path))

                logger.info(f"Created API endpoint for {resource}")

                return {
                    "success": result.get("success", True),
                    "resource": resource,
                    "operations": operations,
                    "files": created_files,
                }

            except Exception as e:
                logger.error(f"Error managing API endpoint: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "api_endpoint_error",
                    "hint": "Check project directory structure and permissions",
                }

        @tool
        def manage_react_component(
            project_dir: str,
            component_name: str,
            component_type: str = "server",
            resource_name: Optional[str] = None,
            fields: Optional[Dict[str, str]] = None,
            variant: str = "list",
        ) -> Dict[str, Any]:
            """Manage React components with functional implementations.

            Creates or updates React components with real data fetching,
            state management, and event handlers. Works for ANY resource.

            REQUIREMENTS (Tier 2 - Prerequisites):
            - Must be called AFTER manage_api_endpoint (components need API routes)
            - Must be called AFTER manage_data_model (components need Prisma types)
            - Use 'import type { X } from @prisma/client' for type imports
            - Server components: import { prisma } from '@/lib/prisma'
            - Client components: NEVER import prisma directly - use API routes

            Args:
                project_dir: Path to the web project directory
                component_name: Component name (e.g., "TodoList", "UserForm")
                component_type: "server" or "client" component
                resource_name: Associated resource (for data operations)
                fields: Resource fields (for forms and display)
                variant: Component variant:
                        - "list": List page showing all items (server component)
                        - "form": Reusable form component for create/edit (client component)
                        - "new": Create new item page (client page using form)
                        - "detail": View/edit single item page with delete (client page)
                        - "actions": Delete/edit button component (client component)

            Returns:
                Dictionary with success status and component path
            """
            try:
                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                # Sanitize resource_name if provided (same logic as manage_api_endpoint)
                clean_resource_name = resource_name
                if resource_name:
                    clean_resource = resource_name.strip()
                    clean_resource = clean_resource.replace("/[id]", "").replace(
                        "[id]", ""
                    )
                    clean_resource = clean_resource.rstrip("/").lstrip("/")
                    if "/" in clean_resource:
                        clean_resource = clean_resource.split("/")[0]
                    clean_resource = re.sub(r"[^\w]", "", clean_resource)
                    clean_resource_name = (
                        clean_resource if clean_resource else resource_name
                    )

                # Auto-set component_type for client-side variants
                # These variants always generate client components with "use client"
                # This prevents the stub fallback when variant="form" but component_type
                # defaults to "server"
                if variant in ["form", "new", "detail", "actions"]:
                    component_type = "client"

                # Phase 1 Fix (Issue #885): Read from Prisma schema instead of
                # using dangerous defaults. This makes tools schema-aware.
                if not fields and clean_resource_name:
                    model_info = read_prisma_model(
                        project_dir, clean_resource_name.capitalize()
                    )
                    if model_info["success"]:
                        # Convert Prisma types to our field types and filter out auto-fields
                        prisma_to_field_type = {
                            "String": "string",
                            "Int": "number",
                            "Float": "float",
                            "Boolean": "boolean",
                            "DateTime": "datetime",
                        }
                        fields = {}
                        for field_name, prisma_type in model_info["fields"].items():
                            # Skip auto-generated fields
                            if field_name.lower() in {"id", "createdat", "updatedat"}:
                                continue
                            fields[field_name] = prisma_to_field_type.get(
                                prisma_type, "string"
                            )
                        if fields:
                            logger.info(
                                f"Auto-read fields from Prisma schema for {clean_resource_name}: {fields}"
                            )
                    # Note: We don't fail here - some components don't need fields

                content = ""

                if (
                    component_type == "server"
                    and variant == "list"
                    and clean_resource_name
                ):
                    # Generate server component with data fetching
                    resource = clean_resource_name.lower()
                    Resource = clean_resource_name.capitalize()
                    resource_plural = pluralize(resource)

                    field_display = generate_field_display(fields or {})

                    content = SERVER_COMPONENT_LIST.format(
                        resource=resource,
                        Resource=Resource,
                        resource_plural=resource_plural,
                        field_display=field_display,
                    )

                elif (
                    component_type == "client"
                    and variant == "form"
                    and clean_resource_name
                ):
                    # Generate client component with form and state
                    resource = clean_resource_name.lower()
                    Resource = clean_resource_name.capitalize()

                    # Phase 3 Fix (Issue #885): Fail clearly if no fields available
                    # instead of using dangerous defaults
                    if not fields:
                        return {
                            "success": False,
                            "error": f"No fields available for {clean_resource_name} form component",
                            "hint": "Run manage_data_model first to create the Prisma model with fields",
                        }

                    # Generate form state fields
                    form_state = []
                    date_field_names = []
                    for field_name, field_type in fields.items():
                        if field_name not in ["id", "createdAt", "updatedAt"]:
                            normalized_type = (
                                field_type.lower()
                                if isinstance(field_type, str)
                                else str(field_type).lower()
                            )
                            default = (
                                "0"
                                if normalized_type
                                in {"number", "int", "integer", "float"}
                                else "false" if normalized_type == "boolean" else '""'
                            )
                            form_state.append(f"    {field_name}: {default}")
                            if normalized_type in {"date", "datetime", "timestamp"}:
                                date_field_names.append(f'"{field_name}"')

                    # Generate form fields
                    form_fields = []
                    for field_name, field_type in fields.items():
                        if field_name not in ["id", "createdAt", "updatedAt"]:
                            form_fields.append(
                                generate_form_field(field_name, field_type)
                            )

                    content = CLIENT_COMPONENT_FORM.format(
                        resource=resource,
                        Resource=Resource,
                        form_state_fields=",\n".join(form_state),
                        date_fields=(
                            f"[{', '.join(date_field_names)}] as const"
                            if date_field_names
                            else "[] as const"
                        ),
                        form_fields="\n".join(form_fields),
                    )

                elif variant == "new" and clean_resource_name:
                    # Generate "new" page that uses the form component
                    content = generate_new_page(clean_resource_name)

                elif variant == "detail" and clean_resource_name:
                    # Generate detail/edit page with form and delete functionality
                    # Phase 3 Fix (Issue #885): Fail clearly if no fields available
                    if not fields:
                        return {
                            "success": False,
                            "error": f"No fields available for {clean_resource_name} detail page",
                            "hint": "Run manage_data_model first to create the Prisma model with fields",
                        }
                    content = generate_detail_page(clean_resource_name, fields)

                elif variant == "actions" and clean_resource_name:
                    # Generate actions component with delete functionality
                    content = generate_actions_component(clean_resource_name)

                else:
                    # Generic component template
                    content = f"""interface {component_name}Props {{
  // Add props here
}}

export function {component_name}({{ }}: {component_name}Props) {{
  return (
    <div>
      <h2>{component_name}</h2>
    </div>
  );
}}"""

                # Determine file path (use clean_resource_name to avoid malformed paths)
                if (
                    component_type == "server"
                    and variant == "list"
                    and clean_resource_name
                ):
                    file_path = Path(
                        f"{project_dir}/src/app/{pluralize(clean_resource_name)}/page.tsx"
                    )
                elif variant == "form":
                    file_path = Path(
                        f"{project_dir}/src/components/{component_name}.tsx"
                    )
                elif variant == "new" and clean_resource_name:
                    file_path = Path(
                        f"{project_dir}/src/app/{pluralize(clean_resource_name)}/new/page.tsx"
                    )
                elif variant == "detail" and clean_resource_name:
                    file_path = Path(
                        f"{project_dir}/src/app/{pluralize(clean_resource_name)}/[id]/page.tsx"
                    )
                elif variant == "actions" and clean_resource_name:
                    file_path = Path(
                        f"{project_dir}/src/components/{clean_resource_name.capitalize()}Actions.tsx"
                    )
                else:
                    file_path = Path(
                        f"{project_dir}/src/components/{component_name}.tsx"
                    )

                # Write component file
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                result = {"success": True}
                created_files = [str(file_path)]

                # Generate component tests for form and actions variants
                if variant in ["form", "actions"] and clean_resource_name and fields:
                    try:
                        resource = clean_resource_name.lower()
                        Resource = clean_resource_name.capitalize()
                        resource_plural = pluralize(resource)
                        test_data_fields = generate_test_data_fields(fields, variant=1)

                        if variant == "form":
                            # Generate form component test
                            form_field_assertions = generate_form_field_assertions(
                                fields
                            )
                            form_fill_actions = generate_form_fill_actions(fields)

                            form_test_content = COMPONENT_TEST_FORM.format(
                                Resource=Resource,
                                resource_plural=resource_plural,
                                form_field_assertions=form_field_assertions,
                                form_fill_actions=form_fill_actions,
                                test_data_fields=test_data_fields,
                            )
                            form_test_path = Path(
                                f"{project_dir}/src/components/__tests__/{Resource}Form.test.tsx"
                            )
                            form_test_path.parent.mkdir(parents=True, exist_ok=True)
                            form_test_path.write_text(
                                form_test_content, encoding="utf-8"
                            )
                            created_files.append(str(form_test_path))
                            logger.info(f"Created form component test for {Resource}")

                        elif variant == "actions":
                            # Generate actions component test
                            actions_test_content = COMPONENT_TEST_ACTIONS.format(
                                Resource=Resource,
                                resource=resource,
                                resource_plural=resource_plural,
                            )
                            actions_test_path = Path(
                                f"{project_dir}/src/components/__tests__/{Resource}Actions.test.tsx"
                            )
                            actions_test_path.parent.mkdir(parents=True, exist_ok=True)
                            actions_test_path.write_text(
                                actions_test_content, encoding="utf-8"
                            )
                            created_files.append(str(actions_test_path))
                            logger.info(
                                f"Created actions component test for {Resource}"
                            )

                    except Exception as test_error:
                        logger.warning(
                            f"Could not generate component test: {test_error}"
                        )

                logger.info(f"Created React component: {component_name}")

                return {
                    "success": result.get("success", True),
                    "component": component_name,
                    "type": component_type,
                    "file_path": str(file_path),
                    "files": created_files,
                }

            except Exception as e:
                logger.error(f"Error managing React component: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "component_error",
                    "hint": "Check project structure and component syntax",
                }

        @tool
        def update_landing_page(
            project_dir: str,
            resource_name: str,
            description: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Update the landing page to include a link to the new resource.

            Modifies src/app/page.tsx to add navigation to the newly created
            resource pages. This ensures users can easily access the new features
            from the main page.

            Args:
                project_dir: Path to the Next.js project directory
                resource_name: Name of the resource (e.g., "todo", "product")
                description: Optional description for the link

            Returns:
                Dictionary with success status and updated file path
            """
            try:
                project_path = Path(project_dir)
                page_path = project_path / "src" / "app" / "page.tsx"

                if not page_path.exists():
                    return {
                        "success": False,
                        "error": f"Landing page not found: {page_path}",
                        "hint": "Ensure this is a Next.js project with app router",
                    }

                resource = resource_name.lower()
                Resource = resource_name.capitalize()
                resource_plural = pluralize(resource)
                link_description = description or f"Manage your {resource_plural}"

                # Read current content
                current_content = page_path.read_text(encoding="utf-8")

                # Check if link already exists
                if (
                    f'href="/{resource_plural}"' in current_content
                    or f"href='/{resource_plural}'" in current_content
                ):
                    return {
                        "success": True,
                        "message": f"Link to /{resource_plural} already exists in landing page",
                        "file_path": str(page_path),
                        "already_exists": True,
                    }

                # Generate new landing page with link to resource using dark theme
                new_content = LANDING_PAGE_WITH_LINKS.format(
                    resource_plural=resource_plural,
                    Resource=Resource,
                    link_description=link_description,
                )

                # Write updated content
                page_path.write_text(new_content, encoding="utf-8")

                logger.info(f"Updated landing page with link to /{resource_plural}")

                return {
                    "success": True,
                    "message": f"Landing page updated with link to /{resource_plural}",
                    "file_path": str(page_path),
                    "resource": resource_name,
                    "link_path": f"/{resource_plural}",
                }

            except Exception as e:
                logger.error(f"Error updating landing page: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "hint": "Check that src/app/page.tsx exists and is writable",
                }

        @tool
        def setup_nextjs_testing(
            project_dir: str,
            resource_name: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Set up Vitest testing infrastructure for a Next.js project.

            Installs testing dependencies and creates configuration files:
            - Vitest + React Testing Library
            - vitest.config.ts with proper aliases and jsdom environment
            - tests/setup.ts with common mocks (next/navigation, Prisma)
            - Updates package.json with test scripts

            Should be called after the project is initialized but before running tests.

            Args:
                project_dir: Path to the Next.js project directory
                resource_name: Optional resource name to customize Prisma mocks

            Returns:
                Dictionary with success status and created files
            """
            from gaia.agents.code.prompts.code_patterns import TEST_SETUP, VITEST_CONFIG

            try:
                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                created_files = []
                resource = resource_name.lower() if resource_name else "todo"

                # Install testing dependencies
                install_cmd = (
                    "npm install -D vitest @vitejs/plugin-react jsdom "
                    "@testing-library/react @testing-library/jest-dom @testing-library/user-event "
                    "@types/node"
                )
                install_result = subprocess.run(
                    install_cmd,
                    shell=True,
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                    timeout=1200,
                    check=False,
                )

                if install_result.returncode != 0:
                    return {
                        "success": False,
                        "error": "Failed to install testing dependencies",
                        "details": install_result.stderr,
                        "hint": "Check npm configuration and network connectivity",
                    }

                # Create vitest.config.ts
                vitest_config_path = project_path / "vitest.config.ts"
                vitest_config_path.write_text(VITEST_CONFIG, encoding="utf-8")
                created_files.append(str(vitest_config_path))

                # Create tests/setup.ts with resource-specific Prisma mocks
                tests_dir = project_path / "tests"
                tests_dir.mkdir(exist_ok=True)

                setup_content = TEST_SETUP.format(resource=resource)
                setup_path = tests_dir / "setup.ts"
                setup_path.write_text(setup_content, encoding="utf-8")
                created_files.append(str(setup_path))

                # Update package.json to add test scripts
                package_json_path = project_path / "package.json"
                if package_json_path.exists():
                    import json

                    package_data = json.loads(
                        package_json_path.read_text(encoding="utf-8")
                    )

                    if "scripts" not in package_data:
                        package_data["scripts"] = {}

                    # Add test scripts if not present
                    if "test" not in package_data["scripts"]:
                        package_data["scripts"]["test"] = "vitest run"
                    if "test:watch" not in package_data["scripts"]:
                        package_data["scripts"]["test:watch"] = "vitest"
                    if "test:coverage" not in package_data["scripts"]:
                        package_data["scripts"][
                            "test:coverage"
                        ] = "vitest run --coverage"

                    package_json_path.write_text(
                        json.dumps(package_data, indent=2) + "\n", encoding="utf-8"
                    )
                    created_files.append(str(package_json_path))

                logger.info(f"Set up Vitest testing infrastructure in {project_dir}")

                return {
                    "success": True,
                    "message": "Testing infrastructure configured successfully",
                    "files": created_files,
                    "dependencies_installed": [
                        "vitest",
                        "@vitejs/plugin-react",
                        "jsdom",
                        "@testing-library/react",
                        "@testing-library/jest-dom",
                        "@testing-library/user-event",
                    ],
                    "scripts_added": {
                        "test": "vitest run",
                        "test:watch": "vitest",
                        "test:coverage": "vitest run --coverage",
                    },
                }

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "error": "npm install timed out",
                    "hint": "Check network connectivity and try again",
                }
            except Exception as e:
                logger.error(f"Error setting up testing: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "hint": "Check project structure and npm configuration",
                }

        @tool
        def validate_crud_completeness(
            project_dir: str, resource_name: str
        ) -> Dict[str, Any]:
            """Validate that all necessary CRUD files exist for a resource.

            Checks for the presence of all required files for a complete CRUD application:
            - API routes (collection and item endpoints)
            - Pages (list, new, detail/edit)
            - Components (form)
            - Database model

            Args:
                project_dir: Path to the project directory
                resource_name: Resource name to validate (e.g., "todo", "user")

            Returns:
                Dictionary with validation results and lists of existing/missing files
            """
            try:
                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                resource = resource_name.lower()
                Resource = resource_name.capitalize()
                resource_plural = pluralize(resource)

                # Define expected files
                expected_files = {
                    "api_routes": {
                        f"src/app/api/{resource_plural}/route.ts": "Collection API route (GET list, POST create)",
                        f"src/app/api/{resource_plural}/[id]/route.ts": "Item API route (GET single, PATCH update, DELETE)",
                    },
                    "pages": {
                        f"src/app/{resource_plural}/page.tsx": "List page showing all items",
                        f"src/app/{resource_plural}/new/page.tsx": "Create new item page",
                        f"src/app/{resource_plural}/[id]/page.tsx": "View/edit single item page",
                    },
                    "components": {
                        f"src/components/{Resource}Form.tsx": "Reusable form component for create/edit"
                    },
                }

                # Check which files exist
                missing_files = {}
                existing_files = {}

                for category, files in expected_files.items():
                    missing_files[category] = []
                    existing_files[category] = []

                    for file_path, description in files.items():
                        full_path = project_path / file_path
                        if full_path.exists():
                            existing_files[category].append(
                                {"path": file_path, "description": description}
                            )
                        else:
                            missing_files[category].append(
                                {"path": file_path, "description": description}
                            )

                # Check if Prisma model exists
                schema_file = project_path / "prisma" / "schema.prisma"
                model_exists = False
                if schema_file.exists():
                    schema_content = schema_file.read_text()
                    model_exists = f"model {Resource}" in schema_content

                # Calculate completeness
                total_files = sum(len(files) for files in expected_files.values())
                existing_count = sum(len(files) for files in existing_files.values())
                missing_count = sum(len(files) for files in missing_files.values())

                all_complete = missing_count == 0 and model_exists

                logger.info(
                    f"CRUD completeness check for {resource}: {existing_count}/{total_files} files exist"
                )

                return {
                    "success": True,
                    "complete": all_complete,
                    "resource": resource,
                    "model_exists": model_exists,
                    "existing_files": existing_files,
                    "missing_files": missing_files,
                    "stats": {
                        "total": total_files,
                        "existing": existing_count,
                        "missing": missing_count,
                    },
                }

            except Exception as e:
                logger.error(f"Error validating CRUD completeness: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "validation_error",
                }

        @tool
        def generate_crud_scaffold(
            project_dir: str, resource_name: str, fields: Dict[str, str]
        ) -> Dict[str, Any]:
            """Generate a complete CRUD scaffold with all necessary files.

            This high-level tool orchestrates multiple operations to create
            a fully functional CRUD application for a resource. It generates:
            - API routes for all CRUD operations
            - List page to view all items
            - Form component for create/edit
            - Create page (new item)
            - Detail/edit page (single item with delete)

            Args:
                project_dir: Path to the project directory
                resource_name: Resource name (e.g., "todo", "product")
                fields: Dictionary of field names to types

            Returns:
                Dictionary with generation results and validation status
            """
            try:
                results = {
                    "api_routes": [],
                    "pages": [],
                    "components": [],
                    "errors": [],
                }

                logger.info(f"Generating complete CRUD scaffold for {resource_name}...")

                # 1. Generate API endpoints (all CRUD operations)
                logger.info("  → Generating API routes...")
                api_result = manage_api_endpoint(
                    project_dir=project_dir,
                    resource_name=resource_name,
                    operations=["GET", "POST", "PATCH", "DELETE"],
                    fields=fields,
                    enable_pagination=True,
                )
                if api_result.get("success"):
                    results["api_routes"].extend(api_result.get("files", []))
                else:
                    results["errors"].append(
                        f"API generation failed: {api_result.get('error')}"
                    )

                # 2. Generate list page (server component)
                logger.info("  → Generating list page...")
                list_result = manage_react_component(
                    project_dir=project_dir,
                    component_name=f"{resource_name.capitalize()}List",
                    component_type="server",
                    resource_name=resource_name,
                    fields=fields,
                    variant="list",
                )
                if list_result.get("success"):
                    results["pages"].append(list_result.get("file_path"))
                else:
                    results["errors"].append(
                        f"List page generation failed: {list_result.get('error')}"
                    )

                # 3. Generate form component (reusable for create/edit)
                logger.info("  → Generating form component...")
                form_result = manage_react_component(
                    project_dir=project_dir,
                    component_name=f"{resource_name.capitalize()}Form",
                    component_type="client",
                    resource_name=resource_name,
                    fields=fields,
                    variant="form",
                )
                if form_result.get("success"):
                    results["components"].append(form_result.get("file_path"))
                else:
                    results["errors"].append(
                        f"Form component generation failed: {form_result.get('error')}"
                    )

                # 4. Generate new page (create page)
                logger.info("  → Generating create (new) page...")
                new_result = manage_react_component(
                    project_dir=project_dir,
                    component_name=f"New{resource_name.capitalize()}Page",
                    component_type="client",
                    resource_name=resource_name,
                    fields=fields,
                    variant="new",
                )
                if new_result.get("success"):
                    results["pages"].append(new_result.get("file_path"))
                else:
                    results["errors"].append(
                        f"New page generation failed: {new_result.get('error')}"
                    )

                # 5. Generate detail page (view/edit page with delete)
                logger.info("  → Generating detail/edit page...")
                detail_result = manage_react_component(
                    project_dir=project_dir,
                    component_name=f"{resource_name.capitalize()}DetailPage",
                    component_type="client",
                    resource_name=resource_name,
                    fields=fields,
                    variant="detail",
                )
                if detail_result.get("success"):
                    results["pages"].append(detail_result.get("file_path"))
                else:
                    results["errors"].append(
                        f"Detail page generation failed: {detail_result.get('error')}"
                    )

                # 6. Generate actions component (edit/delete buttons)
                logger.info("  → Generating actions component...")
                actions_result = manage_react_component(
                    project_dir=project_dir,
                    component_name=f"{resource_name.capitalize()}Actions",
                    component_type="client",
                    resource_name=resource_name,
                    fields=fields,
                    variant="actions",
                )
                if actions_result.get("success"):
                    results["components"].append(actions_result.get("file_path"))
                else:
                    results["errors"].append(
                        f"Actions component generation failed: {actions_result.get('error')}"
                    )

                # 7. Validate completeness
                logger.info("  → Validating completeness...")
                validation = validate_crud_completeness(project_dir, resource_name)

                success = len(results["errors"]) == 0
                logger.info(
                    f"CRUD scaffold generation {'succeeded' if success else 'completed with errors'}"
                )

                return {
                    "success": success,
                    "resource": resource_name,
                    "generated": results,
                    "validation": validation,
                }

            except Exception as e:
                logger.error(f"Error generating CRUD scaffold: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "scaffold_generation_error",
                }

        @tool
        def manage_data_model(
            project_dir: str,
            model_name: str,
            fields: Dict[str, str],
            relationships: Optional[List[Dict[str, str]]] = None,
        ) -> Dict[str, Any]:
            """Manage database models with Prisma ORM.

            Creates or updates Prisma model definitions. Works for ANY model type.

            Args:
                project_dir: Path to the project directory
                model_name: Model name (singular, PascalCase, e.g., "User", "Product")
                fields: Dictionary of field names to types
                        Supported: "string", "text", "number", "float", "boolean",
                                  "date", "datetime", "timestamp", "email", "url"
                relationships: Optional list of relationships
                              [{"type": "hasMany", "model": "Post"}]

            Returns:
                Dictionary with success status and schema file path
            """
            try:
                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                schema_file = project_path / "prisma" / "schema.prisma"

                if not schema_file.exists():
                    return {
                        "success": False,
                        "error": "schema.prisma not found. Initialize Prisma first.",
                    }

                # Read existing schema
                schema_content = schema_file.read_text()

                # Validate schema doesn't have forbidden output field in generator block
                if "output" in schema_content:
                    # Check if it's in generator client block specifically
                    generator_match = re.search(
                        r"generator\s+client\s*\{[^}]*output[^}]*\}",
                        schema_content,
                        re.DOTALL,
                    )
                    if generator_match:
                        # Auto-fix: remove the output line
                        fixed_content = re.sub(
                            r'\n\s*output\s*=\s*"[^"]*"', "", schema_content
                        )
                        schema_file.write_text(fixed_content, encoding="utf-8")
                        schema_content = fixed_content
                        logger.warning(
                            "Removed invalid 'output' field from generator client block"
                        )

                # Generate field definitions
                field_lines = []
                field_lines.append("  id        Int      @id @default(autoincrement())")

                # Map types to Prisma types
                type_mapping = {
                    "string": "String",
                    "text": "String",
                    "number": "Int",
                    "float": "Float",
                    "boolean": "Boolean",
                    "date": "DateTime",
                    "datetime": "DateTime",
                    "timestamp": "DateTime",
                    "email": "String",
                    "url": "String",
                }

                # Define reserved fields that are auto-generated
                reserved_fields = {"id", "createdat", "updatedat"}

                # Build field lines from user input (skip reserved fields)
                for field_name, field_type in fields.items():
                    if field_name.lower() in reserved_fields:
                        logger.warning(
                            f"Skipping reserved field '{field_name}' - auto-generated by Prisma"
                        )
                        continue
                    prisma_type = type_mapping.get(field_type.lower(), "String")
                    field_lines.append(f"  {field_name:<12} {prisma_type}")

                # Add relationships if provided
                if relationships:
                    for rel in relationships:
                        rel_type = rel.get("type", "hasMany")
                        rel_model = rel.get("model")
                        if rel_type == "hasMany":
                            field_lines.append(
                                f"  {rel_model.lower()}s   {rel_model}[]"
                            )
                        elif rel_type == "hasOne":
                            field_lines.append(
                                f"  {rel_model.lower()}     {rel_model}?"
                            )

                # Always add timestamps - they're standard for Prisma and our templates expect them
                # Note: Reserved fields (including createdAt/updatedAt) are already skipped
                # from user input above, so there's no risk of duplication
                field_lines.append("  createdAt    DateTime @default(now())")
                field_lines.append("  updatedAt    DateTime @updatedAt")

                # Check if model already exists in schema
                model_pattern = rf"model\s+{model_name}\s*\{{"
                if re.search(model_pattern, schema_content):
                    return {
                        "success": False,
                        "error": f"Model '{model_name}' already exists in schema",
                        "error_type": "duplicate_model",
                        "hint": "Use a different model name or edit the existing model",
                        "suggested_fix": f"Read schema.prisma to see existing {model_name} definition",
                    }

                # Generate model definition
                model_definition = f"""

model {model_name} {{
{chr(10).join(field_lines)}
}}
"""

                # Save original schema for rollback
                original_schema = schema_content

                # Append to schema
                schema_content += model_definition

                # Write new schema
                schema_file.write_text(schema_content, encoding="utf-8")

                # Validate with prisma format
                validate_result = subprocess.run(
                    f'npx prisma format --schema="{schema_file}"',
                    cwd=str(project_path),
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                    check=False,
                )

                if validate_result.returncode != 0:
                    # Rollback to original schema
                    schema_file.write_text(original_schema, encoding="utf-8")
                    return {
                        "success": False,
                        "error": f"Schema validation failed: {validate_result.stderr}",
                        "error_type": "schema_validation_error",
                        "hint": "The schema changes caused validation errors",
                        "suggested_fix": "Check field types and model syntax",
                    }

                result = {"success": True}

                logger.info(f"Added Prisma model: {model_name}")

                # Auto-generate Prisma client types
                prisma_generated = False
                generation_note = ""

                try:
                    # Format schema first
                    subprocess.run(
                        f'npx prisma format --schema="{schema_file}"',
                        cwd=str(project_path),
                        shell=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=600,
                        check=False,
                    )

                    # Generate Prisma client types
                    generate_result = subprocess.run(
                        f'npx prisma generate --schema="{schema_file}"',
                        cwd=str(project_path),
                        shell=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=1200,
                        check=False,
                    )

                    if generate_result.returncode != 0:
                        stderr = generate_result.stderr
                        logger.error(f"prisma generate failed: {stderr}")
                        return {
                            "success": False,
                            "error": f"prisma generate failed: {stderr}",
                            "schema_file": str(schema_file),
                            "fix_hint": "Check schema.prisma for syntax errors",
                        }

                    # Verify Prisma client was actually generated
                    client_index = (
                        project_path
                        / "node_modules"
                        / ".prisma"
                        / "client"
                        / "index.js"
                    )
                    if not client_index.exists():
                        logger.error("Prisma client not generated despite no errors")
                        return {
                            "success": False,
                            "error": "Prisma client not generated despite no errors",
                            "fix_hint": "Run 'npm install' then 'npx prisma generate'",
                        }

                    prisma_generated = True
                    generation_note = (
                        "Schema updated and Prisma client generated successfully"
                    )
                    logger.info(generation_note)

                    # Push schema changes to database
                    logger.info(f"Running prisma db push in {project_dir}")
                    db_push_result = subprocess.run(
                        "npx prisma db push",
                        cwd=str(project_path),
                        shell=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=1200,
                        check=False,
                    )

                    if db_push_result.returncode != 0:
                        logger.error(f"prisma db push failed: {db_push_result.stderr}")
                        return {
                            "success": False,
                            "error": f"prisma db push failed: {db_push_result.stderr}",
                            "fix_hint": "Check DATABASE_URL in .env file",
                            "generated": True,  # Client was generated successfully
                            "pushed": False,
                        }

                    generation_note = "Schema updated, Prisma client generated, and database pushed successfully"
                    logger.info(generation_note)

                except Exception as e:
                    # Prisma generation failed - block the operation
                    logger.error(f"Could not generate Prisma client: {e}")
                    return {
                        "success": False,
                        "error": f"Could not generate Prisma client: {e}",
                        "fix_hint": "Ensure prisma is installed (npm install)",
                    }

                return {
                    "success": result.get("success", True),
                    "model_name": model_name,
                    "schema_file": str(schema_file),
                    "schema_updated": True,
                    "prisma_generated": prisma_generated,
                    "note": generation_note,
                }

            except Exception as e:
                logger.error(f"Error managing data model: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "data_model_error",
                }

        @tool
        def manage_prisma_client(project_dir: str) -> Dict[str, Any]:
            """Manage Prisma client generation and database sync.

            Generates the Prisma client and pushes schema changes to the database.

            Args:
                project_dir: Path to the project directory

            Returns:
                Dictionary with success status and commands to run
            """
            try:
                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                # Check if Prisma is configured
                schema_file = project_path / "prisma" / "schema.prisma"
                if not schema_file.exists():
                    return {
                        "success": False,
                        "error": "Prisma not initialized. schema.prisma not found.",
                    }

                # Provide guidance for Prisma operations
                commands = [
                    "npm run db:generate  # Generate Prisma Client",
                    "npm run db:push      # Push schema to database",
                    "npm run db:studio    # Open Prisma Studio (optional)",
                ]

                logger.info("Prisma client management guidance provided")

                return {
                    "success": True,
                    "commands": commands,
                    "working_dir": str(project_path),
                }

            except Exception as e:
                logger.error(f"Error managing Prisma client: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "prisma_client_error",
                }

        @tool
        def manage_web_config(
            project_dir: str, config_type: str, updates: Dict[str, Any]
        ) -> Dict[str, Any]:
            """Manage web application configuration files.

            Updates configuration files like .env, next.config.js, etc.
            Delegates actual file operations to file_io.

            Args:
                project_dir: Path to the project directory
                config_type: Type of config ("env", "nextjs", "tailwind")
                updates: Dictionary of configuration updates

            Returns:
                Dictionary with success status
            """
            try:
                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                if config_type == "env":
                    env_file = project_path / ".env"
                    if not env_file.exists():
                        # Create new .env file
                        content = "\n".join(f"{k}={v}" for k, v in updates.items())
                    else:
                        # Update existing
                        content = env_file.read_text()
                        for key, value in updates.items():
                            if f"{key}=" in content:
                                lines = content.split("\n")
                                content = "\n".join(
                                    (
                                        f"{key}={value}"
                                        if line.startswith(f"{key}=")
                                        else line
                                    )
                                    for line in lines
                                )
                            else:
                                content += f"\n{key}={value}"

                    env_file.write_text(content, encoding="utf-8")

                    return {
                        "success": True,
                        "config_type": config_type,
                        "file": str(env_file),
                        "updates": updates,
                    }
                else:
                    return {
                        "success": True,
                        "config_type": config_type,
                        "updates": updates,
                    }

            except Exception as e:
                logger.error(f"Error managing config: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "config_error",
                }

        @tool
        def generate_style_tests(
            project_dir: str, resource_name: str = "Item"
        ) -> Dict[str, Any]:
            """Generate CSS and styling tests for the project.

            Creates test files that validate:
            1. CSS file integrity (no TypeScript in CSS - Issue #1002)
            2. Tailwind directive presence
            3. Design system class definitions
            4. Layout imports globals.css
            5. App router structure

            Tests are placed in the project's /tests directory.

            Args:
                project_dir: Path to the Next.js project directory
                resource_name: Resource name for component styling tests

            Returns:
                Dictionary with success status and generated files
            """
            from gaia.agents.code.prompts.code_patterns import (
                generate_routes_test_content,
                generate_style_test_content,
            )

            try:
                project_path = Path(project_dir)
                tests_dir = project_path / "tests"
                styling_dir = tests_dir / "styling"

                # Ensure directories exist
                tests_dir.mkdir(parents=True, exist_ok=True)
                styling_dir.mkdir(parents=True, exist_ok=True)

                files_created = []

                # 1. Generate styles.test.ts (main CSS integrity test)
                styles_test_path = tests_dir / "styles.test.ts"
                styles_content = generate_style_test_content(resource_name)

                if hasattr(self, "write_file"):
                    result = self.write_file(str(styles_test_path), styles_content)
                    if result.get("success"):
                        files_created.append(str(styles_test_path))
                else:
                    styles_test_path.write_text(styles_content)
                    files_created.append(str(styles_test_path))

                # 2. Generate routes.test.ts (app router structure test)
                routes_test_path = styling_dir / "routes.test.ts"
                routes_content = generate_routes_test_content(resource_name)

                if hasattr(self, "write_file"):
                    result = self.write_file(str(routes_test_path), routes_content)
                    if result.get("success"):
                        files_created.append(str(routes_test_path))
                else:
                    routes_test_path.write_text(routes_content)
                    files_created.append(str(routes_test_path))

                # 3. Install glob package if not present (needed for tests)
                package_json = project_path / "package.json"
                if package_json.exists():
                    pkg_content = package_json.read_text()
                    if '"glob"' not in pkg_content:
                        try:
                            subprocess.run(
                                ["npm", "install", "--save-dev", "glob", "@types/glob"],
                                cwd=str(project_path),
                                capture_output=True,
                                text=True,
                                timeout=600,
                                check=False,
                            )
                            logger.info("Installed glob package for style tests")
                        except Exception as e:
                            logger.warning(f"Could not install glob package: {e}")

                logger.info(
                    f"Generated {len(files_created)} style test files for {resource_name}"
                )

                return {
                    "success": True,
                    "files": files_created,
                    "message": f"Generated style tests for {resource_name}",
                    "tests_description": [
                        "styles.test.ts - CSS integrity (TypeScript detection, Tailwind, braces)",
                        "styling/routes.test.ts - App router structure and styling consistency",
                    ],
                }

            except Exception as e:
                logger.error(f"Error generating style tests: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "test_generation_error",
                }
