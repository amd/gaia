# GAIA Code Agent

An AI-powered code analysis, generation, and management agent for the GAIA framework.

> **Note**: This is a proof of concept implementation demonstrating autonomous AI-driven code generation capabilities within the GAIA framework.

## Overview

The GAIA Code Agent is an autonomous agent that attempts to handle coding tasks from requirements to implementation. It plans workflows, generates code with best practices in mind, integrates linting and formatting tools, and attempts to fix errors iteratively. The agent can create project architectures, scaffold folder structures, and assist with development workflows.

## Key Features

- **Autonomous Workflow Execution**: Multi-step planning and execution for complex tasks
- **Architectural Planning**: Creates detailed PLAN.md with project structure and task tracking before implementation
- **Progress Tracking**: PLAN.md includes checkbox task lists ([ ] and [x]) for tracking implementation progress
- **Context Synchronization**: PLAN.md is automatically re-read and included in context at each generation step
- **Project Scaffolding**: Generates complete project structures with appropriate folder hierarchy
- **Dynamic Planning**: Creates follow-up validation plans after project generation
- **Code Generation**: Functions, classes, and comprehensive unit tests with automatic file saving
- **Code Analysis**: AST-based Python parsing with symbol extraction and validation
- **Quality Assurance**: Integrated pylint and Black formatting with automatic error fixing
- **File Operations**: Read, write, search, edit, and diff operations with Git-style unified diff support
- **Markdown Support**: Read and write markdown files for documentation with streaming preview
- **Error Recovery**: Attempts iterative error correction for syntax, runtime, and linting issues
- **GAIA.md Integration**: Automatically reads project context and can initialize GAIA.md for existing projects
- **Execution Safety**: Built-in timeout prevents hanging on scripts waiting for input
- **Performance Monitoring**: Optional LLM statistics tracking (tokens, timing) via --show-stats flag

## Installation

```bash
# Install GAIA with development dependencies
# Linux/Windows:
pip install -e .[dev]

# macOS:
pip install -e ".[dev]"
```

## Prerequisites

**Important**: The Code Agent requires a larger context size (32,768 tokens) to handle complex code generation tasks. When using the local Lemonade server, start it with the `--ctx-size` parameter:

```bash
# Start Lemonade server with larger context size for Code Agent
lemonade-server serve --ctx-size 32768
```

**Note**: This requirement only applies when using the local Lemonade server. If you're using Claude or ChatGPT via `--use-claude` or `--use-chatgpt`, no special configuration is needed.

## Usage

### Basic Examples

```bash
# Initialize GAIA.md for existing project
gaia code /init

# Generate a function
gaia code "Create a function to calculate factorial"

# Generate unit tests for existing code
gaia code "Generate tests for my_module.py"

# Fix linting issues
gaia code "Fix linting issues in script.py"

# Create complete project with architecture
gaia code "Create a task management API with user authentication"

# Interactive mode
gaia code --interactive

# List available tools
gaia code --list-tools

# Use with Claude API (no server setup required)
gaia code "Create a REST API" --use-claude

# Use with ChatGPT API (no server setup required)
gaia code "Create a REST API" --use-chatgpt
```

### Debug and Trace Options

```bash
# Enable debug logging (see internal decision logs)
gaia code "Create a todo CLI app" --debug

# Show prompts sent to LLM in console
gaia code "Create a todo CLI app" --show-prompts

# Save detailed JSON trace of agent execution
gaia code "Create a todo CLI app" --trace

# Silent mode: JSON output only (for scripts/automation)
gaia code "Create a todo CLI app" --silent

# Enable step-through debugging mode (pause at each agent step)
gaia code "Create a todo CLI app" --step-through

# Control maximum steps (default: 100)
gaia code "Create a todo CLI app" --max-steps 150

# Show LLM performance statistics (tokens, timing)
gaia code "Create a todo CLI app" --show-stats

# Maximum debug: console + trace with full logs
gaia code "Create a todo CLI app" --debug --show-prompts --trace
```

**Debug Flags:**
- `--debug`: Enable DEBUG level logging with internal decision traces
- `--show-prompts`: Display every prompt sent to the LLM in real-time
- `--trace`: Save detailed JSON trace of agent execution
- `--silent`: Suppress console output, return JSON only
- `--step-through`: Enable step-through debugging mode (pause at each agent step)
- `--max-steps <n>`: Override default maximum steps (default: 100)
- `--stats`, `--show-stats`: Display LLM performance statistics after each response

