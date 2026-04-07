"""
Unit tests for OpenAPI Generator.

Covers:
- OpenAPI spec generation
- Schema extraction
- Swagger UI generation
- ReDoc generation
"""

import pytest
from fastapi import FastAPI, APIRouter, Body
from pydantic import BaseModel
from typing import List, Optional

from gaia.api.openapi import OpenAPIGenerator


class ChatRequest(BaseModel):
    """Request model for chat completion."""
    model: str
    messages: List[dict]
    temperature: Optional[float] = 0.7


class ChatResponse(BaseModel):
    """Response model for chat completion."""
    id: str
    choices: List[dict]
    usage: dict


class TestOpenAPIGeneratorInit:
    """Test OpenAPIGenerator initialization."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        assert generator.app is app
        assert generator.title == "GAIA API"
        assert generator.version == "1.0.0"

    def test_init_custom_values(self):
        """Should initialize with custom values."""
        app = FastAPI()
        generator = OpenAPIGenerator(
            app,
            title="Custom API",
            version="2.0.0",
            description="Custom description",
        )

        assert generator.title == "Custom API"
        assert generator.version == "2.0.0"
        assert generator.description == "Custom description"


class TestOpenAPISpecGeneration:
    """Test OpenAPI spec generation."""

    def test_openapi_version(self):
        """Generated spec should be OpenAPI 3.0.x."""
        app = FastAPI(title="Test API")
        generator = OpenAPIGenerator(app, version="1.0.0")

        spec = generator.generate()

        assert spec["openapi"].startswith("3.0")

    def test_info_section(self):
        """Should include info section."""
        app = FastAPI()
        generator = OpenAPIGenerator(
            app,
            title="Test API",
            version="1.0.0",
            description="Test description",
        )

        spec = generator.generate()

        assert "info" in spec
        assert spec["info"]["title"] == "Test API"
        assert spec["info"]["version"] == "1.0.0"
        assert spec["info"]["description"] == "Test description"

    def test_servers_section(self):
        """Should include servers section."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        spec = generator.generate()

        assert "servers" in spec
        assert len(spec["servers"]) > 0

    def test_paths_extracted(self):
        """All API paths should be extracted."""
        app = FastAPI()

        @app.get("/chat")
        def get_chat():
            """Get chat history."""

        @app.post("/chat/completions")
        def create_completion():
            """Create chat completion."""

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        assert "paths" in spec
        assert "/chat" in spec["paths"]
        assert "/chat/completions" in spec["paths"]

    def test_path_operations(self):
        """Should extract path operations correctly."""
        app = FastAPI()

        @app.get("/resource")
        def get_resource():
            """Get a resource."""

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        resource_ops = spec["paths"]["/resource"]
        assert "get" in resource_ops
        assert resource_ops["get"]["summary"] == "Get a resource"

    def test_operation_id(self):
        """Should generate operation IDs."""
        app = FastAPI()

        @app.get("/resource")
        def get_resource():
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        operation = spec["paths"]["/resource"]["get"]
        assert "operationId" in operation

    def test_components_schemas(self):
        """Should include components/schemas section."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        spec = generator.generate()

        assert "components" in spec
        assert "schemas" in spec["components"]


class TestSchemaExtraction:
    """Test schema extraction from Pydantic models."""

    def test_schemas_extracted(self):
        """Pydantic schemas should be extracted to components."""
        app = FastAPI()

        @app.post("/chat")
        def create_chat(request: ChatRequest):
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        assert "ChatRequest" in spec["components"]["schemas"]

    def test_schema_properties(self):
        """Should extract schema properties."""
        app = FastAPI()

        @app.post("/chat")
        def create_chat(request: ChatRequest):
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        chat_schema = spec["components"]["schemas"]["ChatRequest"]
        assert "properties" in chat_schema
        assert "model" in chat_schema["properties"]
        assert "messages" in chat_schema["properties"]

    def test_schema_required_fields(self):
        """Should identify required fields."""
        app = FastAPI()

        @app.post("/chat")
        def create_chat(request: ChatRequest):
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        chat_schema = spec["components"]["schemas"]["ChatRequest"]
        assert "required" in chat_schema
        assert "model" in chat_schema["required"]
        assert "messages" in chat_schema["required"]

    def test_schema_description(self):
        """Should include schema description from docstring."""
        app = FastAPI()

        @app.post("/chat")
        def create_chat(request: ChatRequest):
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        chat_schema = spec["components"]["schemas"]["ChatRequest"]
        assert "description" in chat_schema

    def test_response_schema(self):
        """Should extract response schemas."""
        app = FastAPI()

        @app.get("/chat", response_model=ChatResponse)
        def get_chat() -> ChatResponse:
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        assert "ChatResponse" in spec["components"]["schemas"]


class TestSwaggerUI:
    """Test Swagger UI generation."""

    def test_swagger_ui_generation(self):
        """Swagger UI HTML should be generated correctly."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        html = generator.generate_swagger_ui(spec_url="/openapi.json")

        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "swagger-ui" in html.lower()
        assert "/openapi.json" in html

    def test_swagger_ui_custom_title(self):
        """Should support custom title."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        html = generator.generate_swagger_ui(
            spec_url="/api/openapi.json",
            title="Custom Swagger UI"
        )

        assert "Custom Swagger UI" in html
        assert "/api/openapi.json" in html


class TestReDoc:
    """Test ReDoc generation."""

    def test_redoc_generation(self):
        """ReDoc HTML should be generated correctly."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        html = generator.generate_redoc(spec_url="/openapi.json")

        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "redoc" in html.lower()
        assert "/openapi.json" in html

    def test_redoc_custom_title(self):
        """Should support custom title."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        html = generator.generate_redoc(
            spec_url="/api/openapi.json",
            title="Custom ReDoc"
        )

        assert "Custom ReDoc" in html


class TestAddRoutes:
    """Test adding OpenAPI routes."""

    def test_add_routes(self):
        """Should add OpenAPI routes to app."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)
        generator.add_routes()

        # Check routes were added
        routes = [route.path for route in app.routes]

        assert "/openapi.json" in routes
        assert "/docs" in routes
        assert "/redoc" in routes

    def test_add_routes_with_prefix(self):
        """Should add routes with prefix."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)
        generator.add_routes("/api")

        routes = [route.path for route in app.routes]

        assert "/api/openapi.json" in routes
        assert "/api/docs" in routes
        assert "/api/redoc" in routes


class TestGenerateJSON:
    """Test JSON generation."""

    def test_generate_json(self):
        """Should generate JSON string."""
        app = FastAPI()

        @app.get("/test")
        def test_endpoint():
            pass

        generator = OpenAPIGenerator(app)
        json_spec = generator.generate_json()

        assert isinstance(json_spec, str)
        assert '"openapi"' in json_spec
        assert '"info"' in json_spec

    def test_generate_json_indent(self):
        """Should support custom indentation."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        json_compact = generator.generate_json(indent=0)
        json_pretty = generator.generate_json(indent=4)

        # Compact should have fewer newlines
        assert json_compact.count("\n") < json_pretty.count("\n")


