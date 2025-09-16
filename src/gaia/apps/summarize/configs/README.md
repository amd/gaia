# Summarizer Configuration Templates

This directory contains predefined configuration templates for the GAIA summarizer.

## Available Configurations

### meeting_summary.json
Standard meeting summary format ideal for sharing with team members.
- Styles: executive, participants, action_items
- Format: JSON
- Use case: Regular team meetings, status updates

### meeting_minutes.json
Formal meeting documentation with comprehensive details.
- Styles: detailed, participants, action_items
- Format: PDF
- Use case: Board meetings, formal project reviews

### email_brief.json
Quick email summaries with key points.
- Styles: executive, bullets
- Format: JSON
- Use case: Email digests, quick email reviews

### quick_brief.json
Ultra-concise summary with just the essentials.
- Styles: brief only
- Format: JSON
- Use case: Quick overview, executive briefings

### comprehensive.json
Full analysis with all available summary styles.
- Styles: all styles
- Format: JSON
- Use case: Detailed analysis, evaluation purposes

### openai_premium.json
**Testing/Validation Only**: High-quality summaries using GPT-4 for comparison.
- Model: gpt-4
- Styles: executive, detailed, participants, action_items
- Format: JSON
- Use case: Validation against cloud models, quality benchmarking

### openai_fast.json
**Testing/Validation Only**: Quick summaries using GPT-3.5 Turbo for comparison.
- Model: gpt-3.5-turbo
- Styles: brief, action_items
- Format: JSON
- Use case: Speed/cost comparison with local models

## Configuration File Format

```json
{
  "name": "Configuration Name",
  "description": "Brief description shown in --list-configs",
  "styles": ["style1", "style2"],
  "format": "json|pdf|email|both",
  "max_tokens": 1024,
  "include_original": true|false,
  "combined_prompt": true|false
}
```

### Fields

- **name**: Display name for the configuration
- **description**: Brief description shown when listing configs
- **styles**: Array of summary styles to generate
  - Options: brief, detailed, bullets, executive, participants, action_items
- **format**: Output format
  - json: JSON file with metadata and performance metrics
  - pdf: PDF report (not yet implemented)
  - email: Opens email client with summary
  - both: Generates both JSON and PDF
- **max_tokens**: Maximum tokens for LLM generation
- **include_original**: Whether to append original content to output
- **combined_prompt**: Whether to combine multiple styles into one LLM call

## Usage

```bash
# List available configurations
gaia summarize --list-configs

# Use a specific configuration
gaia summarize -i transcript.txt --config meeting_summary

# Override config settings
gaia summarize -i email.txt --config email_brief --format email --email-to team@company.com
```

## Creating Custom Configurations

To create your own configuration:

1. Copy an existing template
2. Modify the fields as needed
3. Save with a descriptive filename (e.g., `my_custom_config.json`)
4. Use with: `gaia summarize --config my_custom_config`

Command-line arguments will override configuration file settings.