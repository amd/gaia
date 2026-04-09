---
template_id: build-commands
template_type: commands
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Build command patterns for Python, TypeScript, and multi-language projects
schema_version: "1.0"
---

# Build Command Patterns

## Purpose

This template provides reusable build command patterns for compiling, packaging, and preparing software projects for distribution.

## Python Builds

### Build Wheel Distribution

```bash
# Pattern: Build Python wheel
command_template: |
  python -m build --wheel --outdir {{DIST_DIR}} {{SOURCE_DIR}}
example: |
  python -m build --wheel --outdir dist/
variables:
  - DIST_DIR: Output directory for built packages (default: dist/)
  - SOURCE_DIR: Source directory containing pyproject.toml
```

### Build Source Distribution

```bash
# Pattern: Build Python source distribution
command_template: |
  python -m build --sdist --outdir {{DIST_DIR}} {{SOURCE_DIR}}
example: |
  python -m build --sdist
variables:
  - DIST_DIR: Output directory for built packages
  - SOURCE_DIR: Source directory
```

### Build Both Wheel and Sdist

```bash
# Pattern: Build all distributions
command_template: |
  python -m build --outdir {{DIST_DIR}}
example: |
  python -m build
variables:
  - DIST_DIR: Output directory
```

### Install from Local Build

```bash
# Pattern: Install package in development mode
command_template: |
  pip install -e {{PACKAGE_PATH}} {{EXTRAS}}
example: |
  pip install -e ".[dev]"
variables:
  - PACKAGE_PATH: Path to package (use "." for current directory)
  - EXTRAS: Optional extras like [dev], [test], [ui]
```

### Install Build Dependencies

```bash
# Pattern: Install build requirements
command_template: |
  pip install build setuptools wheel {{EXTRA_PACKAGES}}
example: |
  pip install build setuptools wheel
variables:
  - EXTRA_PACKAGES: Additional build dependencies
```

### Run Setup.py Commands

```bash
# Pattern: Legacy setup.py command
command_template: |
  python setup.py {{COMMAND}} {{FLAGS}}
example: |
  python setup.py sdist bdist_wheel
warning: "Deprecated - prefer python -m build"
variables:
  - COMMAND: sdist, bdist_wheel, install, etc.
  - FLAGS: Command-specific flags
```

## TypeScript Builds

### Compile TypeScript

```bash
# Pattern: Compile TypeScript to JavaScript
command_template: |
  npx tsc {{FLAGS}}
example: |
  npx tsc --project tsconfig.json
variables:
  - FLAGS: --project, --outDir, --watch, --build
```

### Build with Watch Mode

```bash
# Pattern: TypeScript watch mode
command_template: |
  npx tsc --watch {{CONFIG_PATH}}
example: |
  npx tsc --watch
variables:
  - CONFIG_PATH: Path to tsconfig.json
```

### Build Vite Project

```bash
# Pattern: Build Vite project for production
command_template: |
  npm run build {{FLAGS}}
example: |
  npm run build
variables:
  - FLAGS: --mode production, --mode development
```

### Build React App

```bash
# Pattern: Create React App build
command_template: |
  npm run build
example: |
  npm run build
variables:
  - None: Uses default configuration
```

## Multi-language Builds

### Build with Make

```bash
# Pattern: Run Makefile targets
command_template: |
  make {{TARGET}} {{VARIABLES}}
example: |
  make all
variables:
  - TARGET: Makefile target (all, clean, build, test)
  - VARIABLES: VAR=value pairs
```

### Build with CMake

```bash
# Pattern: CMake configure and build
command_template: |
  cmake -S {{SOURCE_DIR}} -B {{BUILD_DIR}} {{OPTIONS}}
  cmake --build {{BUILD_DIR}}
example: |
  cmake -S . -B build
  cmake --build build
variables:
  - SOURCE_DIR: Source directory (default: .)
  - BUILD_DIR: Build directory (default: build)
  - OPTIONS: -DCMAKE_BUILD_TYPE=Release, etc.
```

### Build with Maven

```bash
# Pattern: Maven build
command_template: |
  mvn {{GOALS}} {{FLAGS}}
example: |
  mvn clean package
variables:
  - GOALS: clean, compile, package, install, etc.
  - FLAGS: -DskipTests, -P profile, -pl modules
```

### Build with Gradle

```bash
# Pattern: Gradle build
command_template: |
  ./gradlew {{TASKS}} {{FLAGS}}
example: |
  ./gradlew build
variables:
  - TASKS: build, clean, test, assemble, etc.
  - FLAGS: --offline, --refresh-dependencies
```

## UV-Specific Builds (GAIA)

### Create Virtual Environment

```bash
# Pattern: Create UV virtual environment
command_template: |
  uv venv {{VENV_PATH}} {{FLAGS}}
example: |
  uv venv
variables:
  - VENV_PATH: Virtual environment path (default: .venv)
  - FLAGS: --python 3.11, --clear
```

### Install with UV

```bash
# Pattern: Install dependencies with UV
command_template: |
  uv pip install {{FLAGS}} {{PACKAGES}}
example: |
  uv pip install -e ".[dev]"
variables:
  - PACKAGES: Package names or -r requirements.txt
  - FLAGS: -e (editable), -r (requirements file)
```

### Sync Dependencies

```bash
# Pattern: Sync environment with lock file
command_template: |
  uv pip sync {{LOCK_FILE}} {{FLAGS}}
example: |
  uv pip sync requirements.txt
variables:
  - LOCK_FILE: Requirements or lock file
  - FLAGS: --dry-run, --python
```

## Clean Operations

### Clean Python Artifacts

```bash
# Pattern: Remove Python build artifacts
command_template: |
  rm -rf {{PATHS}}
example: |
  rm -rf build/ dist/ *.egg-info __pycache__/
variables:
  - PATHS: Directories to remove
```

### Clean Node Modules

```bash
# Pattern: Remove and reinstall node_modules
command_template: |
  rm -rf node_modules/ package-lock.json
  npm install
example: |
  rm -rf node_modules/ package-lock.json && npm install
```

## Verification Commands

### Verify Python Package

```bash
# Pattern: Check package structure
command_template: |
  twine check {{DIST_PATH}}/*
example: |
  twine check dist/*
variables:
  - DIST_PATH: Path to dist/ directory
```

### List Package Contents

```bash
# Pattern: Show wheel contents
command_template: |
  unzip -l {{WHEEL_FILE}}
example: |
  unzip -l dist/my_package-1.0.0-py3-none-any.whl
variables:
  - WHEEL_FILE: Path to .whl file
```

## Related Components

- [[component-framework/commands/shell-commands.md]] - For general shell operations
- [[component-framework/commands/test-commands.md]] - For test execution
- [[component-framework/commands/git-commands.md]] - For version control
