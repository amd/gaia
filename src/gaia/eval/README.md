# RAG Evaluation Framework

A comprehensive framework for evaluating Retrieval Augmented Generation (RAG) systems using ground truth data and Claude AI analysis.

## Overview

This evaluation framework consists of four main components:

1. **Ground Truth Generation** (`groundtruth.py`) - Generates question-answer pairs from documents
2. **Claude Analysis** (`claude.py`) - Provides AI-powered qualitative analysis
3. **RAG Evaluation** (`eval.py`) - Evaluates RAG system performance with metrics and analysis
4. **Summary Reporting** (`eval.py`) - Generates comprehensive reports comparing multiple model evaluations

All components are fully integrated into the Gaia CLI for easy command-line usage, with Python APIs available for programmatic access.

## Components

### GroundTruthGenerator

Generates ground truth data from documents using Claude AI to create question-answer pairs for evaluation.

**Features:**
- Supports HTML, PDF, TXT, MD, CSV files
- Generates document summaries and Q&A pairs
- Batch processing for multiple documents
- Customizable prompts

### ClaudeClient

Provides interface to Claude AI for document analysis and evaluation.

**Features:**
- Multiple Claude model support
- Token counting and optimization
- File analysis with various formats
- HTML text extraction using BeautifulSoup

### RagEvaluator

Evaluates RAG system performance using similarity scores and qualitative analysis.

**Features:**
- Quantitative metrics (similarity scores, pass rates)
- Qualitative analysis using Claude AI
- Detailed reporting and recommendations
- Per-question and overall analysis
- Multi-model comparison reports
- Automated performance ranking
- Cost efficiency analysis
- Strategic investment recommendations

## Setup

### Prerequisites

1. **Environment Variables**
   ```bash
   # Add to your .env file or environment
   ANTHROPIC_API_KEY=your_api_key_here
   ```

2. **Dependencies**
   The framework requires these packages (should be installed via the main project requirements):
   - `anthropic` - Claude AI client
   - `numpy` - Numerical computations
   - `beautifulsoup4` - HTML parsing
   - `python-dotenv` - Environment variable loading

## Usage

### 1. Generate Ground Truth Data

#### Command Line Interface (Recommended)

The groundtruth generator is integrated into the main Gaia CLI:

```bash
# Process a single file
gaia groundtruth -f ./data/html/blender/introduction.html

# Process all HTML files in a directory
gaia groundtruth -d ./data/html/blender

# Process with custom output directory
gaia groundtruth -f ./data/html/intro.html -o ./output/gt

# Process with custom file pattern (e.g., PDFs)
gaia groundtruth -d ./data -p "*.pdf" -o ./output/gt

# Use custom Claude model
gaia groundtruth -f ./data/doc.html -m claude-3-opus-20240229

# Use custom prompt from file
gaia groundtruth -f ./data/doc.html --custom-prompt ./prompts/my_prompt.txt

# Process without saving extracted text
gaia groundtruth -f ./data/doc.html --no-save-text

# Generate 10 Q&A pairs per document
gaia groundtruth -d ./data/html/blender --num-samples 10
```

**Command Line Options:**
- `-f, --file`: Process a single document file
- `-d, --directory`: Process all matching files in a directory
- `-o, --output-dir`: Output directory (default: `./output/groundtruth`)
- `-p, --pattern`: File pattern for directory processing (default: `*.html`)
- `-m, --model`: Claude model to use (default: `claude-sonnet-4-20250514`)
- `--max-tokens`: Maximum tokens for responses (default: 4096)
- `--num-samples`: Number of Q&A pairs to generate per document (default: 5)
- `--no-save-text`: Don't save extracted text for HTML files
- `--custom-prompt`: Path to file containing custom prompt

> **Note**: You can also run the groundtruth generator as a standalone module with `python -m gaia.eval.groundtruth` if preferred.

#### Python API

You can also use the generator programmatically:

```python
from gaia.eval.groundtruth import GroundTruthGenerator

# Initialize generator
generator = GroundTruthGenerator()

# Generate for single document
result = generator.generate(
    file_path="./data/html/blender/introduction.html",
    output_dir="./output/groundtruth"
)

# Batch process multiple documents
results = generator.generate_batch(
    input_dir="./data/html/blender",
    file_pattern="*.html",
    output_dir="./output/groundtruth"
)
```

