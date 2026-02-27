# Code Snippet Synchronization

This website displays **live code examples** from the actual GAIA repository to ensure the code stays in sync.

## Current Setup (Local)

The website reads code snippets from the local GAIA repository at build time:

```
website/                          (this repo)
  src/utils/codeSnippets.ts      reads from ↓
../../../gaia3/examples/         (local GAIA repo)
```

**Files synced:**
- `weather_agent.py` → MCP/Voice tab
- `rag_doc_agent.py` → Knowledge/Documents tab
- `product_mockup_agent.py` → Coding tab
- `file_watcher_agent.py` → Automation/Workflow tab

## Future Setup (GitHub)

When examples are published on GitHub, update `src/utils/codeSnippets.ts`:

```typescript
// Option 1: Fetch from GitHub at build time
const GITHUB_BASE = 'https://raw.githubusercontent.com/amd/gaia/main/examples';

export async function readExampleFile(filename: string): Promise<string> {
  const response = await fetch(`${GITHUB_BASE}/${filename}`);
  return await response.text();
}

// Option 2: Use GitHub API with caching
import { Octokit } from '@octokit/rest';
const octokit = new Octokit();
const { data } = await octokit.repos.getContent({
  owner: 'amd',
  repo: 'gaia',
  path: `examples/${filename}`
});
```

## Benefits

✅ **Always up-to-date** - Code on website matches actual examples
✅ **Single source of truth** - No manual copy-paste
✅ **CI/CD tested** - Examples are validated in GAIA's test suite
✅ **Version synced** - Can pin to specific git tags/releases

## Build Process

1. **Build time**: Astro runs `loadAllSnippets()`
2. **File read**: Node.js reads example files from local or GitHub
3. **Extraction**: Utility extracts relevant code sections
4. **Syntax highlight**: Adds HTML spans for colors
5. **Static output**: Generated HTML includes the code

The code is **baked into the static site** at build time, so runtime performance is not affected.

## Testing Locally

```bash
cd website
npm run dev
```

The code tabs should show actual Python code from the examples with syntax highlighting.

If examples are missing, check:
1. GAIA repo is cloned at `../../../gaia3` relative to website
2. Example files exist in `gaia3/examples/`
3. Build output doesn't show file read errors
