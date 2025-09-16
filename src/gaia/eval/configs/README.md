# Batch Experiment Configuration Examples

This directory contains example configurations for running batch experiments on transcript data. These configurations are specifically designed for **transcript summarization** experiments but can be adapted for Q&A tasks.

## Available Configurations

### 1. `basic_summarization_lfm2.json`
**Purpose**: Mixed cloud and local model comparison
**Best for**: Comparing Claude (cloud) vs local Lemonade models
**Models**: Claude Sonnet 4, Mistral-7B, Phi-3.5-Mini, LFM2, various other local models
**Features**:
- Cloud vs local model performance comparison
- Consistent system prompt across all models
- Production-ready with `combined_prompt: false`

**Usage**:
```bash
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_lfm2.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments
```

### 2. `basic_summarization_hybrid.json`
**Purpose**: Llama model size comparison
**Best for**: Comparing different Llama Hybrid model sizes
**Models**: Llama-3.2-1B, Llama-3.2-3B, Llama-3.1-8B (all Hybrid versions)
**Features**:
- Compare small vs medium vs large Llama models
- All local inference through Lemonade
- Consistent configuration across sizes

**Usage**:
```bash
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_hybrid.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments
```

### 3. `basic_summarization_gguf.json`
**Purpose**: GGUF quantized model comparison
**Best for**: Testing quantized models for edge deployment
**Models**: Qwen3-0.6B, Qwen3-1.7B, Qwen3-4B (all GGUF format)
**Features**:
- Ultra-lightweight models suitable for resource-constrained environments
- All local inference with minimal memory footprint
- Perfect for privacy-focused deployments

**Usage**:
```bash
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_gguf.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments
```

### 4. `summary_styles.json`
**Purpose**: Explore different summarization approaches
**Best for**: Finding the right summary style for your use case
**Models**: Claude Sonnet 4 (consistent model, varying approaches)
**Features**:
- **Executive Summary**: High-level, C-suite focused
- **Action Items**: Task and outcome focused
- **Narrative**: Story-telling approach
- **Technical Notes**: Detailed technical documentation
- **Creative Insights**: Analytical with strategic insights

**Usage**:
```bash
gaia batch-experiment -c src/gaia/eval/configs/summary_styles.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments
```

## How to Use These Configurations

### Basic Workflow
```bash
# 1. Choose a configuration
CONFIG="src/gaia/eval/configs/basic_summarization_lfm2.json"

# 2. Generate groundtruth from your transcript data
gaia groundtruth -d ./test_data/meetings --use-case summarization -o ./groundtruth

# 3. Run experiments using groundtruth (for comparative evaluation)
gaia batch-experiment -c $CONFIG -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# 4. Evaluate results 
gaia eval -d ./experiments -o ./evaluation

# 5. Generate comparative report
gaia report -d ./evaluation -o ./reports/evaluation_report.md

# 6. View interactive results
gaia visualize --experiments-dir ./experiments --evaluations-dir ./evaluation
```

### Model Performance Comparison Workflow
Compare different model types systematically:

```bash
# Compare cloud vs local models
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_lfm2.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# Compare different Llama sizes
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_hybrid.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# Compare lightweight GGUF models
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_gguf.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# Evaluate all experiments together
gaia eval -d ./experiments -o ./evaluation
gaia report -d ./evaluation -o ./reports/model_comparison_report.md
```

### Creating Custom Configurations

You can modify these configurations or create your own:

1. **Copy an existing config**: Start with the closest match to your needs
2. **Modify system prompts**: Adjust the analysis focus and style
3. **Adjust parameters**:
   - `max_tokens`: Higher for detailed summaries (1024-2048)
   - `temperature`: Lower (0.0-0.1) for consistency, higher (0.3-0.7) for creativity
   - `model`: Choose based on cost/quality needs
4. **Add experiments**: Include multiple variations to compare approaches

### Configuration Structure

```json
{
  "description": "Human-readable description of the config purpose",
  "experiments": [
    {
      "name": "Unique-Experiment-Name",
      "llm_type": "claude",  // or "lemonade"
      "model": "claude-sonnet-4-20250514",
      "experiment_type": "summarization",  // or "qa"
      "system_prompt": "Detailed instructions for the LLM...",
      "max_tokens": 1024,
      "temperature": 0.1,
      "parameters": {}  // Additional model-specific parameters
    }
  ]
}
```

## Tips for Effective Summarization Experiments

### System Prompt Best Practices
- **Be specific** about the desired output format
- **Define the role** (meeting analyst, executive assistant, etc.)
- **Structure the task** with numbered sections or clear expectations
- **Include context** about the audience or use case

### Token Allocation Guidelines
- **Executive summaries**: 512-768 tokens
- **Standard summaries**: 1024 tokens
- **Detailed analysis**: 1536-2048 tokens
- **Technical documentation**: 1536+ tokens

### Temperature Guidelines
- **Factual summarization**: 0.0-0.1
- **Structured analysis**: 0.1-0.2
- **Creative insights**: 0.3-0.6
- **Brainstorming**: 0.6-0.8

### Model Selection Guidelines

**Cloud Models:**
- **Claude Sonnet 4**: High quality, reliable for production use, costs ~$0.003/1K input tokens

**Local Models (via Lemonade):**
- **Llama-3.1-8B-Hybrid**: Best local quality, good for most use cases
- **Llama-3.2-3B-Hybrid**: Balanced performance, faster inference
- **Llama-3.2-1B-Hybrid**: Fastest inference, basic summaries
- **Mistral-7B**: Good alternative to Llama, different training approach
- **Qwen3-4B-GGUF**: Best quantized model quality
- **Qwen3-1.7B-GGUF**: Good quantized performance
- **Qwen3-0.6B-GGUF**: Ultra-lightweight for edge deployment
- **Phi-3.5-Mini**: Microsoft's efficient small model

## Getting Started

1. **Start simple**: Use `basic_summarization_lfm2.json` for your first experiment (includes both cloud and local models)
2. **Compare local models**: Try `basic_summarization_hybrid.json` to compare different Llama sizes
3. **Test lightweight models**: Use `basic_summarization_gguf.json` for resource-constrained environments
4. **Explore styles**: Use `summary_styles.json` to find your preferred summarization approach

### Recommended Learning Path
```bash
# Step 1: Start with mixed cloud/local comparison
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_lfm2.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# Step 2: Focus on local models if privacy/cost is important
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_hybrid.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# Step 3: Test edge deployment with quantized models
gaia batch-experiment -c src/gaia/eval/configs/basic_summarization_gguf.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments

# Step 4: Experiment with different summarization styles
gaia batch-experiment -c src/gaia/eval/configs/summary_styles.json -i ./groundtruth/consolidated_summarization_groundtruth.json -o ./experiments
```

## Support

For questions about these configurations or creating custom ones:
- Check the main documentation in `src/gaia/eval/README.md`
- Run `gaia batch-experiment --help` for command options
- Use `gaia batch-experiment --create-sample-config` to generate templates