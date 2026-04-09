---
template_id: test-commands
template_type: commands
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Test command patterns for unit, integration, and coverage testing
schema_version: "1.0"
---

# Test Command Patterns

## Purpose

This template provides reusable test execution command patterns for running unit tests, integration tests, and generating coverage reports.

## Unit Tests

### Run All Unit Tests

```bash
# Pattern: Run unit test suite
command_template: |
  python -m pytest tests/unit/ {{FLAGS}}
example: |
  python -m pytest tests/unit/ -v --tb=short
variables:
  - FLAGS: -v (verbose), --tb=short (traceback), -x (stop on first failure)
```

### Run Specific Test File

```bash
# Pattern: Run single test file
command_template: |
  python -m pytest {{TEST_FILE}} {{FLAGS}}
example: |
  python -m pytest tests/unit/utils/test_component_loader.py -v
variables:
  - TEST_FILE: Path to test file
  - FLAGS: -v, -k PATTERN (keyword filter)
```

### Run Specific Test Function

```bash
# Pattern: Run single test function
command_template: |
  python -m pytest {{TEST_FILE}}::{{TEST_FUNCTION}} {{FLAGS}}
example: |
  python -m pytest tests/unit/utils/test_component_loader.py::TestComponentLoader::test_load_component_success -v
variables:
  - TEST_FILE: Path to test file
  - TEST_FUNCTION: Class and method name
  - FLAGS: -v, -s (capture output)
```

### Run Tests by Keyword

```bash
# Pattern: Run tests matching keyword
command_template: |
  python -m pytest {{TEST_PATH}} -k "{{PATTERN}}" {{FLAGS}}
example: |
  python -m pytest tests/unit/ -k "component_loader" -v
variables:
  - TEST_PATH: Directory or file to search
  - PATTERN: Keyword pattern (supports and/or/not)
  - FLAGS: -v, --collect-only (show what would run)
```

### Run Tests by Marker

```bash
# Pattern: Run tests with specific marker
command_template: |
  python -m pytest {{TEST_PATH}} -m "{{MARKER}}" {{FLAGS}}
example: |
  python -m pytest tests/ -m "slow" -v
variables:
  - TEST_PATH: Test directory or file
  - MARKER: Marker name (slow, integration, smoke)
  - FLAGS: -v, --markers (list available markers)
```

### Run Tests in Parallel

```bash
# Pattern: Run tests with xdist parallelization
command_template: |
  python -m pytest {{TEST_PATH}} -n {{WORKERS}} {{FLAGS}}
example: |
  python -m pytest tests/unit/ -n auto
variables:
  - TEST_PATH: Test directory
  - WORKERS: Number of workers (auto, 4, 8)
  - FLAGS: --dist=loadscope (group by module)
```

## Integration Tests

### Run All Integration Tests

```bash
# Pattern: Run integration test suite
command_template: |
  python -m pytest tests/integration/ {{FLAGS}}
example: |
  python -m pytest tests/integration/ -v --tb=long
variables:
  - FLAGS: -v, --tb=long (full traceback), -s (no output capture)
```

### Run Integration Tests with Fixtures

```bash
# Pattern: Run integration tests requiring fixtures
command_template: |
  python -m pytest tests/integration/ --fixture-args {{ARGS}} {{FLAGS}}
example: |
  python -m pytest tests/integration/ --require-lemonade -v
variables:
  - ARGS: Fixture-specific arguments
  - FLAGS: -v, -s
```

### Run End-to-End Tests

```bash
# Pattern: Run E2E test suite
command_template: |
  python -m pytest tests/e2e/ {{FLAGS}}
example: |
  python -m pytest tests/e2e/ -v -s
variables:
  - FLAGS: -v, -s, --headed (for browser tests)
```

## Coverage Reports

### Generate Coverage Report

```bash
# Pattern: Run tests with coverage
command_template: |
  python -m pytest {{TEST_PATH}} --cov={{SOURCE_DIR}} --cov-report={{REPORT_TYPE}} {{FLAGS}}
example: |
  python -m pytest tests/unit/ --cov=src/gaia --cov-report=html --cov-report=term
variables:
  - TEST_PATH: Test directory
  - SOURCE_DIR: Source directory to measure
  - REPORT_TYPE: term (terminal), html, xml, json
  - FLAGS: --cov-fail-under=80
```

### Generate HTML Coverage Report

```bash
# Pattern: Generate HTML coverage report
command_template: |
  python -m pytest --cov={{SOURCE_DIR}} --cov-report=html:{{HTML_DIR}} {{TEST_PATH}}
example: |
  python -m pytest --cov=src/gaia --cov-report=html:htmlcov/ tests/unit/
variables:
  - SOURCE_DIR: Source directory
  - HTML_DIR: Output directory for HTML report
  - TEST_PATH: Tests to run
```

