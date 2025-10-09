# JAX (Jira Agent Experience)

Natural language interface for JIRA operations powered by AI.

## For Users

### Using Released Version
1. Download [GAIA installer](https://github.com/amd/gaia/releases/latest) from releases
2. Launch JAX (JIRA Agentic Experience) from Start Menu or Applications
3. Configure JIRA credentials (see [Prerequisites](../jira.md#prerequisites) for setup)

### Features
- **Natural language queries**: "show my open issues"
- **Create issues**: "create a bug for login problem"
- **Search**: "find issues assigned to John"
- **Project navigation**: "show project ABC status"

## For Contributors

### Prerequisites
1. Node.js 20+ installed
2. MCP server running: `gaia mcp start`

### Setup & Run
```bash
# Install dependencies (first time only)
npm install
cd src/gaia/electron && npm install
cd src/gaia/apps/jira/webui && npm install

# Run the app
npm run app:jira:run:electron    # Desktop mode
npm run app:jira:run:dev         # Web development mode
```

### Development
JAX (Jira Agent Experience) is located at `src/gaia/apps/jira/webui/` and connects to the MCP server for AI-powered operations.

## See Also
- [Apps Documentation](../) - Overview of all GAIA apps
- [MCP Documentation](../mcp.md) - MCP server setup