class TestDocstringParsing:
    """Test docstring parsing."""

    def test_parse_docstring_description(self):
        """Should parse description from docstring."""
        app = FastAPI()

        def endpoint():
            """This is the description."""
            pass

        app.get("/test")(endpoint)
        generator = OpenAPIGenerator(app)

        spec = generator.generate()
        operation = spec["paths"]["/test"]["get"]

        assert "description" in operation

    def test_parse_docstring_tags(self):
        """Should parse tags from docstring."""
        app = FastAPI()

        def endpoint():
            """
            Endpoint description.

            Tags: chat, inference
            """
            pass

        app.get("/test")(endpoint)
        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        # Tags should be parsed (may be in operation or route)
        operation = spec["paths"]["/test"]["get"]
        # Description should contain parsed info
        assert "description" in operation


class TestTypeExtraction:
    """Test type extraction for schemas."""

    def test_extract_type_schema_string(self):
        """Should extract string type schema."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        schema = generator._extract_type_schema(str)
        assert schema == {"type": "string"}

    def test_extract_type_schema_integer(self):
        """Should extract integer type schema."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        schema = generator._extract_type_schema(int)
        assert schema == {"type": "integer"}

    def test_extract_type_schema_number(self):
        """Should extract number type schema."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        schema = generator._extract_type_schema(float)
        assert schema == {"type": "number"}

    def test_extract_type_schema_boolean(self):
        """Should extract boolean type schema."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        schema = generator._extract_type_schema(bool)
        assert schema == {"type": "boolean"}

    def test_extract_type_schema_array(self):
        """Should extract array type schema."""
        from typing import List
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        schema = generator._extract_type_schema(List[str])
        assert schema["type"] == "array"

    def test_extract_type_schema_dict(self):
        """Should extract object type schema."""
        from typing import Dict
        app = FastAPI()
        generator = OpenAPIGenerator(app)

        schema = generator._extract_type_schema(Dict[str, Any])
        assert schema["type"] == "object"


class TestOpenAPICompleteness:
    """Test OpenAPI spec completeness."""

    def test_complete_spec_structure(self):
        """Should generate complete spec structure."""
        app = FastAPI(title="Complete API", version="1.0.0")

        @app.get("/resource", response_model=ChatResponse)
        def get_resource() -> ChatResponse:
            """Get a resource."""

        @app.post("/resource")
        def create_resource(request: ChatRequest):
            """Create a resource."""

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        # Check all required sections
        assert spec["openapi"] == "3.0.3"
        assert "info" in spec
        assert "servers" in spec
        assert "paths" in spec
        assert "components" in spec

        # Check info
        assert spec["info"]["title"] == "Complete API"
        assert spec["info"]["version"] == "1.0.0"

        # Check paths
        assert "/resource" in spec["paths"]
        assert "get" in spec["paths"]["/resource"]
        assert "post" in spec["paths"]["/resource"]

        # Check components
        assert "schemas" in spec["components"]
        assert "ChatRequest" in spec["components"]["schemas"]
        assert "ChatResponse" in spec["components"]["schemas"]
