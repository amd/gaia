# Improve SD Agent Playbook and Reliability

## Summary

Split the SD agent playbook into 3 progressive parts and fixed critical reliability issues in the agent.

## Playbook Improvements

**Split into 3 parts** for better learning progression:
- **Part 1:** Quick start + build first agent (25 min)
- **Part 2:** Architecture deep dive (20 min)
- **Part 3:** Advanced patterns & variations (20 min)

**Documentation enhancements:**
- Added Lemonade Server architecture explanation with link
- Created Mermaid diagrams showing multi-modal flow
- Added 5 video placeholders for demos
- Improved code examples with detailed comments
- Made prompts more specific and meaningful

## Agent Reliability Fixes

**Fixed multi-image generation issue:**
- Updated system prompt to default to ONE image unless explicitly requested
- Added clear examples showing single vs. multiple image generation

**Fixed tool execution error:**
- Changed `if image_path is None:` to `if not image_path:` in `create_story_from_last_image`
- Now correctly handles empty strings from LLM tool calls

**Improved UX:**
- Agent now includes full story text in final answer (not just file path)
- Users can read stories immediately without opening files
- Images now use random seeds by default for variety
- Use `--seed` option for reproducible results

## Files Changed

- `docs/playbooks/sd-agent/` - Split into index + 3 parts
- `src/gaia/agents/sd/prompts.py` - Improved workflow instructions
- `src/gaia/agents/sd/agent.py` - Fixed empty string handling
- `docs/docs.json` - Added new parts, removed presentations
- `.gitignore` - Excluded backup file

## Testing

Tested with: `gaia sd "generate an image of a robot exploring ancient ruins and tell me the story of what it discovers"`

✅ Generates ONE image (not variations)
✅ Creates story successfully
✅ Completes in 4 steps (not 8+)
✅ Shows story text in final answer