**Output**: Creates `.groundtruth.json` files containing:
- Document metadata (timestamp, model used, token usage, cost)
- Document summary
- Q&A pairs for evaluation
- Cost tracking: Token usage and API costs per document

### 2. Create Results Template (Optional)

For manual testing, you can create a template file that structures the ground truth data for easy copy-paste of responses:

```bash
# Create template from ground truth file
gaia create-template -f ./output/groundtruth/introduction.groundtruth.json

# Create template with custom output directory
gaia create-template -f ./output/groundtruth/doc.groundtruth.json -o ./templates/

# Create template with custom similarity threshold
gaia create-template -f ./output/groundtruth/doc.groundtruth.json --threshold 0.8
```

This creates a template file with placeholder responses that you can manually fill in:

```json
{
  "metadata": {
    "test_file": "path/to/groundtruth.json",
    "timestamp": "2025-01-XX XX:XX:XX",
    "similarity_threshold": 0.7,
    "instructions": "Fill in the 'response' fields with your RAG system outputs, then evaluate using gaia eval"
  },
  "analysis": {
    "qa_results": [
      {
        "query": "What is Blender?",
        "ground_truth": "Blender is a free and open-source 3D...",
        "response": "[FILL IN YOUR RAG SYSTEM RESPONSE FOR QUESTION 1]"
      }
    ]
  }
}
```

> **Note**: Similarity scores are calculated automatically during evaluation, not stored in templates.

### 3. Run Your RAG System

Test your RAG system against the generated ground truth data. You can either:

**Option A: Use the template (for manual testing)**
1. Use `gaia create-template` to generate a template file
2. Fill in the `response` fields with your RAG system outputs
3. Proceed to evaluation

**Option B: Generate results programmatically**
Your results should be saved in JSON format with this structure:

```json
{
  "metadata": {
    "test_file": "path/to/groundtruth.json",
    "timestamp": "2025-01-XX XX:XX:XX",
    "similarity_threshold": 0.7
  },
  "analysis": {
    "qa_results": [
      {
        "query": "What is Blender?",
        "ground_truth": "Blender is a free and open-source 3D...",
        "response": "Blender is a 3D modeling software..."
      }
    ]
  }
}
```

> **Note**: Similarity scores and pass/fail determinations are calculated automatically during evaluation using the comprehensive scoring system.

### 4. Evaluate Results

#### Command Line Interface (Recommended)

Analyze your RAG system's performance using the CLI:

```bash
# Evaluate RAG results file
gaia eval -f ./output/templates/introduction.template.json

# Evaluate with custom output directory
gaia eval -f ./output/rag/results.json -o ./output/eval

# Evaluate with specific Claude model
gaia eval -f ./output/rag/results.json -m claude-3-opus-20240229

# Evaluate and display summary only (no detailed report file)
gaia eval -f ./output/rag/results.json --summary-only
```

**Command Line Options:**
- `-f, --results-file`: Path to the RAG results JSON file (template or results) (required)
- `-o, --output-dir`: Output directory for evaluation report (default: `./output/eval`)
- `-m, --model`: Claude model to use for evaluation (default: `claude-sonnet-4-20250514`)
- `--summary-only`: Only display summary, don't save detailed evaluation report

**Evaluation Output**: The evaluation generates comprehensive reports including:
- Overall performance rating (excellent/good/fair/poor) with detailed explanation
- Per-question analysis with 4-criteria scoring (correctness, completeness, conciseness, relevance)
- Similarity scores and comprehensive pass/fail determinations
- Cost tracking: Token usage and API costs per question and total
- Strengths, weaknesses, and actionable improvement recommendations
- Statistical metrics: mean, median, min, max, standard deviation of similarity scores

#### Python API

You can also evaluate programmatically:

```python
from gaia.eval.eval import RagEvaluator

# Initialize evaluator
evaluator = RagEvaluator()

# Generate comprehensive evaluation
evaluation_data = evaluator.generate_enhanced_report(
    results_path="./output/rag/results.json",
    output_dir="./output/eval"
)

# Print key metrics
print("Overall Rating:", evaluation_data['overall_rating']['rating'])
print("Pass Rate:", evaluation_data['overall_rating']['metrics']['pass_rate'])
print("Mean Similarity:", evaluation_data['overall_rating']['metrics']['mean_similarity'])
print("Total Cost:", f"${evaluation_data['total_cost']['total_cost']:.4f}")
print("Cost per Question:", f"${evaluation_data['total_cost']['total_cost']/len(evaluation_data['per_question']):.4f}")
```

