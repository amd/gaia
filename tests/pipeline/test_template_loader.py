"""
Tests for GAIA Template Loader

Tests YAML template loading, parsing, and validation functionality.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from gaia.agents.base import AgentCapabilities, AgentDefinition, AgentTriggers
from gaia.agents.registry import AgentRegistry
from gaia.pipeline.recursive_template import (
    AgentCategory,
    PhaseConfig,
    RecursivePipelineTemplate,
    RoutingRule,
    SelectionMode,
)
from gaia.pipeline.template_loader import (
    TemplateLoader,
    TemplateValidationError,
)


class TestTemplateLoader:
    """Test cases for TemplateLoader class."""

    @pytest.fixture
    def template_loader(self):
        """Create a TemplateLoader instance for testing."""
        return TemplateLoader(template_dir="/tmp/templates")

    @pytest.fixture
    def sample_yaml_template(self):
        """Sample YAML template content for testing."""
        return """
agent_categories:
  PLANNING:
    - id: planning-analysis-strategist
      name: "Planning & Strategy"

templates:
  test-template:
    name: "Test Template"
    description: "A test template for unit tests"

    configuration:
      quality_threshold: 85
      max_iterations: 5

    phases:
      - category: PLANNING
        selection: auto
        agents:
          - planning-analysis-strategist
        output: test_plan

      - category: DEVELOPMENT
        selection: sequential
        agents:
          - senior-developer
        output: test_implementation

    routing_rules:
      - condition: "defect_type == 'security'"
        route_to:
          category: REVIEW
          agent: security-auditor
        loop_back: true
        guidance: "Fix security issues first"

    quality_weights:
      code_quality: 0.30
      requirements_coverage: 0.25
      testing: 0.20
      documentation: 0.15
      best_practices: 0.10
"""

    @pytest.fixture
    def mock_agent_registry(self):
        """Create a mock agent registry for testing."""
        registry = MagicMock(spec=AgentRegistry)

        # Create mock agents
        planning_agent = AgentDefinition(
            id="planning-analysis-strategist",
            name="Planning Strategist",
            version="1.0.0",
            category="planning",
            description="Test planning agent",
            triggers=AgentTriggers(keywords=["planning"], phases=["PLANNING"]),
            capabilities=AgentCapabilities(capabilities=["analysis"]),
        )

        dev_agent = AgentDefinition(
            id="senior-developer",
            name="Senior Developer",
            version="1.0.0",
            category="development",
            description="Test developer agent",
            triggers=AgentTriggers(keywords=["development"], phases=["DEVELOPMENT"]),
            capabilities=AgentCapabilities(capabilities=["coding"]),
        )

        # Configure mock get_agent to return agents
        def get_agent_side_effect(agent_id):
            agents = {
                "planning-analysis-strategist": planning_agent,
                "senior-developer": dev_agent,
            }
            return agents.get(agent_id)

        registry.get_agent.side_effect = get_agent_side_effect
        return registry

    def test_init(self, template_loader):
        """Test TemplateLoader initialization."""
        assert template_loader._template_dir == Path("/tmp/templates")
        assert template_loader._loaded_templates == {}

    def test_load_from_string(self, template_loader, sample_yaml_template):
        """Test loading templates from YAML string."""
        templates = template_loader.load_from_string(sample_yaml_template)

        assert "test-template" in templates
        template = templates["test-template"]

        assert template.name == "test-template"
        assert template.description == "A test template for unit tests"
        assert template.quality_threshold == 0.85
        assert template.max_iterations == 5

    def test_load_from_string_invalid_yaml(self, template_loader):
        """Test loading invalid YAML raises error."""
        invalid_yaml = """
templates:
  invalid:
    name: "Missing closing quote
"""
        with pytest.raises(TemplateValidationError):
            template_loader.load_from_string(invalid_yaml)

    def test_load_from_string_empty(self, template_loader):
        """Test loading empty YAML raises error."""
        with pytest.raises(TemplateValidationError, match="Empty YAML"):
            template_loader.load_from_string("")

    def test_load_from_string_missing_templates_section(self, template_loader):
        """Test loading YAML without templates section raises error."""
        yaml_without_templates = """
agent_categories:
  PLANNING:
    - id: test-agent
