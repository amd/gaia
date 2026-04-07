"""
OpenAPI specification generator for GAIA API.

This module provides automatic OpenAPI 3.0 specification generation
from FastAPI applications, including Swagger UI and ReDoc HTML generation.

Example:
    >>> from fastapi import FastAPI
    >>> from gaia.api.openapi import OpenAPIGenerator
    >>>
    >>> app = FastAPI(title="GAIA API")
    >>>
    >>> @app.post("/v1/chat/completions")
    ... async def create_completion(request: dict):
    ...     \"\"\"Create chat completion.\"\"\"
    ...     pass
    >>>
    >>> generator = OpenAPIGenerator(app)
    >>> spec = generator.generate()
    >>> swagger_html = generator.generate_swagger_ui()
"""

import json
from typing import Any, Dict, List, Optional, Type, get_type_hints, get_origin, get_args
from fastapi import FastAPI, APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel
import inspect


class OpenAPIGenerator:
    """
    Auto-generate OpenAPI 3.0 specification from FastAPI app.

    Features:
        - Automatic schema extraction from Pydantic models
        - Route documentation from docstrings
        - Custom schema extensions
        - Swagger UI HTML generation
        - ReDoc HTML generation

    Example:
        >>> from fastapi import FastAPI
        >>> from gaia.api.openapi import OpenAPIGenerator
        >>>
        >>> app = FastAPI(title="GAIA API")
        >>>
        >>> @app.post("/v1/chat/completions")
        >>> async def create_completion(request: dict):
        >>>     \"\"\"Create chat completion.\"\"\"
        >>>     ...
        >>>
        >>> generator = OpenAPIGenerator(app)
        >>> spec = generator.generate()
        >>> swagger_html = generator.generate_swagger_ui()
    """

    OPENAPI_VERSION = "3.0.3"

    def __init__(
        self,
        app: FastAPI,
        title: str = "GAIA API",
        version: str = "1.0.0",
        description: str = "GAIA (Generative AI Is Awesome) API for running generative AI applications locally on AMD hardware.",
    ) -> None:
        """
        Initialize OpenAPI generator.

        Args:
            app: FastAPI application
            title: API title
            version: API version
            description: API description

        Example:
            >>> app = FastAPI(title="My API")
            >>> generator = OpenAPIGenerator(
            ...     app,
            ...     title="My API",
            ...     version="2.0.0",
            ...     description="My API description"
            ... )
        """
        self.app = app
        self.title = title
        self.version = version
        self.description = description
        self._schema_cache: Dict[str, Any] = {}

    def generate(self) -> Dict[str, Any]:
        """
        Generate OpenAPI 3.0 specification.

        Returns:
            OpenAPI spec as dictionary

        Example output structure:
            {
                "openapi": "3.0.3",
                "info": {
                    "title": "GAIA API",
                    "version": "1.0.0",
                    "description": "..."
                },
                "servers": [{"url": "/v1"}],
                "paths": {...},
                "components": {
                    "schemas": {...}
                }
            }
        """
        spec: Dict[str, Any] = {
            "openapi": self.OPENAPI_VERSION,
            "info": {
                "title": self.title,
                "version": self.version,
                "description": self.description,
            },
            "servers": self._generate_servers(),
            "paths": self._extract_paths(),
            "components": {
                "schemas": self._extract_schemas(),
            },
        }

        return spec

    def generate_json(self, indent: int = 2) -> str:
        """
        Generate OpenAPI spec as JSON string.

        Args:
            indent: JSON indentation level

        Returns:
            JSON string representation of OpenAPI spec

        Example:
            >>> generator = OpenAPIGenerator(app)
            >>> json_spec = generator.generate_json()
        """
        return json.dumps(self.generate(), indent=indent)

    def generate_swagger_ui(
        self,
        spec_url: str = "/openapi.json",
        title: str = "GAIA API - Swagger UI",
    ) -> str:
        """
        Generate Swagger UI HTML page.

        Args:
            spec_url: URL to OpenAPI JSON spec
            title: Page title

        Returns:
            Complete HTML string

        Example:
            >>> generator = OpenAPIGenerator(app)
            >>> html = generator.generate_swagger_ui(spec_url="/api/openapi.json")
        """
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
    <style>
        html {{
            box-sizing: border-box;
        }}
        *, *:before, *:after {{
            box-sizing: inherit;
        }}
        body {{
            margin: 0;
            background: #fafafa;
        }}
    </style>
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
    <script>
        window.onload = function() {{
            SwaggerUIBundle({{
                url: "{spec_url}",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIStandalonePreset
                ],
                plugins: [
                    SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "StandaloneLayout"
            }});
        }};
    </script>
