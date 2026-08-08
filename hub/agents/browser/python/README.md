# gaia-agent-browser

Standalone GAIA agent — web research (search, fetch, download). Depends on the published
`amd-gaia` framework wheel.

## Features

- **Web Search**: Configurable search providers including DuckDuckGo (default) and You.com
- **Page Fetching**: Extract content from web pages  
- **File Download**: Save files from URLs locally
- **SSRF Protection**: Built-in security against Server Side Request Forgery

## Search Providers

### DuckDuckGo (Default)
Uses DuckDuckGo HTML search API. No API key required.

### You.com 
Uses You.com Search API with optional authentication:
- **Keyless mode**: 100 free searches/day per IP (no setup required)
- **Authenticated mode**: Higher rate limits with `YDC_API_KEY` environment variable

## Configuration

```python
from gaia_agent_browser import BrowserAgent, BrowserAgentConfig

# Use DuckDuckGo (default)
agent = BrowserAgent()

# Use You.com keyless (100 searches/day)
config = BrowserAgentConfig(web_search_provider="youcom")
agent = BrowserAgent(config)

# Use You.com with API key (higher limits)
import os
os.environ['YDC_API_KEY'] = 'your_api_key_here'
config = BrowserAgentConfig(web_search_provider="youcom")
agent = BrowserAgent(config)

# Or pass API key directly
config = BrowserAgentConfig(
    web_search_provider="youcom",
    youcom_api_key="your_api_key_here"
)
agent = BrowserAgent(config)
```

## Environment Variables

- `YDC_API_KEY`: You.com API key (automatically detected)

## Install

```bash
pip install gaia-agent-browser              # from PyPI (once published)
pip install -e hub/agents/browser/python    # editable, for development
```

Installing registers the `web` agent via the `gaia.agent` entry-point
group; the GAIA registry discovers it automatically.

## Develop / test

```bash
pip install -e ".[test]"
pytest hub/agents/browser/python/tests/ -x
```
