---
name: web-search
description: Search the web via the Brave Search API. Use when the user asks about current events or external facts.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: verified
    permissions:
      - network:read:*.brave.com
    requirements:
      python: ">=3.10"
      env_vars:
        - BRAVE_API_KEY
    tools:
      - name: search_web
        description: Search the web for current information
        parameters:
          query: {type: string, required: true}
          max_results: {type: integer, required: false}
        returns: {type: object}
---

# Web Search

A self-contained Brave Search wrapper — `search_web` is this skill's own `@tool`,
backed by the skill's `tools.py`.