"""
        with pytest.raises(TemplateValidationError, match="No 'templates' section"):
            template_loader.load_from_string(yaml_without_templates)

    def test_build_phases(self, template_loader, sample_yaml_template):
        """Test phase configuration parsing."""
        data = yaml.safe_load(sample_yaml_template)
        template = template_loader._parse_yaml(data)

        phases = template["test-template"].phases

        assert len(phases) == 2

        # Check first phase
        planning_phase = phases[0]
        assert planning_phase.name == "PLANNING"
        assert planning_phase.category == AgentCategory.PLANNING
        assert planning_phase.selection_mode == SelectionMode.AUTO
        assert "planning-analysis-strategist" in planning_phase.agents
        assert planning_phase.exit_criteria == {"artifact": "test_plan"}

        # Check second phase
        dev_phase = phases[1]
        assert dev_phase.name == "DEVELOPMENT"
        assert dev_phase.category == AgentCategory.DEVELOPMENT
        assert dev_phase.selection_mode == SelectionMode.SEQUENTIAL
        assert "senior-developer" in dev_phase.agents
        assert dev_phase.exit_criteria == {"artifact": "test_implementation"}

    def test_build_routing_rules(self, template_loader, sample_yaml_template):
        """Test routing rules parsing."""
        data = yaml.safe_load(sample_yaml_template)
        template = template_loader._parse_yaml(data)

        rules = template["test-template"].routing_rules

        assert len(rules) == 1
        rule = rules[0]

        assert rule.condition == "defect_type == 'security'"
        assert rule.route_to == "security-auditor"
        assert rule.loop_back is True
        assert rule.guidance == "Fix security issues first"

    def test_build_agent_categories(self, template_loader, sample_yaml_template):
        """Test agent categories mapping."""
        data = yaml.safe_load(sample_yaml_template)
        template = template_loader._parse_yaml(data)

        categories = template["test-template"].agent_categories

        assert "planning" in categories
        assert "planning-analysis-strategist" in categories["planning"]
        assert "development" in categories
        assert "senior-developer" in categories["development"]

    def test_quality_weights(self, template_loader, sample_yaml_template):
        """Test quality weights parsing."""
        data = yaml.safe_load(sample_yaml_template)
        template = template_loader._parse_yaml(data)

        weights = template["test-template"].quality_weights

        assert weights["code_quality"] == 0.30
        assert weights["requirements_coverage"] == 0.25
        assert weights["testing"] == 0.20
        assert weights["documentation"] == 0.15
        assert weights["best_practices"] == 0.10

    def test_validate_template_success(self, template_loader, mock_agent_registry):
        """Test successful template validation."""
        data = yaml.safe_load("""
templates:
  valid-template:
    name: "Valid Template"
    configuration:
      quality_threshold: 90
    phases:
      - category: PLANNING
        selection: auto
        agents:
          - planning-analysis-strategist
        output: plan
""")
        template = template_loader._parse_yaml(data)["valid-template"]

        errors = template_loader.validate_template(template, mock_agent_registry)

        assert len(errors) == 0

    def test_validate_template_missing_agent(self, template_loader):
        """Test validation fails for missing agent."""
        registry = MagicMock(spec=AgentRegistry)
        registry.get_agent.return_value = None  # No agents found

        data = yaml.safe_load("""
templates:
  invalid-template:
    name: "Invalid Template"
    configuration:
      quality_threshold: 90
    phases:
      - category: PLANNING
        agents:
          - non-existent-agent
        output: plan
""")
        template = template_loader._parse_yaml(data)["invalid-template"]

        errors = template_loader.validate_template(template, registry)

        assert len(errors) > 0
        assert any("non-existent-agent" in error for error in errors)

    def test_validate_template_invalid_threshold(
        self, template_loader, mock_agent_registry
    ):
        """Test validation catches invalid quality threshold."""
        # Creating template with invalid threshold raises ValueError
        with pytest.raises(ValueError, match="quality_threshold must be between 0 and 1"):
            RecursivePipelineTemplate(
                name="bad-template",
                quality_threshold=1.5,  # Invalid: > 1
                max_iterations=5,
            )

    def test_validate_template_invalid_iterations(
        self, template_loader, mock_agent_registry
    ):
        """Test validation catches invalid max iterations."""
        # Creating template with invalid iterations raises ValueError
        with pytest.raises(ValueError, match="max_iterations must be at least 1"):
            RecursivePipelineTemplate(
                name="bad-template",
                quality_threshold=0.9,
                max_iterations=0,  # Invalid: < 1
            )

    def test_load_template_caching(self, template_loader, sample_yaml_template):
        """Test that loaded templates are cached."""
        # First load
        templates1 = template_loader.load_from_string(sample_yaml_template)

        # Manually add to cache (simulating load_template behavior)
        template_loader._loaded_templates["test-template"] = templates1["test-template"]

        # Verify cache hit
        assert "test-template" in template_loader._loaded_templates

    def test_clear_cache(self, template_loader, sample_yaml_template):
        """Test cache clearing."""
        templates = template_loader.load_from_string(sample_yaml_template)
        template_loader._loaded_templates = templates

        assert len(template_loader._loaded_templates) > 0

        template_loader.clear_cache()

        assert len(template_loader._loaded_templates) == 0

    def test_get_available_templates(self, template_loader, sample_yaml_template):
        """Test getting available template names."""
        templates = template_loader.load_from_string(sample_yaml_template)

        names = list(templates.keys())

        assert "test-template" in names

    def test_unknown_category_defaults_to_planning(self, template_loader):
        """Test that unknown category defaults to PLANNING."""
        yaml_content = """