</body>
</html>"""

    def generate_redoc(
        self,
        spec_url: str = "/openapi.json",
        title: str = "GAIA API - Documentation",
    ) -> str:
        """
        Generate ReDoc HTML page.

        Args:
            spec_url: URL to OpenAPI JSON spec
            title: Page title

        Returns:
            Complete HTML string

        Example:
            >>> generator = OpenAPIGenerator(app)
            >>> html = generator.generate_redoc(spec_url="/api/openapi.json")
        """
        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.css">
</head>
<body>
    <redoc spec-url="{spec_url}"></redoc>
    <script src="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js"> </script>
</body>
</html>"""

    def add_routes(self, router_prefix: str = "") -> None:
        """
        Add OpenAPI routes to the FastAPI app.

        Routes added:
            - GET {prefix}/openapi.json - OpenAPI JSON spec
            - GET {prefix}/docs - Swagger UI
            - GET {prefix}/redoc - ReDoc

        Args:
            router_prefix: Optional URL prefix for docs routes

        Example:
            >>> generator = OpenAPIGenerator(app)
            >>> generator.add_routes("/api")
            >>> # Now /api/openapi.json, /api/docs, /api/redoc are available
        """
        from fastapi import Response

        # Add OpenAPI JSON route
        @self.app.get(f"{router_prefix}/openapi.json", include_in_schema=False)
        def get_openapi_json() -> Dict[str, Any]:
            return self.generate()

        # Add Swagger UI route
        @self.app.get(f"{router_prefix}/docs", include_in_schema=False)
        def get_swagger_ui() -> Response:
            html = self.generate_swagger_ui(spec_url=f"{router_prefix}/openapi.json")
            return Response(content=html, media_type="text/html")

        # Add ReDoc route
        @self.app.get(f"{router_prefix}/redoc", include_in_schema=False)
        def get_redoc() -> Response:
            html = self.generate_redoc(spec_url=f"{router_prefix}/openapi.json")
            return Response(content=html, media_type="text/html")

    def _generate_servers(self) -> List[Dict[str, str]]:
        """Generate servers section of OpenAPI spec."""
        return [
            {"url": "/v1", "description": "API v1"},
            {"url": "/v2", "description": "API v2"},
        ]

    def _extract_paths(self) -> Dict[str, Any]:
        """
        Extract path operations from app routes.

        Returns:
            Dictionary of paths with their operations

        Example:
            >>> paths = generator._extract_paths()
            >>> "/v1/chat/completions" in paths
            True
        """
        paths: Dict[str, Any] = {}

        for route in self.app.routes:
            if not isinstance(route, APIRoute):
                continue

            if not route.include_in_schema:
                continue

            path = route.path

            for method in route.methods:
                if method.lower() not in ["get", "post", "put", "delete", "patch", "options"]:
                    continue

                operation = self._extract_operation(route, method.lower())
                if path not in paths:
                    paths[path] = {}
                paths[path][method.lower()] = operation

        return paths

    def _extract_operation(self, route: APIRoute, method: str) -> Dict[str, Any]:
        """
        Extract operation details from route.

        Args:
            route: FastAPI APIRoute
            method: HTTP method

        Returns:
            Operation dictionary
        """
        operation: Dict[str, Any] = {
            "summary": route.summary or route.name.replace("_", " ").title(),
            "operationId": route.unique_id,
            "responses": self._extract_responses(route),
        }

        # Add description from docstring
        if route.description:
            operation["description"] = route.description

        # Parse docstring for additional info
        if route.endpoint.__doc__:
            doc_info = self._parse_docstring(route.endpoint.__doc__)
            if doc_info.get("description"):
                operation["description"] = doc_info["description"]
            if doc_info.get("tags"):
                operation["tags"] = doc_info["tags"]

        # Add request body if present
        request_body = self._extract_request_body(route)
        if request_body:
            operation["requestBody"] = request_body

        # Add parameters (path, query, header)
        parameters = self._extract_parameters(route)
        if parameters:
            operation["parameters"] = parameters

        # Add tags
        if route.tags:
            operation["tags"] = list(route.tags)

        return operation

    def _extract_request_body(self, route: APIRoute) -> Optional[Dict[str, Any]]:
        """Extract request body schema from route."""
        # Try different FastAPI versions
        params = getattr(route, 'body_params', None)
        if params is None:
            # FastAPI 0.100+ uses different structure
            return None

        for param in params:
            param_type = getattr(param, 'type_', None)
            if param_type and isinstance(param_type, type) and issubclass(param_type, BaseModel):
                schema_ref = self._get_schema_ref(param_type)
                return {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": schema_ref}
                        }
                    },
                }
        return None

    def _extract_parameters(self, route: APIRoute) -> List[Dict[str, Any]]:
        """Extract parameters from route."""
        parameters = []

        # Handle different FastAPI versions
        path_params = getattr(route, 'path_params', None)
        if path_params:
            for param in path_params:
                parameters.append({
                    "name": param.name if hasattr(param, 'name') else str(param),
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                })

        query_params = getattr(route, 'query_params', None)
        if query_params:
            for param in query_params:
                param_schema = {"type": "string"}
                param_type = getattr(param, 'type_', None)
                if param_type == int:
                    param_schema = {"type": "integer"}
                elif param_type == bool:
                    param_schema = {"type": "boolean"}

                parameters.append({
                    "name": param.name if hasattr(param, 'name') else str(param),
                    "in": "query",
                    "required": getattr(param, 'required', False),
                    "schema": param_schema,
                })

        return parameters

    def _extract_responses(self, route: APIRoute) -> Dict[str, Any]:
        """Extract responses from route."""
        responses = {
            "200": {
                "description": "Successful response",
            },
        }

        # Try to extract response schema from return type
        type_hints = get_type_hints(route.endpoint)
        return_type = type_hints.get("return")

        if return_type:
            schema = self._extract_type_schema(return_type)
            if schema:
                responses["200"]["content"] = {
                    "application/json": {
                        "schema": schema
                    }
                }

        return responses

    def _extract_schemas(self) -> Dict[str, Any]:
        """
        Extract Pydantic schemas from app.

        Returns:
            Dictionary of schema definitions

        Example:
            >>> schemas = generator._extract_schemas()
            >>> "ChatRequest" in schemas
            True
        """
        schemas: Dict[str, Any] = {}

        # Collect all Pydantic models used in routes
        for route in self.app.routes:
            if not isinstance(route, APIRoute):
                continue

            # Extract from body params (handle different FastAPI versions)
            body_params = getattr(route, 'body_params', None)
            if body_params:
                for param in body_params:
                    param_type = getattr(param, 'type_', None)
                    if param_type and isinstance(param_type, type) and issubclass(param_type, BaseModel):
                        schemas.update(self._extract_model_schema(param_type))

            # Extract from response models
            if route.response_model and issubclass(route.response_model, BaseModel):
                schemas.update(self._extract_model_schema(route.response_model))

            # Extract from type hints
            try:
                type_hints = get_type_hints(route.endpoint)
                for hint_type in type_hints.values():
                    schemas.update(self._extract_types_from_annotation(hint_type))
            except (NameError, AttributeError):
                pass

        return schemas

    def _extract_model_schema(self, model: Type[BaseModel]) -> Dict[str, Any]:
        """
        Extract schema from Pydantic model.

        Args:
            model: Pydantic BaseModel class

        Returns:
            Schema dictionary
        """
        model_name = model.__name__

        if model_name in self._schema_cache:
            return {model_name: self._schema_cache[model_name]}

        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {},
        }

        # Get model fields
        if hasattr(model, "model_fields"):
            # Pydantic v2
            for field_name, field_info in model.model_fields.items():
                field_type = field_info.annotation
                schema["properties"][field_name] = self._extract_type_schema(field_type)
                if field_info.is_required():
                    if "required" not in schema:
                        schema["required"] = []
                    schema["required"].append(field_name)
        elif hasattr(model, "__fields__"):
            # Pydantic v1
            for field_name, field in model.__fields__.items():
                field_type = field.outer_type_
                schema["properties"][field_name] = self._extract_type_schema(field_type)
                if field.required:
                    if "required" not in schema:
                        schema["required"] = []
                    schema["required"].append(field_name)

        # Add model description if available
        if model.__doc__:
            schema["description"] = model.__doc__.strip()

        self._schema_cache[model_name] = schema
        return {model_name: schema}

    def _extract_types_from_annotation(self, annotation: Any) -> Dict[str, Any]:
        """Extract Pydantic models from type annotation."""
        schemas = {}

        origin = get_origin(annotation)
        args = get_args(annotation)

        if origin:
            for arg in args:
                if isinstance(arg, type) and issubclass(arg, BaseModel):
                    schemas.update(self._extract_model_schema(arg))
                elif arg:
                    schemas.update(self._extract_types_from_annotation(arg))
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            schemas.update(self._extract_model_schema(annotation))

        return schemas

    def _extract_type_schema(self, type_hint: Any) -> Dict[str, Any]:
        """
        Extract JSON schema from type hint.

        Args:
            type_hint: Type annotation

        Returns:
            JSON schema dictionary
        """
        if type_hint is str:
            return {"type": "string"}
        elif type_hint is int:
            return {"type": "integer"}
        elif type_hint is float:
            return {"type": "number"}
        elif type_hint is bool:
            return {"type": "boolean"}
        elif type_hint is type(None):
            return {"type": "null"}

        origin = get_origin(type_hint)
        args = get_args(type_hint)

        if origin is list or origin is List:
            if args:
                items = self._extract_type_schema(args[0])
                return {"type": "array", "items": items}
            return {"type": "array"}
        elif origin is dict or origin is Dict:
            return {"type": "object"}
        elif origin is Optional:
            if args:
                schema = self._extract_type_schema(args[0])
                return {"anyOf": [schema, {"type": "null"}]}
        elif origin:
            return {"type": "object"}

        if isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
            return {"$ref": f"#/components/schemas/{type_hint.__name__}"}

        return {"type": "string"}

    def _get_schema_ref(self, model: Type[BaseModel]) -> str:
        """Get schema reference string for model."""
        return f"#/components/schemas/{model.__name__}"

    def _parse_docstring(self, docstring: str) -> Dict[str, Any]:
        """
        Parse function docstring for operation metadata.

        Args:
            docstring: Function docstring

        Returns:
            Dictionary with extracted metadata

        Example:
            >>> doc = generator._parse_docstring('''
            ...     Create a chat completion.
            ...
            ...     Tags: chat, inference
            ... ''')
            >>> doc["tags"]
            ['chat', 'inference']
        """
        result: Dict[str, Any] = {}

        if not docstring:
            return result

        lines = docstring.strip().split("\n")

        # First non-empty line is description
        description_lines = []
        in_description = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_description:
                    break
                continue

            if stripped.startswith("Tags:"):
                tags_str = stripped.replace("Tags:", "").strip()
                result["tags"] = [t.strip() for t in tags_str.split(",")]
            elif stripped.startswith("Args:") or stripped.startswith("Returns:"):
                break
            else:
                in_description = True
                description_lines.append(stripped)

        if description_lines:
            result["description"] = " ".join(description_lines)

        return result