**JSON Output Structure:**
The `--trace` flag saves a complete trace with:
```json
{
  "status": "success",
  "result": "Final answer from agent",
  "system_prompt": "Complete system prompt used",
  "conversation": [
    {"role": "user", "content": "User's query"},
    {"role": "assistant", "content": {"thought": "...", "tool": "...", "tool_args": {...}}},
    {"role": "system", "content": {...}},  // Tool results
    ...
  ],
  "steps_taken": 28,
  "duration": 123.45,
  "total_input_tokens": 15000,
  "total_output_tokens": 8000,
  "output_file": "/absolute/path/to/output.json"
}
```

This trace can be analyzed to understand the agent's decision-making process, debug issues, or optimize prompts.

### Advanced Application Examples

The Code Agent can attempt to generate project scaffolding and basic implementations for applications:

```bash
# Example: HRMS (Human Resource Management System) project structure
gaia code "Build an HRMS SaaS app with role-based access (HR, employee), employee directory, and leave requests with approval flow."

# This may generate:
# - Basic project structure and folder organization
# - Starter code for authentication and role management
# - Skeleton code for employee directory operations
# - Basic leave request workflow structure
# - API endpoint scaffolding
# - Initial database model definitions
# - Basic unit test templates
# - Configuration file templates
```

**Note**: Generated code serves as a starting point and will require review, testing, and refinement for production use. Complex features like payment processing, real-time communications, and security-critical components should be implemented with careful review and testing.

## Testing

```bash
# Run all Code Agent tests
python -m pytest tests/test_code_agent.py -v

# Test specific features
python -m pytest tests/test_code_agent.py::TestCodeAgent::test_generate_function -xvs
python -m pytest tests/test_code_agent.py::TestCodeAgent::test_workflow_with_validation_and_linting -xvs
```

## Python API

```python
from gaia.agents.code.agent import CodeAgent

# Initialize the agent
agent = CodeAgent()

# Process a query
result = agent.process_query("Generate a function to sort a list")
```

## Using Code Agent via API Server

The Code Agent is available through the GAIA API Server as the `gaia-code` model, providing an OpenAI-compatible REST API for integration with VSCode, IDEs, and other tools.

**Want to use GAIA Code in VSCode?** See the [VSCode Integration Guide](./vscode.md) for complete setup instructions.

### Quick Start

```bash
# 1. Start Lemonade server with extended context (required for Code agent)
lemonade-server serve --ctx-size 32768

# 2. Install dependencies
pip install -e .[api]

# 3. Start GAIA API server
gaia api start

# 4. Use with any OpenAI-compatible client
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gaia-code",
    "messages": [{"role": "user", "content": "Create a fibonacci function"}]
  }'
```

### Key Features via API

- **OpenAI Compatible**: Works with OpenAI Python client and other compatible tools
- **Workspace Awareness**: Automatically detects workspace root from VSCode/IDEs
- **Streaming Support**: Real-time progress updates during code generation
- **Multi-Turn Conversations**: Maintains context across multiple requests

### For More Information