### Generate XML Coverage Report (CI/CD)

```bash
# Pattern: Generate XML coverage for CI
command_template: |
  python -m pytest --cov={{SOURCE_DIR}} --cov-report=xml:{{XML_FILE}} {{TEST_PATH}}
example: |
  python -m pytest --cov=src/gaia --cov-report=xml:coverage.xml tests/
variables:
  - SOURCE_DIR: Source directory
  - XML_FILE: Output XML file path
  - TEST_PATH: Tests to run
```

### View Coverage Summary

```bash
# Pattern: Show coverage summary
command_template: |
  coverage report {{FLAGS}}
example: |
  coverage report -m
variables:
  - FLAGS: -m (show missing lines), --fail-under=80
```

### View HTML Coverage

```bash
# Pattern: Open HTML coverage report
command_template: |
  coverage html -d {{HTML_DIR}}
example: |
  coverage html -d htmlcov
variables:
  - HTML_DIR: Output directory
notes: "Open htmlcov/index.html in browser to view"
```

## Test Configuration

### Run with Custom Config

```bash
# Pattern: Run pytest with custom config file
command_template: |
  python -m pytest {{TEST_PATH}} -c {{CONFIG_FILE}} {{FLAGS}}
example: |
  python -m pytest tests/ -c pyproject.toml
variables:
  - TEST_PATH: Test directory
  - CONFIG_FILE: pytest.ini, pyproject.toml, setup.cfg
  - FLAGS: -v
```

### Show Test Collection

```bash
# Pattern: Show what tests would run
command_template: |
  python -m pytest {{TEST_PATH}} --collect-only {{FLAGS}}
example: |
  python -m pytest tests/unit/ --collect-only
variables:
  - TEST_PATH: Test directory
  - FLAGS: -q (quiet), --tree (hierarchical view)
```

## Linting as Tests

### Run Type Checking (MyPy)

```bash
# Pattern: Run mypy type checker
command_template: |
  python -m mypy {{SOURCE_PATH}} {{FLAGS}}
example: |
  python -m mypy src/gaia/
variables:
  - SOURCE_PATH: Source directory or file
  - FLAGS: --strict, --ignore-missing-imports
```

### Run Linter (Ruff/Flake8)

```bash
# Pattern: Run code linter
command_template: |
  python -m ruff check {{SOURCE_PATH}} {{FLAGS}}
example: |
  python -m ruff check src/gaia/
variables:
  - SOURCE_PATH: Source directory
  - FLAGS: --fix, --select E,W,F
```

### Run Format Check (Black)

```bash
# Pattern: Check code formatting
command_template: |
  python -m black {{SOURCE_PATH}} --check {{FLAGS}}
example: |
  python -m black src/gaia/ --check
variables:
  - SOURCE_PATH: Source directory
  - FLAGS: --diff, --line-length 88
```

## CI/CD Test Commands

### Run All Quality Checks

```bash
# Pattern: Run complete quality suite
command_template: |
  python util/lint.py --all {{FLAGS}}
example: |
  python util/lint.py --all
variables:
  - FLAGS: --fix, --ci
```

### Run Tests with Coverage Threshold

```bash
# Pattern: Run tests with minimum coverage
command_template: |
  python -m pytest {{TEST_PATH}} --cov={{SOURCE_DIR}} --cov-fail-under={{MIN_COVERAGE}}
example: |
  python -m pytest tests/ --cov=src/gaia --cov-fail-under=80
variables:
  - TEST_PATH: Test directory
  - SOURCE_DIR: Source directory
  - MIN_COVERAGE: Minimum coverage percentage
```

## GAIA-Specific Test Commands

### Run GAIA Unit Tests

```bash
# Pattern: Run GAIA unit test suite
command_template: |
  python -m pytest tests/unit/ -v --tb=short
example: |
  python -m pytest tests/unit/ -v --tb=short
```

### Run GAIA Integration Tests (Hybrid)

```bash
# Pattern: Run GAIA hybrid integration tests
command_template: |
  python -m pytest tests/ --hybrid {{FLAGS}}
example: |
  python -m pytest tests/ --hybrid -v
variables:
  - FLAGS: -v, -x
notes: "Runs cloud + local tests based on environment"
```

## Related Components

- [[component-framework/commands/build-commands.md]] - For build operations
- [[component-framework/commands/shell-commands.md]] - For shell execution
- [[component-framework/checklists/code-review-checklist.md]] - For code validation
