"""
GAIA Agent Definitions

Predefined agent definitions for the 17 core GAIA agents.
"""

from typing import Dict, Any, List

# Agent definitions as YAML-style dictionaries
# These can be loaded into AgentDefinition objects

AGENT_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    # Planning Agents (4)
    "planning-analysis-strategist": {
        "agent": {
            "id": "planning-analysis-strategist",
            "name": "Planning Analysis Strategist",
            "version": "1.0.0",
            "category": "planning",
            "description": """
                Strategic planning agent that analyzes requirements,
                breaks down complex tasks, and creates implementation roadmaps.
            """,
            "triggers": {
                "keywords": [
                    "plan", "strategy", "analyze", "breakdown",
                    "roadmap", "architecture", "design", "requirements"
                ],
                "phases": ["PLANNING", "ANALYSIS"],
                "complexity_range": {"min": 0.3, "max": 1.0}
            },
            "capabilities": [
                "requirements-analysis",
                "task-breakdown",
                "strategic-planning",
                "risk-assessment",
                "roadmap-creation"
            ],
            "system_prompt": "prompts/planning-analysis-strategist.md",
            "tools": [
                "file_read", "search_codebase", "analyze_requirements"
            ],
            "execution_targets": {
                "default": "cpu"
            },
            "constraints": {
                "max_file_changes": 10,
                "max_lines_per_file": 300,
                "requires_review": True,
                "timeout_seconds": 600
            },
            "metadata": {
                "author": "GAIA Team",
                "created": "2026-03-23",
                "tags": ["planning", "analysis", "strategy"]
            }
        }
    },

    "solutions-architect": {
        "agent": {
            "id": "solutions-architect",
            "name": "Solutions Architect",
            "version": "1.0.0",
            "category": "planning",
            "description": """
                Architecture design specialist for system design,
                component diagrams, and technical specifications.
            """,
            "triggers": {
                "keywords": [
                    "architecture", "system design", "component",
                    "microservices", "scalability", "infrastructure"
                ],
                "phases": ["PLANNING", "DESIGN"],
                "complexity_range": {"min": 0.5, "max": 1.0}
            },
            "capabilities": [
                "system-architecture",
                "component-design",
                "technology-selection",
                "scalability-planning"
            ],
            "system_prompt": "prompts/solutions-architect.md",
            "tools": [
                "file_read", "file_write", "diagram_generation"
            ],
            "constraints": {
                "max_file_changes": 15,
                "requires_review": True,
                "timeout_seconds": 900
            }
        }
    },

    "api-designer": {
        "agent": {
            "id": "api-designer",
            "name": "API Designer",
            "version": "1.0.0",
            "category": "planning",
            "description": """
                API design specialist for REST, GraphQL, and gRPC APIs.
                Creates OpenAPI specs and API documentation.
            """,
            "triggers": {
                "keywords": [
                    "api", "rest", "graphql", "grpc", "endpoint",
                    "openapi", "swagger", "graphql schema"
                ],
                "phases": ["PLANNING", "DESIGN", "DEVELOPMENT"],
                "complexity_range": {"min": 0.3, "max": 1.0}
            },
            "capabilities": [
                "api-design",
                "openapi-specification",
                "graphql-schema",
                "api-documentation"
            ],
            "system_prompt": "prompts/api-designer.md",
            "tools": [
                "file_read", "file_write", "api_validation"
            ],
            "constraints": {
                "max_file_changes": 20,
                "requires_review": True
            }
        }
    },

    "database-architect": {
        "agent": {
            "id": "database-architect",
            "name": "Database Architect",
            "version": "1.0.0",
            "category": "planning",
            "description": """
                Database design specialist for schema design,
                indexing strategies, and data modeling.
            """,
            "triggers": {
                "keywords": [
                    "database", "schema", "sql", "nosql", "migration",
                    "index", "data model", "entity"
                ],
                "phases": ["PLANNING", "DESIGN", "DEVELOPMENT"],
                "complexity_range": {"min": 0.4, "max": 1.0}
            },
            "capabilities": [
                "database-design",
                "schema-modeling",
                "query-optimization",
                "migration-planning"
            ],
            "system_prompt": "prompts/database-architect.md",
            "tools": [
                "file_read", "file_write", "sql_validation"
            ],
            "constraints": {
                "max_file_changes": 15,
                "requires_review": True
            }
        }
    },

    # Development Agents (5)
    "senior-developer": {
        "agent": {
            "id": "senior-developer",
            "name": "Senior Developer",
            "version": "1.0.0",
            "category": "development",
            "description": """
                Full-stack generalist agent capable of handling complex
                development tasks across frontend, backend, and infrastructure.
            """,
            "triggers": {
                "keywords": [
                    "implement", "develop", "code", "build", "create",
                    "feature", "endpoint", "component", "function"
                ],
                "phases": ["DEVELOPMENT", "REFACTORING"],
                "complexity_range": {"min": 0.3, "max": 1.0}
            },
            "capabilities": [
                "full-stack-development",
                "api-design",
                "database-design",
                "testing",
                "code-review",
                "debugging",
                "refactoring"
            ],
            "system_prompt": "prompts/senior-developer.md",
            "tools": [
                "file_read",
                "file_write",
                "bash_execute",
                "git_operations",
                "search_codebase",
                "run_tests"
            ],
            "execution_targets": {
                "default": "cpu",
                "fallback": ["gpu"]
            },
            "constraints": {
                "max_file_changes": 20,
                "max_lines_per_file": 500,
                "requires_review": True,
                "timeout_seconds": 600
            },
            "metadata": {
                "author": "GAIA Team",
                "created": "2026-03-23",
                "tags": ["development", "full-stack", "core"]
            }
        }
    },

    "frontend-specialist": {
        "agent": {
            "id": "frontend-specialist",
            "name": "Frontend Specialist",
            "version": "1.0.0",
            "category": "development",
            "description": """
                Frontend development specialist for React, Vue, Angular,
                and modern web technologies.
            """,
            "triggers": {
                "keywords": [
                    "react", "vue", "angular", "frontend", "ui",
                    "component", "jsx", "typescript", "css", "html"
                ],
                "phases": ["DEVELOPMENT"],
                "complexity_range": {"min": 0.2, "max": 1.0}
            },
            "capabilities": [
                "react-development",
                "vue-development",
                "angular-development",
                "typescript",
                "css-styling",
                "responsive-design"
            ],
            "system_prompt": "prompts/frontend-specialist.md",
            "tools": [
                "file_read", "file_write", "npm_install", "run_tests"
            ],
            "constraints": {
                "max_file_changes": 25,
                "requires_review": True
            }
        }
    },

    "backend-specialist": {
        "agent": {
            "id": "backend-specialist",
            "name": "Backend Specialist",
            "version": "1.0.0",
            "category": "development",
            "description": """
                Backend development specialist for APIs, services,
                and server-side logic.
            """,
            "triggers": {
                "keywords": [
                    "backend", "api", "service", "server", "endpoint",
                    "flask", "django", "fastapi", "express", "node"
                ],
                "phases": ["DEVELOPMENT"],
                "complexity_range": {"min": 0.3, "max": 1.0}
            },
            "capabilities": [
                "api-development",
                "service-architecture",
                "database-integration",
                "authentication",
                "caching"
            ],
            "system_prompt": "prompts/backend-specialist.md",
            "tools": [
                "file_read", "file_write", "bash_execute", "run_tests"
            ],
            "constraints": {
                "max_file_changes": 20,
                "requires_review": True
            }
        }
    },

    "devops-engineer": {
        "agent": {
            "id": "devops-engineer",
            "name": "DevOps Engineer",
            "version": "1.0.0",
            "category": "development",
            "description": """
                DevOps specialist for CI/CD, infrastructure as code,
                containerization, and deployment.
            """,
            "triggers": {
                "keywords": [
                    "deploy", "ci/cd", "docker", "kubernetes", "terraform",
                    "infrastructure", "pipeline", "container"
                ],
                "phases": ["DEVELOPMENT", "DEPLOYMENT"],
                "complexity_range": {"min": 0.4, "max": 1.0}
            },
            "capabilities": [
                "ci-cd-pipeline",
                "docker-containerization",
                "kubernetes-orchestration",
                "terraform-iac",
                "cloud-deployment"
            ],
            "system_prompt": "prompts/devops-engineer.md",
            "tools": [
                "bash_execute", "file_write", "docker_commands"
            ],
            "constraints": {
                "max_file_changes": 15,
                "requires_review": True
            }
        }
    },

    "data-engineer": {
        "agent": {
            "id": "data-engineer",
            "name": "Data Engineer",
            "version": "1.0.0",
            "category": "development",
            "description": """
                Data engineering specialist for ETL pipelines,
                data processing, and analytics infrastructure.
            """,
            "triggers": {
                "keywords": [
                    "etl", "pipeline", "data processing", "spark",
                    "analytics", "data warehouse", "streaming"
                ],
                "phases": ["DEVELOPMENT"],
                "complexity_range": {"min": 0.4, "max": 1.0}
            },
            "capabilities": [
                "etl-development",
                "data-pipeline",
                "spark-processing",
                "data-modeling"
            ],
            "system_prompt": "prompts/data-engineer.md",
            "tools": [
                "file_read", "file_write", "bash_execute"
            ],
            "constraints": {
                "max_file_changes": 15,
                "requires_review": True
            }
        }
    },

    # Review Agents (5)
    "quality-reviewer": {
        "agent": {
            "id": "quality-reviewer",
            "name": "Quality Reviewer",
            "version": "1.0.0",
            "category": "review",
            "description": """
                Code quality reviewer that performs comprehensive
                code reviews and identifies improvement areas.
            """,
            "triggers": {
                "keywords": [
                    "review", "quality", "code review", "audit",
                    "improve", "refactor", "best practices"
                ],
                "phases": ["QUALITY", "REVIEW"],
                "complexity_range": {"min": 0.0, "max": 1.0}
            },
            "capabilities": [
                "code-review",
                "quality-assessment",
                "best-practices-validation",
                "improvement-suggestions"
            ],
            "system_prompt": "prompts/quality-reviewer.md",
            "tools": [
                "file_read", "search_codebase", "run_linters"
            ],
            "constraints": {
                "max_file_changes": 0,
                "requires_review": False
            }
        }
    },

    "security-auditor": {
        "agent": {
            "id": "security-auditor",
            "name": "Security Auditor",
            "version": "1.0.0",
            "category": "review",
            "description": """
                Security specialist that identifies vulnerabilities,
                security risks, and compliance issues.
            """,
            "triggers": {
                "keywords": [
                    "security", "vulnerability", "audit", "penetration",
                    "owasp", "encryption", "authentication"
                ],
                "phases": ["QUALITY", "REVIEW"],
                "complexity_range": {"min": 0.3, "max": 1.0}
            },
            "capabilities": [
                "security-audit",
                "vulnerability-detection",
                "compliance-check",
                "threat-modeling"
            ],
            "system_prompt": "prompts/security-auditor.md",
            "tools": [
                "file_read", "security_scan", "dependency_check"
            ],
            "constraints": {
                "max_file_changes": 0,
                "requires_review": True
            }
        }
    },

    "performance-analyst": {
        "agent": {
            "id": "performance-analyst",
            "name": "Performance Analyst",
            "version": "1.0.0",
            "category": "review",
            "description": """
                Performance specialist that identifies bottlenecks,
                optimization opportunities, and scalability issues.
            """,
            "triggers": {
                "keywords": [
                    "performance", "optimize", "bottleneck", "slow",
                    "scalability", "profiling", "benchmark"
                ],
                "phases": ["QUALITY", "REVIEW", "REFACTORING"],
                "complexity_range": {"min": 0.4, "max": 1.0}
            },
            "capabilities": [
                "performance-analysis",
                "bottleneck-detection",
                "optimization",
                "benchmarking"
            ],
            "system_prompt": "prompts/performance-analyst.md",
            "tools": [
                "file_read", "profiling", "benchmark"
            ],
            "constraints": {
                "max_file_changes": 0,
                "requires_review": True
            }
        }
    },

    "accessibility-reviewer": {
        "agent": {
            "id": "accessibility-reviewer",
            "name": "Accessibility Reviewer",
            "version": "1.0.0",
            "category": "review",
            "description": """
                Accessibility specialist that ensures WCAG compliance
                and inclusive design practices.
            """,
            "triggers": {
                "keywords": [
                    "accessibility", "wcag", "a11y", "inclusive",
                    "aria", "screen reader", "keyboard navigation"
                ],
                "phases": ["QUALITY", "REVIEW"],
                "complexity_range": {"min": 0.0, "max": 1.0}
            },
            "capabilities": [
                "wcag-compliance",
                "accessibility-audit",
                "aria-validation",
                "inclusive-design"
            ],
            "system_prompt": "prompts/accessibility-reviewer.md",
            "tools": [
                "file_read", "accessibility_scan"
            ],
            "constraints": {
                "max_file_changes": 0,
                "requires_review": True
            }
        }
    },

    "test-coverage-analyzer": {
        "agent": {
            "id": "test-coverage-analyzer",
            "name": "Test Coverage Analyzer",
            "version": "1.0.0",
            "category": "review",
            "description": """
                Testing specialist that analyzes test coverage,
                identifies gaps, and suggests test improvements.
            """,
            "triggers": {
                "keywords": [
                    "test", "coverage", "unit test", "integration test",
                    "test gap", "mock", "assertion"
                ],
                "phases": ["QUALITY", "REVIEW"],
                "complexity_range": {"min": 0.0, "max": 1.0}
            },
            "capabilities": [
                "coverage-analysis",
                "test-quality-assessment",
                "gap-identification",
                "test-generation"
            ],
            "system_prompt": "prompts/test-coverage-analyzer.md",
            "tools": [
                "file_read", "run_tests", "coverage_report"
            ],
            "constraints": {
                "max_file_changes": 10,
                "requires_review": True
            }
        }
    },

    # Management Agents (3)
    "software-program-manager": {
        "agent": {
            "id": "software-program-manager",
            "name": "Software Program Manager",
            "version": "1.0.0",
            "category": "management",
            "description": """
                Project management specialist that coordinates tasks,
                tracks progress, and ensures delivery quality.
            """,
            "triggers": {
                "keywords": [
                    "manage", "coordinate", "track", "progress",
                    "milestone", "deadline", "status", "report"
                ],
                "phases": ["PLANNING", "DECISION", "MANAGEMENT"],
                "complexity_range": {"min": 0.0, "max": 1.0}
            },
            "capabilities": [
                "project-management",
                "task-coordination",
                "progress-tracking",
                "status-reporting"
            ],
            "system_prompt": "prompts/software-program-manager.md",
            "tools": [
                "file_read", "file_write", "chronicle_access"
            ],
            "constraints": {
                "max_file_changes": 5,
                "requires_review": False
            }
        }
    },

    "technical-writer": {
        "agent": {
            "id": "technical-writer",
            "name": "Technical Writer",
            "version": "1.0.0",
            "category": "management",
            "description": """
                Documentation specialist that creates and maintains
                technical documentation, guides, and API references.
            """,
            "triggers": {
                "keywords": [
                    "document", "write", "readme", "guide",
                    "api doc", "tutorial", "manual"
                ],
                "phases": ["DEVELOPMENT", "DOCUMENTATION"],
                "complexity_range": {"min": 0.0, "max": 1.0}
            },
            "capabilities": [
                "technical-writing",
                "api-documentation",
                "tutorial-creation",
                "documentation-review"
            ],
            "system_prompt": "prompts/technical-writer.md",
            "tools": [
                "file_read", "file_write", "markdown_format"
            ],
            "constraints": {
                "max_file_changes": 15,
                "requires_review": True
            }
        }
    },

    "release-manager": {
        "agent": {
            "id": "release-manager",
            "name": "Release Manager",
            "version": "1.0.0",
            "category": "management",
            "description": """
                Release management specialist that coordinates
                versioning, changelogs, and release processes.
            """,
            "triggers": {
                "keywords": [
                    "release", "version", "changelog", "tag",
                    "publish", "deploy", "rollout"
                ],
                "phases": ["DEPLOYMENT", "MANAGEMENT"],
                "complexity_range": {"min": 0.3, "max": 1.0}
            },
            "capabilities": [
                "release-management",
                "versioning",
                "changelog-generation",
                "deployment-coordination"
            ],
            "system_prompt": "prompts/release-manager.md",
            "tools": [
                "file_read", "file_write", "git_operations", "bash_execute"
            ],
            "constraints": {
                "max_file_changes": 10,
                "requires_review": True
            }
        }
    },
}


def get_agent_definition(agent_id: str) -> dict:
    """
    Get agent definition by ID.

    Args:
        agent_id: Agent identifier

    Returns:
        Agent definition dictionary or None
    """
    return AGENT_DEFINITIONS.get(agent_id)


def get_agents_by_category(category: str) -> List[Dict[str, Any]]:
    """
    Get all agents in a category.

    Args:
        category: Category name (planning, development, review, management)

    Returns:
        List of agent definitions
    """
    return [
        defn for defn in AGENT_DEFINITIONS.values()
        if defn.get("agent", {}).get("category") == category
    ]


def get_all_agent_ids() -> List[str]:
    """Get list of all agent IDs."""
    return list(AGENT_DEFINITIONS.keys())


def load_agent_definitions() -> Dict[str, Dict[str, Any]]:
    """
    Load all agent definitions.

    Returns:
        Dictionary of agent definitions
    """
    return AGENT_DEFINITIONS
