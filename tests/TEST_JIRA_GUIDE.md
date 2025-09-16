# GAIA Jira Test Runner Guide

## Overview
The enhanced GAIA Jira test suite provides comprehensive testing capabilities with interactive modes, step-through debugging, and detailed reporting.

## Features

### 1. Step-Through Mode
Pause after each test to inspect results and JSON output files.

### 2. Interactive Test Selection
Choose which tests to run from an organized menu system.

### 3. CSV Export
Export detailed test results to CSV for analysis in Excel or other tools.

### 4. Model Selection
Test with different LLM models to compare performance.

### 5. Categorized Tests
Tests are organized into logical categories for easier navigation.

## Command Line Options

```bash
# Basic usage - run all tests
python tests/test_jira.py

# Step mode - pause after each test
python tests/test_jira.py --step

# Interactive mode - select tests from menu
python tests/test_jira.py --interactive
python tests/test_jira.py -i

# Run specific test
python tests/test_jira.py --test test_search_my_issues

# List all available tests
python tests/test_jira.py --list
python tests/test_jira.py -l

# Export results to CSV
python tests/test_jira.py --csv
python tests/test_jira.py --csv my_results.csv

# Use different model
python tests/test_jira.py --model Qwen3-Coder-30B-A3B-Instruct-GGUF
python tests/test_jira.py -m Qwen2.5-0.5B-Instruct-CPU

# Combine options
python tests/test_jira.py --interactive --step --csv --model Qwen3-Coder-30B-A3B-Instruct-GGUF
```

## Test Categories

### Core Functionality
- Agent initialization
- Search operations
- Query generation
- Issue creation
- Timeout handling
- App methods

### Basic Operations
- Simple fetch queries
- Basic creation patterns

### Intermediate Queries
- Search variations
- Status/priority filtering
- Advanced JQL

### Advanced Operations
- Time-based analysis
- Complex filters
- Multi-step workflows

### Expert Features
- Cross-project analysis
- Analytics queries
- Bulk operations

### Master Level
- Complex business logic
- Automation scenarios
- Predictive analysis

### Comprehensive Coverage
- Additional advanced queries
- Strategic insights

## CSV Export Format

The CSV export includes:

### Summary Section
- Test run date/time
- Total tests run
- Pass/fail counts
- Overall success rate

### Category Summary
- Tests per category
- Pass/fail by category
- Category success rates

### Detailed Results
- Test name
- Category
- Status (PASSED/FAILED)
- Duration in seconds
- Failure reason (if failed)
- Timestamp

## Example CSV Output

```csv
GAIA Jira Agent Test Results
Test Run Date: 2025-08-29 10:30:00
Total Tests: 25
Passed: 22
Failed: 3
Success Rate: 88.0%

Category Summary
Category,Total,Passed,Failed,Success Rate
Core Functionality,8,8,0,100.0%
Basic Operations,2,2,0,100.0%
Intermediate Queries,3,2,1,66.7%
...

Detailed Test Results
Test Name,Category,Status,Duration (s),Failure Reason,Timestamp
test_agent_initialization,Core Functionality,PASSED,0.52,,2025-08-29T10:30:01
test_search_my_issues,Core Functionality,PASSED,2.31,,2025-08-29T10:30:03
...
```

## JSON Output Files

Each test that calls the agent's `process_query` method generates a JSON trace file:
- Filename: `agent_output_YYYYMMDD_HHMMSS.json`
- Contains complete conversation history
- Includes all tool calls and responses
- Shows agent reasoning steps

## Interactive Mode Workflow

1. Run with `--interactive` flag
2. Select tests from numbered menu:
   - Enter test number(s) separated by commas
   - Enter `0` to run all tests
   - Enter `q` to quit
3. After tests complete, choose to run more or exit
4. CSV export happens automatically if `--csv` was specified

## Step Mode Workflow

1. Run with `--step` flag
2. After each test:
   - Review console output
   - Check `agent_output_*.json` files
   - Press Enter to continue
   - Press 'q' to stop testing

## Best Practices

### For Debugging
```bash
python tests/test_jira.py --interactive --step --csv debug_results.csv
```
- Use interactive mode to select specific failing tests
- Step mode allows inspection after each test
- CSV provides permanent record

### For Regression Testing
```bash
python tests/test_jira.py --csv regression_$(date +%Y%m%d).csv
```
- Run all tests
- Save timestamped results
- Compare CSV files over time

### For Model Comparison
```bash
# Test with different models
python tests/test_jira.py --model Qwen2.5-0.5B-Instruct-CPU --csv model1.csv
python tests/test_jira.py --model Qwen3-Coder-30B-A3B-Instruct-GGUF --csv model2.csv
```
- Compare performance across models
- Analyze success rates and durations

## Environment Requirements

Required environment variables:
- `ATLASSIAN_SITE_URL`: Your Atlassian site URL
- `ATLASSIAN_API_KEY`: API authentication key
- `ATLASSIAN_USER_EMAIL`: User email for authentication

## Troubleshooting

### Tests Failing with Timeout
- Increase agent max_steps in test setup
- Check network connectivity to Jira
- Verify API credentials are valid

### CSV Export Issues
- Ensure write permissions in output directory
- Check for special characters in test output
- Verify sufficient disk space

### Model Not Found
- Check model is installed/available
- Verify model name spelling
- Ensure Lemonade server is running if using local models

## Available Models

Common models for testing:
- `Qwen2.5-0.5B-Instruct-CPU` (default, fast)
- `Qwen3-Coder-30B-A3B-Instruct-GGUF` (coding tasks)
- `Qwen2.5-3B-Instruct-CPU` (balanced)
- `Qwen2.5-7B-Instruct-CPU` (higher quality)

## Support

For issues or questions:
- Check agent_output_*.json files for detailed traces
- Review CSV export for failure patterns
- Enable debug mode in JiraApp for verbose logging