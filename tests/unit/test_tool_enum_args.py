"""A Literal-typed tool argument reaches the model as a JSON-schema enum."""

from types import SimpleNamespace
from typing import Literal, Optional

from gaia.agents.base.tools import tool


def test_literal_becomes_enum_in_the_registry():
    registry = {}

    @tool(registry=registry)
    def pick(mode: Literal["text", "html"] = "text", other: Optional[str] = None):
        """Pick.

        Args:
            mode: Which.
            other: Something else.
        """

    params = registry["pick"]["parameters"]
    assert params["mode"]["enum"] == ["text", "html"]
    assert params["mode"]["type"] == "string"
    assert "enum" not in params["other"]


def test_fetch_page_schema_lists_its_extract_modes():
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    class Probe(BrowserToolsMixin):
        _web_client = None

    Probe().register_browser_tools()
    agent = SimpleNamespace(
        _tools_registry={"fetch_page": _TOOL_REGISTRY["fetch_page"]}
    )
    (schema,) = Agent._build_openai_tool_schemas(agent)
    extract = schema["function"]["parameters"]["properties"]["extract"]
    assert extract == {
        "type": "string",
        "description": extract["description"],
        "enum": ["text", "html", "links", "tables"],
    }