### 5. Generate Summary Report

After evaluating multiple models, generate a comprehensive comparison report:

### Command Line Interface

```bash
# Generate report from evaluation directory
gaia report -d ./output/eval

# Generate report with custom output filename
gaia report -d ./output/eval -o Model_Performance_Analysis.md

# Generate report and display summary only (no file output)
gaia report -d ./output/eval --summary-only
```

**Command Line Options:**
- `-d, --eval-dir`: Directory containing .eval.json files to analyze (required)
- `-o, --output-file`: Output filename for the markdown report (default: `LLM_RAG_Evaluation_Report.md`)
- `--summary-only`: Only display summary to console, don't save report file

**Report Features:**
The generated markdown report includes:
- **Executive Summary** with performance ranking and production readiness assessment
- **Key Performance Metrics Table** with pass rates, similarity scores, and ratings
- **Critical Failure Patterns** analysis identifying common issues across models
- **Model-Specific Analysis** comparing best and worst performers
- **Cost Efficiency Analysis** with ROI comparison across models
- **Technical Actions** providing prioritized improvement recommendations
- **Investment Decisions** and timeline guidance for strategic resource allocation

#### Python API

```python
from gaia.eval.eval import RagEvaluator

# Initialize evaluator
evaluator = RagEvaluator()

# Generate summary report
result = evaluator.generate_summary_report(
    eval_dir="./output/eval",
    output_path="Model_Comparison_Report.md"
)

print(f"Analyzed {result['models_analyzed']} models")
print(f"Report saved to: {result['report_path']}")

# Access summary data
models_data = result['summary_data']
best_model = models_data[0]  # Sorted by pass rate
print(f"Best model: {best_model['name']} ({best_model['pass_rate']:.0%} pass rate)")
```

## File Structure

```
output/
├── groundtruth/          # Generated ground truth data
│   └── *.groundtruth.json
├── templates/            # Template files for manual testing
│   └── *.template.json
├── rag/                  # RAG system results
│   └── *.results.json
├── eval/                 # Evaluation reports
│   └── *.eval.json
└── reports/              # Summary comparison reports
    └── *.md
```

## Evaluation Metrics

### Quantitative Metrics
- **Similarity Scores**: TF-IDF cosine similarity calculated dynamically during evaluation
- **Pass Rate**: Percentage of responses meeting comprehensive pass criteria
- **Statistical Analysis**: Mean, median, min, max, standard deviation of similarity scores
- **Cost Tracking**: Token usage and API costs per question and total evaluation

### Qualitative Analysis (Claude AI)
- **Correctness**: Factual accuracy assessment (40% weight)
- **Completeness**: Coverage of the question (30% weight)
- **Conciseness**: Appropriate brevity (15% weight)
- **Relevance**: Direct addressing of the query (15% weight)

### Comprehensive Pass/Fail Logic
The system uses sophisticated evaluation criteria combining quantitative and qualitative metrics:
- **Similarity-based**: Pass if TF-IDF cosine similarity ≥ threshold (default 0.7)
- **Qualitative-based**: Pass if weighted qualitative score ≥ 0.6 AND correctness ≥ "fair"
- **Combined logic**: Pass if EITHER similarity OR qualitative criteria are met
- **Hard failure**: Automatic fail if correctness rating is "poor" regardless of other scores

### Overall Ratings
- **Excellent**: Pass rate ≥90%, Mean similarity ≥0.8 (100% quality score)
- **Good**: Pass rate ≥80%, Mean similarity ≥0.7 (66.7% quality score)
- **Fair**: Pass rate ≥60%, Mean similarity ≥0.6 (33.3% quality score)  
- **Poor**: Below fair thresholds (0% quality score)

Quality scores are displayed as percentages with qualitative labels:
- **85-100% (Excellent)** = Predominantly excellent ratings
- **67-84% (Good)** = Predominantly good ratings  
- **34-66% (Fair)** = Predominantly fair ratings
- **0-33% (Poor)** = Predominantly poor ratings

The percentage represents the weighted average of all ratings, converted to a 0-100% scale for intuitive understanding.

