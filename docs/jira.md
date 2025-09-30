# GAIA Jira Agent Documentation

## Overview

The GAIA Jira Agent provides a natural language interface for interacting with Atlassian Jira. The agent communicates directly with the Atlassian REST API - no intermediary services or MCP bridge required. It automatically discovers your Jira instance configuration and allows you to search, create, and update issues using plain English commands.

> **Desktop WebUI**: GAIA includes a JIRA WebUI as an Electron desktop application. See the [WebUI Configuration](#webui-configuration) section below for setup instructions.

## Quick Start

### Prerequisites

1. **Setup GAIA Development Environment**:
   Follow the [GAIA Development Guide](./dev.md) to set up GAIA:

   - Clone the repository
   - Create conda environment
   - Install GAIA: `pip install -e .` (or `pip install -e .[dev]` for development)

   The base installation includes all dependencies needed for the Jira agent.

2. **Download Required Model**:
   The Jira agent uses the `Qwen3-Coder-30B-A3B-Instruct-GGUF` model for reliable JSON parsing and JQL generation.

   Use the Lemonade server's model manager to download it:
   1. Start Lemonade server: `lemonade-server serve`
   2. Open the model manager in your browser (typically http://localhost:8000)
   3. Search for and download: `Qwen3-Coder-30B-A3B-Instruct-GGUF`

   Note: The model is over 17GB and can take a while to download depending on your internet connection. Due to its size, it should be run on a Strix Halo device or similar high-performance hardware with sufficient memory. It provides the best results for complex Jira queries and will be automatically selected when you run Jira commands.

3. **Set Jira Credentials**:

   ```bash
   # Required - Your Jira credentials
   export ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
   export ATLASSIAN_API_KEY=your-api-token
   export ATLASSIAN_USER_EMAIL=your-email@example.com
   ```

   Or create a `.env` file in the project root (see `.env.example` for template)

### Verify Installation

```bash
# Check that GAIA is installed correctly
gaia --version

# Test Jira credentials (will auto-discover your instance)
gaia jira "show all projects"
```

### Basic Usage

```bash
# Interactive mode - best for exploration
gaia jira --interactive

# Direct query - execute a single command
gaia jira "show my open issues"

# Search for issues
gaia jira "find critical bugs from last week"

# Create new issues
gaia jira "create a task: Update documentation"

# Update existing issues
gaia jira "set MDP-6 priority to high"
```

## Important Note

The Jira agent was developed and tested using a dummy Jira project. Different Jira projects may have varying configurations, custom fields, workflows, and permissions that could affect the agent's behavior. You may encounter errors or unexpected results depending on your specific Jira instance setup. The agent's auto-discovery feature helps adapt to different configurations, but some manual adjustments may be needed for complex or highly customized Jira environments.

## Architecture Overview

### Key Components

1. **JiraAgent** (`src/gaia/agents/jira/agent.py`)

   - Core agent that processes natural language queries
   - Automatically discovers Jira instance configuration (projects, issue types, statuses, priorities)
   - Uses LLM to translate natural language to JQL and execute operations
   - Registers three main tools: `jira_search`, `jira_create`, `jira_update`

2. **JiraApp** (`src/gaia/apps/jira/app.py`)

   - Application wrapper for the JiraAgent
   - Provides high-level methods for common operations
   - Handles interactive mode
   - Formats output for user display

3. **GAIA Jira CLI** (`gaia jira` command)

   - Easy command-line interface for Jira operations
   - Supports both direct queries and interactive mode
   - Automatically manages agent lifecycle
   - No coding required - just natural language commands

### How It Works

```mermaid
graph LR
    User[User Query] --> Agent[JiraAgent]
    Agent --> Discover[Auto-Discovery]
    Discover --> Config[Jira Config]
    Config --> LLM[LLM Processing]
    LLM --> Tools[Tool Execution]
    Tools --> API[Jira REST API]
    API --> Result[Formatted Result]
```

1. **Automatic Discovery**: On first use, the agent discovers your Jira instance configuration
2. **Dynamic Prompting**: Uses discovered config to teach the LLM about your specific Jira setup
3. **Natural Language Processing**: LLM converts queries to structured tool calls
4. **Tool Execution**: Executes appropriate Jira operations via REST API
5. **Result Formatting**: Returns user-friendly responses

## Usage Examples

### Natural Language Commands

#### Search Operations

```bash
# Find your assigned issues
gaia jira "show my issues"
gaia jira "what am I working on"

# Search by priority and type
gaia jira "find high priority ideas"
gaia jira "show medium priority ideas from this week"

# Search by project
gaia jira "issues in project MDP"
gaia jira "what's happening in MDP"

# Time-based searches
gaia jira "issues created today"
gaia jira "bugs fixed last week"
gaia jira "what changed yesterday"

# Sprint queries
gaia jira "current sprint ideas"
gaia jira "unfinished ideas in active sprint"
```

#### Creating Issues

```bash
# Simple idea creation
gaia jira "create idea: Explore VR travel features"

# Create with details
gaia jira "create an idea: Implement AI chatbot, high priority"
gaia jira "new idea: Add user analytics dashboard"

# Specify project
gaia jira "create idea in MDP: Refactor user profile data"
```

#### Updating Issues

```bash
# Change priority
gaia jira "update MDP-5 set priority to High"

# Change status
gaia jira "move MDP-6 to Discovery"
gaia jira "move MDP-5 to Parking lot"

# Update multiple fields
gaia jira "update JKL-012 priority High and assign to me"
```

### Interactive Mode

```bash
gaia jira --interactive

# You'll see:
# 🚀 GAIA Jira App - Interactive Mode
# Type 'help' for commands, 'exit' to quit
#
# jira> show my open issues
# [Agent processes your request...]
# 🎫 Found 2 issues
# • MDP-6 - Explore VR travel features
#   Status: Parking lot | Priority: Medium | Assignee: You
# • MDP-5 - Refactor user profile data
#   Status: Parking lot | Priority: Medium | Assignee: You
# ...
```

## Integration Methods

GAIA provides two methods for third-party applications to integrate with the Jira agent:

### 1. Python API (Direct Integration)

Use the JiraAgent directly in your Python applications:

```python
from gaia.agents.jira.agent import JiraAgent

# Initialize the agent
agent = JiraAgent(
    model_id="Qwen3-Coder-30B-A3B-Instruct-GGUF",
    silent_mode=True  # Suppress console output for API usage
)

# Discover Jira configuration (do this once)
config = agent.initialize()
print(f"Connected to Jira with {len(config['projects'])} projects")

# Execute natural language queries
result = agent.process_query("show my high priority ideas in MDP")

if result['status'] == 'success':
    print(f"Result: {result['final_answer']}")
    print(f"Steps taken: {result['steps_taken']}")

    # Access detailed conversation history if needed
    for msg in result.get('conversation', []):
        if msg['role'] == 'system' and 'issues' in msg.get('content', {}):
            issues = msg['content']['issues']
            for issue in issues:
                print(f"  - {issue['key']}: {issue['summary']}")

# Create a new issue
result = agent.process_query("create an idea: Implement AI chatbot with medium priority")

# Update an existing issue
result = agent.process_query("update MDP-5 set priority to High")

# Complex queries
result = agent.process_query(
    "find all ideas in Parking lot status and show their priorities"
)
```

### 2. MCP Server (HTTP/JSON-RPC Integration)

For non-Python applications, use the MCP bridge to access the Jira agent via HTTP:

```bash
# Start the MCP bridge (if needed for external integrations)
gaia mcp start --port 8765
```

Then integrate from any language:

**JavaScript/Node.js Example:**
```javascript
async function queryJira(query) {
    const response = await fetch('http://localhost:8765/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jsonrpc: '2.0',
            id: '1',
            method: 'tools/call',
            params: {
                name: 'gaia.jira',
                arguments: {
                    query: query,
                    operation: 'query'
                }
            }
        })
    });

    const result = await response.json();
    if (result.result) {
        const content = JSON.parse(result.result.content[0].text);
        return content;
    }
    throw new Error(result.error || 'Unknown error');
}

// Usage examples
const issues = await queryJira('show my open ideas');
const newIssue = await queryJira('create idea: Add VR support');
const update = await queryJira('update MDP-6 to high priority');
```

**Python with HTTP (alternative to direct API):**
```python
import requests

def query_jira_via_mcp(query):
    response = requests.post('http://localhost:8765/', json={
        'jsonrpc': '2.0',
        'id': '1',
        'method': 'tools/call',
        'params': {
            'name': 'gaia.jira',
            'arguments': {
                'query': query,
                'operation': 'query'
            }
        }
    })

    result = response.json()
    if 'result' in result:
        import json
        content = json.loads(result['result']['content'][0]['text'])
        return content
    raise Exception(result.get('error', 'Unknown error'))

# Usage
issues = query_jira_via_mcp('show ideas in MDP project')
print(f"Success: {issues['success']}")
print(f"Result: {issues['result']}")
```

**cURL Example:**
```bash
curl -X POST http://localhost:8765/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
      "name": "gaia.jira",
      "arguments": {
        "query": "show my ideas in Parking lot status"
      }
    }
  }'
```

For detailed MCP integration with workflow tools, see:
- **[MCP Documentation](./mcp.md)** - Complete MCP bridge reference
- **[n8n Integration Guide](./n8n.md)** - Workflow automation examples

## Key Features

### Automatic Configuration Discovery

The agent automatically discovers your Jira instance configuration on first use:

```python
# The agent learns about:
# - Available projects and their keys
# - Issue types (Bug, Task, Story, Epic, etc.)
# - Valid statuses (To Do, In Progress, Done, etc.)
# - Priority levels (Highest, High, Medium, Low, Lowest)
# - Custom fields and values specific to your instance
```

This means the agent adapts to YOUR Jira setup without any configuration files.

### Intelligent Query Translation

The agent uses an LLM to understand context and intent:

- **"my issues"** → Knows to use `assignee = currentUser()`
- **"critical"** → Maps to your instance's priority values
- **"this week"** → Calculates correct date ranges
- **"in progress"** → Uses your actual status names (even if custom)

### Robust Error Handling and Recovery

- **Error Recovery**: Automatically retries failed operations with exponential backoff
- **Intelligent Fallbacks**: When operations fail, the agent attempts alternative approaches
- **Credential Validation**: Validates credentials before operations to prevent unnecessary API calls
- **Helpful Error Messages**: Provides specific, actionable error messages for invalid queries
- **Smart Suggestions**: Suggests corrections for common mistakes (e.g., invalid issue types, wrong field names)
- **Network Resilience**: Handles network issues gracefully with automatic reconnection attempts
- **State Recovery**: Maintains conversation context even after errors, allowing users to continue without starting over

## Configuration

### Getting Your Jira API Token

1. Log into your Atlassian account
2. Go to [Account Settings &gt; Security &gt; API tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
3. Click "Create API token"
4. Give it a name (e.g., "GAIA Integration")
5. Copy the token and save it securely

### Setting Environment Variables

#### Windows

```cmd
set ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
set ATLASSIAN_API_KEY=your-api-token
set ATLASSIAN_USER_EMAIL=your-email@example.com
```

#### Linux/Mac

```bash
export ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
export ATLASSIAN_API_KEY=your-api-token
export ATLASSIAN_USER_EMAIL=your-email@example.com
```

#### Using .env file

```bash
# Create a .env file in your project root
ATLASSIAN_SITE_URL=https://your-domain.atlassian.net
ATLASSIAN_API_KEY=your-api-token
ATLASSIAN_USER_EMAIL=your-email@example.com
```

## Advanced Usage

### Custom Model Selection

```python
# Use a different LLM model for specific use cases
agent = JiraAgent(
    model_id="gpt-4",  # Use GPT-4 for complex queries
    debug=True,         # Enable debug output
    show_prompts=True   # Show LLM prompts for transparency
)
```

### Saving Query History

```python
# Save query results to file for audit/analysis
result = agent.process_query(
    "show all security-related issues",
    output_to_file=True,    # Saves to timestamped JSON file
    filename="security_audit.json"  # Custom filename
)

print(f"Results saved to: {result.get('output_file')}")
```

## Troubleshooting

### Common Issues and Solutions

#### "Missing Jira credentials"

```bash
# Check if environment variables are set
echo $ATLASSIAN_SITE_URL
echo $ATLASSIAN_API_KEY
echo $ATLASSIAN_USER_EMAIL

# Verify API token is valid by testing with curl
curl -u your-email@example.com:your-api-token \
  https://your-domain.atlassian.net/rest/api/2/myself
```

#### "No issues found" when you know they exist

```bash
# Enable debug mode to see the generated JQL
gaia jira --debug "your query"

# Check if you have permission to view the project
gaia jira "show all projects"
```

#### "Invalid issue type" errors

```python
# Discover what issue types are available
agent = JiraAgent()
config = agent.initialize()
print("Available issue types:", config['issue_types'])
# Note: The dummy project uses "Idea" and "Epic" as issue types, not "Bug"/"Task"/"Story"
```

#### MCP Bridge Connection Issues

```bash
# Check if MCP bridge is running
gaia mcp status

# Restart the bridge if needed
gaia mcp stop
gaia mcp start

# Test the bridge directly
curl -X POST http://localhost:8765/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Best Practices

### 1. Use Specific Queries

```bash
# Good - specific and actionable
gaia jira "show my high priority ideas in project MDP"

# Less effective - too vague
gaia jira "show issues"
```

### 2. Leverage Auto-Discovery

```python
# Let the agent discover your setup first
agent = JiraAgent()
config = agent.initialize()

# Now the agent knows your specific configuration
result = agent.process_query("create an Idea")  # Uses your issue types
```

### 3. Use Interactive Mode for Exploration

```bash
# Best for iterative queries and exploration
gaia jira --interactive
```

### 4. Create Related Ideas

```python
# Create multiple related ideas efficiently
for idea in ["AI chatbot", "VR features", "Analytics dashboard"]:
    agent.process_query(f"create idea: {idea}")
```

## Testing Your Integration

### Quick Test Script

```python
#!/usr/bin/env python
"""Test your Jira integration"""

from gaia.agents.jira.agent import JiraAgent
import os

# Check credentials
if not os.getenv("ATLASSIAN_SITE_URL"):
    print("❌ Missing ATLASSIAN_SITE_URL")
    exit(1)

print("✅ Credentials found")

# Initialize and discover
agent = JiraAgent(silent_mode=True)
config = agent.initialize()

if config['projects']:
    print(f"✅ Connected! Found {len(config['projects'])} projects")
    print(f"   Projects: {', '.join([p['key'] for p in config['projects'][:3]])}...")
else:
    print("❌ No projects found - check permissions")
    exit(1)

# Test a simple query
result = agent.process_query("show 3 recent issues")
if result['status'] == 'success':
    print("✅ Query successful!")
    print(f"   Result: {result['final_answer'][:100]}...")
else:
    print(f"❌ Query failed: {result.get('error_history', [])}")
```

### Running Agent Tests

```bash
# Run the test suite
python tests/test_jira.py

# Run with specific options
python tests/test_jira.py --interactive  # Choose tests interactively
python tests/test_jira.py --debug        # Show debug output
python tests/test_jira.py --show-prompts # Display LLM prompts
```

## Limitations

- **API Rate Limits**: Atlassian enforces rate limits on API calls
- **Field Permissions**: Can only access/modify fields you have permission for
- **Bulk Operations**: Currently processes items sequentially, not in parallel
- **Attachment Support**: Does not currently support file attachments
- **Workflow Transitions**: Limited support for complex workflow transitions

## WebUI Configuration

The JIRA WebUI provides an in-app configuration screen where you can:
- Enter your JIRA server URL
- Set your username/email
- Configure your API token

Configuration is stored locally and persists between sessions.

## See Also

- [Apps Documentation](./apps.md) - Desktop applications documentation
- [GAIA CLI Documentation](./cli.md) - Full command line interface guide
- [MCP Server Documentation](./mcp.md) - External integration details
- [Agent Development Guide](./dev.md) - Build your own agents
- [Features Overview](./features.md) - Complete GAIA capabilities
