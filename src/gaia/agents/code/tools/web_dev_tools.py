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
    CLIENT_COMPONENT_FORM,
    SERVER_COMPONENT_LIST,
    generate_api_imports,
    generate_detail_page,
    generate_field_display,
    generate_form_field,
    generate_new_page,
    generate_zod_schema,
    pluralize,
)

logger = logging.getLogger(__name__)


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
                fields = fields or {"name": "string", "description": "string"}

                project_path = Path(project_dir)
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                    }

                # Generate resource variants
                resource = resource_name.lower()
                Resource = resource_name.capitalize()
                resource_plural = pluralize(resource)

                # Build API route content
                imports = generate_api_imports(
                    operations, uses_validation="POST" in operations
                )

                # Generate validation schema if needed
                validation_schema = ""
                if "POST" in operations or "PATCH" in operations or "PUT" in operations:
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

                # Combine into complete file
                full_content = (
                    f"{imports}\n\n{validation_schema}\n\n{''.join(handlers)}"
                )

                # Write API route file
                api_file_path = Path(
                    f"{project_dir}/src/app/api/{resource_plural}/route.ts"
                )
                api_file_path.parent.mkdir(parents=True, exist_ok=True)
                api_file_path.write_text(full_content, encoding="utf-8")
                result = {"success": True}

                # Create dynamic route if PATCH or DELETE requested
                created_files = [str(api_file_path)]
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

                    dynamic_content = f"{imports}\n\n{validation_schema}\n\n{''.join(dynamic_handlers)}"
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
                    "note": "API endpoints created with actual Prisma queries. Run 'npm run dev' to test.",
                }

            except Exception as e:
                logger.error(f"Error managing API endpoint: {e}")
                return {"success": False, "error": str(e)}

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

                content = ""

                if component_type == "server" and variant == "list" and resource_name:
                    # Generate server component with data fetching
                    resource = resource_name.lower()
                    Resource = resource_name.capitalize()
                    resource_plural = pluralize(resource)

                    field_display = generate_field_display(fields or {})

                    content = SERVER_COMPONENT_LIST.format(
                        resource=resource,
                        Resource=Resource,
                        resource_plural=resource_plural,
                        field_display=field_display,
                    )

                elif component_type == "client" and variant == "form" and resource_name:
                    # Generate client component with form and state
                    resource = resource_name.lower()
                    Resource = resource_name.capitalize()
                    fields = fields or {"name": "string", "description": "string"}

                    # Generate form state fields
                    form_state = []
                    for field_name, field_type in fields.items():
                        if field_name not in ["id", "createdAt", "updatedAt"]:
                            default = (
                                '""'
                                if field_type == "string"
                                else "0" if field_type == "number" else "false"
                            )
                            form_state.append(f"    {field_name}: {default}")

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
                        form_fields="\n".join(form_fields),
                    )

                elif variant == "new" and resource_name:
                    # Generate "new" page that uses the form component
                    content = generate_new_page(resource_name)

                elif variant == "detail" and resource_name:
                    # Generate detail/edit page with form and delete functionality
                    fields = fields or {"name": "string", "description": "string"}
                    content = generate_detail_page(resource_name, fields)

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

                # Determine file path
                if component_type == "server" and variant == "list":
                    file_path = Path(
                        f"{project_dir}/src/app/{pluralize(resource_name)}/page.tsx"
                    )
                elif variant == "form":
                    file_path = Path(
                        f"{project_dir}/src/components/{component_name}.tsx"
                    )
                elif variant == "new" and resource_name:
                    file_path = Path(
                        f"{project_dir}/src/app/{pluralize(resource_name)}/new/page.tsx"
                    )
                elif variant == "detail" and resource_name:
                    file_path = Path(
                        f"{project_dir}/src/app/{pluralize(resource_name)}/[id]/page.tsx"
                    )
                else:
                    file_path = Path(
                        f"{project_dir}/src/components/{component_name}.tsx"
                    )

                # Write component file
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                result = {"success": True}

                logger.info(f"Created React component: {component_name}")

                return {
                    "success": result.get("success", True),
                    "component": component_name,
                    "type": component_type,
                    "file_path": str(file_path),
                    "note": "Component created with functional implementation",
                }

            except Exception as e:
                logger.error(f"Error managing React component: {e}")
                return {"success": False, "error": str(e)}

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
                    "recommendation": (
                        "All CRUD files present - application is complete!"
                        if all_complete
                        else f"Missing {missing_count} file(s) - use manage_react_component to create missing pages"
                    ),
                }

            except Exception as e:
                logger.error(f"Error validating CRUD completeness: {e}")
                return {"success": False, "error": str(e)}

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

                # 6. Validate completeness
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
                    "summary": f"Generated complete CRUD scaffold for {resource_name}",
                    "next_steps": [
                        "Run 'npm run db:generate' to update Prisma client",
                        "Run 'npm run db:push' to apply database changes",
                        "Run 'npm run dev' to start the development server",
                        f"Visit /{pluralize(resource_name)} to see your application",
                    ],
                }

            except Exception as e:
                logger.error(f"Error generating CRUD scaffold: {e}")
                return {"success": False, "error": str(e)}

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

                for field_name, field_type in fields.items():
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

                # Add timestamps
                field_lines.append("  createdAt DateTime @default(now())")
                field_lines.append("  updatedAt DateTime @updatedAt")

                # Generate model definition
                model_definition = f"""

model {model_name} {{
{chr(10).join(field_lines)}
}}
"""

                # Append to schema
                schema_content += model_definition

                # Write schema file
                schema_file.write_text(schema_content, encoding="utf-8")
                result = {"success": True}

                logger.info(f"Added Prisma model: {model_name}")

                return {
                    "success": result.get("success", True),
                    "model_name": model_name,
                    "schema_file": str(schema_file),
                    "note": "Run 'npm run db:generate && npm run db:push' to apply schema changes",
                }

            except Exception as e:
                logger.error(f"Error managing data model: {e}")
                return {"success": False, "error": str(e)}

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
                    "note": "Run these commands in sequence to update your database",
                }

            except Exception as e:
                logger.error(f"Error managing Prisma client: {e}")
                return {"success": False, "error": str(e)}

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
                        "note": f"Manual configuration needed for {config_type}. Apply: {updates}",
                    }

            except Exception as e:
                logger.error(f"Error managing config: {e}")
                return {"success": False, "error": str(e)}