templates:
  test:
    name: "Test"
    configuration:
      quality_threshold: 90
    phases:
      - category: UNKNOWN_CATEGORY
        agents:
          - test-agent
        output: output
"""
        data = yaml.safe_load(yaml_content)
        template = template_loader._parse_yaml(data)

        phase = template["test"].phases[0]
        assert phase.category == AgentCategory.PLANNING

    def test_routing_rule_with_string_route_to(self, template_loader):
        """Test routing rule with simple string route_to."""
        yaml_content = """
templates:
  test:
    name: "Test"
    configuration:
      quality_threshold: 90
    phases:
      - category: PLANNING
        agents: []
        output: output
    routing_rules:
      - condition: "quality_score < 0.5"
        route_to: "PLANNING"
        priority: 1
"""
        data = yaml.safe_load(yaml_content)
        template = template_loader._parse_yaml(data)

        rule = template["test"].routing_rules[0]
        assert rule.route_to == "PLANNING"
        assert rule.priority == 1

    def test_load_from_file(self, template_loader, sample_yaml_template, tmp_path):
        """Test loading templates from file."""
        # Create temporary file
        yaml_file = tmp_path / "test_templates.yml"
        yaml_file.write_text(sample_yaml_template)

        templates = template_loader.load_from_file(yaml_file)

        assert "test-template" in templates
        assert templates["test-template"].name == "test-template"

    def test_load_from_file_not_found(self, template_loader):
        """Test loading non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            template_loader.load_from_file("/nonexistent/path/templates.yml")

    def test_load_template_by_name(
        self, template_loader, sample_yaml_template, tmp_path
    ):
        """Test loading a single template by name."""
        # Create temporary file
        yaml_file = tmp_path / "test_templates.yml"
        yaml_file.write_text(sample_yaml_template)

        # Load specific template by name
        template = template_loader.load_template("test-template", yaml_file)

        assert template.name == "test-template"
        assert template.description == "A test template for unit tests"
        assert template.quality_threshold == 0.85

    def test_load_template_cache_hit(
        self, template_loader, sample_yaml_template, tmp_path
    ):
        """Test that load_template uses cache when available."""
        yaml_file = tmp_path / "test_templates.yml"
        yaml_file.write_text(sample_yaml_template)

        # First load - populates cache
        template1 = template_loader.load_template("test-template", yaml_file)

        # Modify the file to verify cache is used
        yaml_file.write_text("""
templates:
  test-template:
    name: "Modified Template"
    configuration:
      quality_threshold: 99
""")

        # Second load - should use cache
        template2 = template_loader.load_template("test-template", yaml_file)

        # Should still have original values from cache
        assert template2.name == "test-template"

    def test_load_template_not_found(self, template_loader, tmp_path):
        """Test loading non-existent template raises KeyError."""
        yaml_file = tmp_path / "test_templates.yml"
        yaml_file.write_text("""
templates:
  existing-template:
    name: "Existing"
    configuration:
      quality_threshold: 90
""")

        with pytest.raises(KeyError, match="Template 'non-existent' not found"):
            template_loader.load_template("non-existent", yaml_file)

    def test_quality_threshold_already_normalized(self, template_loader):
        """Test that quality threshold in 0-1 scale is not divided."""
        yaml_content = """
templates:
  test:
    name: "Test"
    configuration:
      quality_threshold: 0.85  # Already in 0-1 scale
"""
        data = yaml.safe_load(yaml_content)
        templates = template_loader._parse_yaml(data)

        # Should remain 0.85, not become 0.0085
        assert templates["test"].quality_threshold == 0.85

    def test_quality_threshold_percentage(self, template_loader):
        """Test that quality threshold in percentage scale is converted."""
        yaml_content = """
templates:
  test:
    name: "Test"
    configuration:
      quality_threshold: 85  # Percentage scale
"""
        data = yaml.safe_load(yaml_content)
        templates = template_loader._parse_yaml(data)

        # Should be converted to 0.85
        assert templates["test"].quality_threshold == 0.85

    def test_agent_categories_from_top_level_def(self, template_loader):
        """Test that agent_categories_def is properly used."""
        yaml_content = """
agent_categories:
  PLANNING:
    - planner-agent-1
    - planner-agent-2
  DEVELOPMENT:
    - developer-agent-1

templates:
  test:
    name: "Test"
    configuration:
      quality_threshold: 90
    phases:
      - category: PLANNING
        agents:
          - phase-specific-agent
        output: output
"""
        data = yaml.safe_load(yaml_content)
        templates = template_loader._parse_yaml(data)
        template = templates["test"]

        # Should include agents from top-level definition
        assert "planning" in template.agent_categories
        assert "planner-agent-1" in template.agent_categories["planning"]
        assert "planner-agent-2" in template.agent_categories["planning"]

        # Should also merge phase-specific agents
        assert "phase-specific-agent" in template.agent_categories["planning"]

        # Should include development category from top-level
        assert "development" in template.agent_categories
        assert "developer-agent-1" in template.agent_categories["development"]


