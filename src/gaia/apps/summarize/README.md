# GAIA Summarizer

The GAIA Summarizer is a powerful tool for generating summaries of meeting transcripts and emails using local LLMs.

## Features

- **Multiple Summary Styles**: Generate different types of summaries based on your needs
- **Auto-Detection**: Automatically detects whether input is a transcript or email
- **Batch Processing**: Process entire directories of files
- **Multiple Output Formats**: JSON, PDF, or email output
- **Local LLM Processing**: Optimized for local Lemonade models
- **Performance Metrics**: Track LLM performance and token usage
- **Configuration Templates**: Pre-defined configs for common use cases
- **HTML Viewer**: Interactive HTML viewer for JSON summaries with automatic browser opening
- **Error Handling**: Robust retry logic and error recovery

## Installation

The summarizer is included with GAIA and designed to work with local LLMs via the Lemonade server.

```bash
# Start the local LLM server (primary usage)
lemonade-server serve

# Optional: Install PDF support
pip install reportlab
```

## Usage

### Test Data

GAIA includes sample meeting transcripts and emails for testing the summarizer:

- **Meeting Transcript**: `data/txt/test_transcript.txt` - Sample project status meeting with participants, discussions, and action items
- **Email**: `data/txt/test_email.txt` - Sample project status update email with development progress and next steps

### Basic Usage

```bash
# Summarize the sample transcript
gaia summarize -i data/txt/test_transcript.txt -o summary.json

# Summarize the sample email
gaia summarize -i data/txt/test_email.txt -o email_summary.json

# Summarize with specific styles
gaia summarize -i data/txt/test_transcript.txt --styles executive action_items

# Generate PDF output
gaia summarize -i data/txt/test_transcript.txt -f pdf

# Process entire data directory
gaia summarize -i data/txt/ -o ./summaries/
```

### Cloud Models (Testing)

```bash
# Use GPT-4 for testing (requires OPENAI_API_KEY)
gaia summarize -i data/txt/test_transcript.txt -m gpt-4 --styles executive brief

# Set your OpenAI API key
export OPENAI_API_KEY=your_api_key_here
```

### Email Output

```bash
# Generate email with summary
gaia summarize -i data/txt/test_transcript.txt -f email --email-to team@company.com

# With CC and custom subject
gaia summarize -i data/txt/test_transcript.txt -f email \
  --email-to team@company.com \
  --email-cc manager@company.com \
  --email-subject "Weekly Meeting Summary"
```

### Using Configurations

```bash
# List available configurations
gaia summarize --list-configs

# Use a configuration
gaia summarize -i data/txt/test_transcript.txt --config meeting_summary

# Override config settings
gaia summarize -i data/txt/test_email.txt --config email_brief --max-tokens 1024
```

## Summary Styles

- **brief**: Concise 2-3 sentence summary
- **detailed**: Comprehensive summary with all key details
- **bullets**: Key points in bullet format
- **executive**: High-level summary with strategic focus
- **participants**: Extract meeting participants (transcripts) or email recipients
- **action_items**: Extract specific action items with owners and deadlines
- **all**: Generate all available styles

## Output Formats

### JSON Format
Default format with full metadata and performance metrics:
- Summary text and extracted information
- Performance statistics (tokens, timing)
- Original content included
- Compatible with eval framework
- **Automatic HTML viewer generated** (can be disabled with `--no-viewer`)

### PDF Format
Professional report format:
- Formatted summary sections
- Performance metrics table
- Original content (truncated if too long)
- Requires `reportlab` package

### Email Format
Opens default email client:
- Summary formatted for email
- Supports TO, CC recipients
- Preview before sending
- Single file input only

## Configuration Files

Pre-defined configurations in `configs/`:
- `meeting_summary`: Standard meeting summaries
- `meeting_minutes`: Formal documentation
- `email_brief`: Quick email summaries
- `quick_brief`: Ultra-concise summaries
- `comprehensive`: Full analysis with all styles

## Advanced Options

### HTML Viewer

By default, an interactive HTML viewer is automatically created and opened when generating JSON output:
- Beautiful formatted display of summaries
- Performance metrics visualization
- Collapsible original content
- Works offline (no external dependencies)

```bash
# Default behavior - opens HTML viewer automatically
gaia summarize -i data/txt/test_transcript.txt -o summary.json

# Disable automatic HTML viewer
gaia summarize -i data/txt/test_transcript.txt -o summary.json --no-viewer

# Batch processing - HTML files created but not opened
gaia summarize -i data/txt/ -o ./summaries/
# Open any .html file manually to view formatted summaries
```

### Performance Optimization

```bash
# Combine multiple styles into single LLM call
gaia summarize -i data/txt/test_transcript.txt --styles executive participants action_items --combined-prompt
```

### Verbosity Control

```bash
# Quiet mode (minimal output)
gaia summarize -i data/txt/test_transcript.txt -o summary.json --quiet

# Verbose mode (debug information)
gaia summarize -i data/txt/test_transcript.txt -o summary.json --verbose
```

### Model Selection

```bash
# Local models (recommended - default)
gaia summarize -i data/txt/test_transcript.txt --model Llama-3.2-3B-Instruct-Hybrid

# Larger local model for complex content
gaia summarize -i data/txt/test_transcript.txt --model Llama-3.1-8B-Instruct-Hybrid

# Cloud models (requires OPENAI_API_KEY)
gaia summarize -i data/txt/test_transcript.txt --model gpt-4
```

## Error Handling

The summarizer includes:
- Automatic retry on LLM failures (3 attempts)
- Token limit detection and content truncation
- Encoding detection for various file formats
- Connection error recovery
- Email validation

## Programmatic Usage

```python
from gaia.apps.summarize.app import SummarizerApp, SummaryConfig

# Local model configuration
config = SummaryConfig(
    model="Llama-3.2-3B-Instruct-Hybrid",
    styles=["executive", "action_items"],
    max_tokens=1024
)

# Cloud model configuration
config_cloud = SummaryConfig(
    model="gpt-4",
    styles=["executive", "action_items"],
    max_tokens=2048
)

# Create summarizer
app = SummarizerApp(config)

# Summarize content
result = app.summarize("Meeting transcript content...")

# Or summarize file
result = app.summarize_file(Path("data/txt/test_transcript.txt"))
```

## Requirements

### Primary Requirements (Local LLMs)
- GAIA installation
- Lemonade server running (local LLM execution)

### Optional Requirements
- `reportlab` for PDF output
- `OPENAI_API_KEY` environment variable (for cloud models)

## Troubleshooting

### "Connection refused" error (Local models)
Ensure Lemonade server is running:
```bash
lemonade-server serve
```

### "OPENAI_API_KEY not found" error (Cloud models)
Set your OpenAI API key:
```bash
export OPENAI_API_KEY=your_api_key_here
# Or add to .env file
echo "OPENAI_API_KEY=your_api_key_here" >> .env
```

### Token limit errors
- Use `--max-tokens` to reduce output size
- Content is automatically truncated if too long

### PDF generation fails
Install reportlab:
```bash
pip install reportlab
```

### Email client doesn't open
- Check default email client settings
- Ensure email addresses are valid
- Try with a simple test first

## Future Enhancements

- Support for more file formats (DOCX, RTF)
- Integration with calendar systems
- Custom summary templates
- Multi-language support
- Audio transcription integration