## Example Workflow

1. **Prepare Documents**
   ```bash
   # Place documents in data directory
   data/html/blender/introduction.html
   ```

2. **Generate Ground Truth**
   ```bash
   # Using Gaia CLI (recommended) - generates 5 Q&A pairs per document by default
   gaia groundtruth -d ./data/html/blender -o ./output/groundtruth

   # Generate 10 Q&A pairs per document
   gaia groundtruth -d ./data/html/blender -o ./output/groundtruth --num-samples 10

   # Or using standalone module
   # python -m gaia.eval.groundtruth -d ./data/html/blender -o ./output/groundtruth --num-samples 10

   # Or using Python API
   # generator = GroundTruthGenerator()
   # generator.generate_batch("./data/html/blender", output_dir="./output/groundtruth", num_samples=10)
   ```

3. **Create Template (For Manual Testing)**
   ```bash
   # Create template file for manual response entry
   gaia create-template -f ./output/groundtruth/introduction.groundtruth.json -o ./output/templates
   ```

4. **Test RAG System**
   ```bash
   # Option A: Fill in template file manually with your RAG responses
   # Edit ./output/templates/introduction.template.json

   # Option B: Generate results programmatically
   # Your RAG system should process the ground truth and save results
   # Results format: see section "3. Run Your RAG System" above
   ```

5. **Evaluate Performance**
   ```bash
   # Using Gaia CLI (recommended)
   gaia eval -f ./output/templates/introduction.template.json -o ./output/eval

   # Or using Python API
   # evaluator = RagEvaluator()
   # evaluation = evaluator.generate_enhanced_report("./output/templates/introduction.template.json", "./output/eval")
   ```

6. **Generate Summary Report (New!)**
   ```bash
   # Generate comprehensive comparison report after evaluating multiple models
   gaia report -d ./output/eval

   # Or using Python API
   # evaluator = RagEvaluator()
   # result = evaluator.generate_summary_report("./output/eval", "Model_Analysis_Report.md")
   ```

## Error Handling

The framework includes robust error handling:

- **API Overload**: Falls back to raw data when Claude API is overloaded
- **File Not Found**: Clear error messages for missing files
- **JSON Parsing**: Graceful handling of malformed responses
- **Token Limits**: Automatic token counting and optimization

## Best Practices

1. **Similarity Threshold**: Start with 0.7, adjust based on your use case
2. **Model Selection**: Use latest Claude models for better analysis
3. **Batch Processing**: Process multiple documents together for efficiency
4. **Output Organization**: Use consistent directory structure for outputs
5. **Token Management**: Monitor token usage for cost optimization
6. **Multi-Model Testing**: Evaluate multiple models against the same ground truth for comparison
7. **Report Generation**: Use summary reports to identify patterns and make strategic decisions
8. **Production Standards**: Aim for 70% pass rate + 0.7 mean similarity for production readiness

## Command Line Help

Get detailed usage information:

```bash
# Show help and all available options for groundtruth
gaia groundtruth --help

# Show help for template creation
gaia create-template --help

# Show help for evaluation
gaia eval --help

# Show help for summary reporting
gaia report --help

# Show help for all Gaia CLI commands
gaia --help

# Examples are included in the help output
gaia groundtruth -h
gaia create-template -h
gaia eval -h
gaia report -h
```

## Troubleshooting

**Common Issues:**

1. **Missing API Key**: Ensure `ANTHROPIC_API_KEY` is set in environment
2. **File Format**: Check supported formats (HTML, PDF, TXT, MD, CSV)
3. **JSON Structure**: Verify RAG results match expected format
4. **Token Limits**: Use appropriate max_tokens for your model
5. **File Permissions**: Ensure read access to input files and write access to output directory
6. **Module Import**: Use `gaia groundtruth` (recommended) or run from project root: `python -m gaia.eval.groundtruth`

**Template Issues:**
7. **Empty Template**: Ensure ground truth file contains QA pairs in the expected format
8. **Invalid Field Names**: Template uses 'query' and 'ground_truth' - verify your ground truth structure

**Report Issues:**
9. **No Evaluation Files**: Ensure the eval directory contains .eval.json files
10. **Inconsistent Data**: Verify all evaluation files have the expected structure and metrics

**Debug Mode:**
Enable detailed logging by setting log level to DEBUG in your application.