class TestTemplateLoaderIntegration:
    """Integration tests for TemplateLoader with real agent registry."""

    @pytest.fixture
    def temp_template_file(self, tmp_path):
        """Create a temporary template file."""
        yaml_content = """
agent_categories:
  PLANNING:
    - id: planning-analysis-strategist
      name: "Planning"

templates:
  integration-test:
    name: "Integration Test Template"
    description: "Template for integration testing"
    configuration:
      quality_threshold: 88
      max_iterations: 7
    phases:
      - category: PLANNING
        selection: auto
        agents:
          - planning-analysis-strategist
        output: integration_plan
      - category: DEVELOPMENT
        selection: auto
        agents:
          - senior-developer
        output: integration_code
    routing_rules:
      - condition: "defect_type == 'missing_tests'"
        route_to:
          category: DEVELOPMENT
        loop_back: true
    quality_weights:
      code_quality: 0.25
      requirements_coverage: 0.25
      testing: 0.25
      documentation: 0.15
      best_practices: 0.10
"""
        yaml_file = tmp_path / "integration_templates.yml"
        yaml_file.write_text(yaml_content)
        return yaml_file

    def test_full_template_loading_and_validation(self, temp_template_file):
        """Test complete template loading and validation workflow."""
        loader = TemplateLoader()

        # Load template
        templates = loader.load_from_file(temp_template_file)

        assert "integration-test" in templates
        template = templates["integration-test"]

        # Verify all template properties
        assert template.name == "integration-test"
        assert template.description == "Template for integration testing"
        assert template.quality_threshold == 0.88
        assert template.max_iterations == 7
        assert len(template.phases) == 2
        assert len(template.routing_rules) == 1

        # Verify phase configuration
        planning_phase = template.get_phase("PLANNING")
        assert planning_phase is not None
        assert planning_phase.exit_criteria == {"artifact": "integration_plan"}

        dev_phase = template.get_phase("DEVELOPMENT")
        assert dev_phase is not None
        assert dev_phase.exit_criteria == {"artifact": "integration_code"}

        # Verify routing rule
        rule = template.routing_rules[0]
        assert rule.condition == "defect_type == 'missing_tests'"
        assert rule.loop_back is True


class TestRecursivePipelineTemplateWithYaml:
    """Test RecursivePipelineTemplate integration with YAML loading."""

    def test_template_from_yaml_matches_direct_construction(self):
        """Verify YAML-loaded template matches directly constructed one."""
        yaml_content = """
templates:
  direct-comparison:
    name: "Direct Comparison"
    description: "Compare YAML vs direct construction"
    configuration:
      quality_threshold: 90
      max_iterations: 10
    phases:
      - category: PLANNING
        selection: auto
        agents:
          - planner-agent
        output: plan
    routing_rules:
      - condition: "quality_score < threshold"
        route_to: "PLANNING"
        loop_back: true
    quality_weights:
      code_quality: 0.25
      requirements_coverage: 0.25
      testing: 0.20
      documentation: 0.15
      best_practices: 0.15
"""
        loader = TemplateLoader()
        yaml_template = loader.load_from_string(yaml_content)["direct-comparison"]

        direct_template = RecursivePipelineTemplate(
            name="direct-comparison",
            description="Compare YAML vs direct construction",
            quality_threshold=0.90,
            max_iterations=10,
            agent_categories={"planning": ["planner-agent"]},
            phases=[
                PhaseConfig(
                    name="PLANNING",
                    category=AgentCategory.PLANNING,
                    selection_mode=SelectionMode.AUTO,
                    agents=["planner-agent"],
                    exit_criteria={"artifact": "plan"},
                ),
            ],
            routing_rules=[
                RoutingRule(
                    condition="quality_score < threshold",
                    route_to="PLANNING",
                    loop_back=True,
                ),
            ],
            quality_weights={
                "code_quality": 0.25,
                "requirements_coverage": 0.25,
                "testing": 0.20,
                "documentation": 0.15,
                "best_practices": 0.15,
            },
        )

        # Compare key attributes
        assert yaml_template.name == direct_template.name
        assert yaml_template.description == direct_template.description
        assert yaml_template.quality_threshold == direct_template.quality_threshold
        assert yaml_template.max_iterations == direct_template.max_iterations
        assert len(yaml_template.phases) == len(direct_template.phases)
        assert len(yaml_template.routing_rules) == len(direct_template.routing_rules)