- **VSCode Integration**: See [VSCode Integration Guide](./vscode.md) for complete setup
- **API Examples & Usage**: See [API Server Documentation](./api.md)
- **Troubleshooting**: See [VSCode Troubleshooting](./vscode.md#troubleshooting) or [API Troubleshooting](./api.md#troubleshooting)

## Workflow Capabilities

The Code Agent attempts to execute workflows with the following steps:

1. **Analyze Requirements**: Parse and interpret the task description
2. **Create Architectural Plan**: Generate PLAN.md with proposed project structure and checkbox task tracking
3. **Build Project Structure**: Create folder hierarchy based on plan
4. **Generate Code**: Create functions, classes, or program scaffolding with PLAN.md context included
5. **Validate Syntax**: Check for syntax errors
6. **Lint and Format**: Apply pylint and Black formatting automatically
7. **Fix Errors**: Attempt to correct identified linting and runtime issues
8. **Generate Tests**: Create comprehensive unit test templates
9. **Execute and Verify**: Run the code and tests, reporting results
10. **Progress Updates**: Optionally update PLAN.md task checkboxes as work progresses

**Key Improvements**:
- PLAN.md includes `[ ]` checkbox tasks for tracking progress
- PLAN.md is re-read before each file generation to maintain context synchronization
- Black formatting is automatically applied to all Python files
- Streaming preview for markdown file generation

**Note**: Success depends on task complexity and LLM capabilities. Review and manual refinement are typically required.

## File Management

- **Generated Functions**: Saved as `[function_name]_generated.py`
- **Generated Classes**: Saved as `[class_name]_generated.py`
- **Generated Tests**: Saved as `test_[module_name].py`
- **Project Plans**: Saved as `PLAN.md` in project root
- **Project Context**: Saved as `GAIA.md` for agent guidance
- **Temporary Files**: Stored in `~/.gaia/cache/`

## Available Tools

The Code Agent provides over 30 comprehensive tools for code operations:

### Core Code Operations
- `list_files`: List files and directories in specified path
- `parse_python_code`: Parse Python code and extract structure using AST
- `validate_syntax`: Validate Python code syntax
- `list_symbols`: List symbols (functions, classes, variables) in Python code

### Code Generation
- `generate_function`: Generate Python functions with docstrings and type hints
- `generate_class`: Generate Python classes with proper structure
- `generate_test`: Generate comprehensive unit tests for modules

### Quality Assurance
- `analyze_with_pylint`: Run pylint analysis on code
- `format_with_black`: Apply Black formatting to code
- `lint_and_format`: Combined linting and formatting
- `validate_project`: Comprehensive project validation for all file types

### Project Management
- `create_project`: Create complete Python projects from requirements
- `create_architectural_plan`: Create detailed PLAN.md with project architecture
- `create_project_structure`: Generate project folder structure from plan
- `implement_from_plan`: Implement components based on architectural plan

### Error Fixing
- `auto_fix_syntax_errors`: Attempt to detect and fix syntax errors
- `fix_code`: Attempt to fix Python code using LLM-driven analysis
- `fix_linting_errors`: Attempt to fix specific linting issues
- `fix_python_errors`: Attempt to fix Python runtime errors based on error messages

### File Operations (from FileIOToolsMixin)
- `read_python_file`: Read and analyze Python files
- `write_python_file`: Write Python code to files with validation
- `edit_python_file`: Edit Python files with content replacement
- `search_code`: Search for patterns in code files
- `replace_function`: Replace specific functions in Python files

### Diff and Markdown Operations
- `generate_diff`: Generate unified diff for files
- `read_markdown_file`: Read and parse markdown files
- `write_markdown_file`: Write content to markdown files

### Workflow and GAIA Integration
- `create_workflow_plan`: Create comprehensive workflow plans for complex queries
- `init_gaia_md`: Initialize GAIA.md by analyzing current codebase
- `update_gaia_md`: Create or update GAIA.md file for project context

## Architecture

- **Tool-Based System**: All operations exposed as tools for LLM interaction
- **State Management**: PLANNING → EXECUTING_PLAN → COMPLETION with error recovery
- **Console Interface**: Interactive sessions with AgentConsole
- **Error Handling**: Comprehensive error recovery with retry mechanisms
- **File IO Mixin**: Modular file operations through FileIOToolsMixin
- **Cache Management**: Temporary file storage in ~/.gaia/cache/
- **Context Awareness**: Reads GAIA.md for project-specific guidance

## Debugging

### VS Code Debugging (Developers)

For code-level debugging with breakpoints, use the VS Code debugger with pre-configured launch configurations:

**Available debug configurations:**
1. **Code Agent Debug - Todo App** - Full project generation workflow
2. **Code Agent Debug - Function Generation** - Simple code generation
3. **Code Agent Debug - REST API** - Complex project generation
4. **Code Agent Debug - Test Generation** - Test file generation
5. **Code Agent Debug - Interactive** - Interactive mode debugging
6. **Code Agent Debug - With Breakpoint** ⭐ - Stops before execution for setting breakpoints

**How to use:**
1. Open VS Code Run and Debug panel (`Ctrl+Shift+D`)
2. Select a debug configuration
3. Set breakpoints in code files
4. Press `F5` to start debugging

**Useful breakpoint locations:**
- `src/gaia/agents/code/agent.py` - Agent initialization and processing
- `src/gaia/agents/code/tools/code_tools.py` - Code generation tools
- `src/gaia/agents/code/tools/validation_parsing.py` - Validation and parsing
- `src/gaia/agents/base/agent.py` - Base agent logic

For comprehensive debugging guide, see [CODE_AGENT_VSCODE_DEBUG_GUIDE.md](../CODE_AGENT_VSCODE_DEBUG_GUIDE.md)

## Troubleshooting

### Server Not Running Error

If you see:
```
❌ Error: Lemonade server is not running or not accessible.
```

Start the Lemonade server with the required context size:
```bash
lemonade-server serve --ctx-size 32768
```

### Context Size Issues

If you experience incomplete code generation or truncated responses:
- Verify the server was started with `--ctx-size 32768`
- Check server logs for context size information
- Alternatively, use `--use-claude` or `--use-chatgpt` for cloud-based LLMs

### Other Common Issues

- **LLM timeout errors**: Ensure Lemonade server is running with `--ctx-size 32768`
- **Import errors**: Install with `pip install -e .[dev]` (Linux/Windows) or `pip install -e ".[dev]"` (macOS)
- **File not found**: Check working directory and file paths
- **Incomplete code generation**: Restart Lemonade server with larger context size
- **Pylint not found**: Ensure pylint is installed (`pip install pylint`)
- **Black not found**: Ensure black is installed (`pip install black`)