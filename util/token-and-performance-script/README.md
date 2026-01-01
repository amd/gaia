# Prompt Token Plotter

Generate plots of llama.cpp telemetry from one or more server log files. The script pulls prompt tokens (`n_prompt_tokens`), input tokens, output tokens, TTFT, and TPS, producing one line per log file for each selected metric.

## Requirements

- Python 3.8+
- `matplotlib` (`pip install matplotlib`)

## Usage

```bash
python prompt_token_plot.py [FLAGS] <log_file> [<log_file> ...]
```

- `--all` — generate plots for prompt tokens, input tokens, output tokens, TTFT, and TPS.
- `--prompt-tokens` — plot prompt token counts (from `n_prompt_tokens`).
- `--input-tokens` — plot input token counts (from `Input tokens:` lines).
- `--output-tokens` — plot output token counts.
- `--ttft` — plot time to first token (seconds).
- `--tps` — plot tokens per second.
- `--show` — display plots interactively.
- `--no-save` — do not write image files.
- If no metric flags are provided, `--prompt-tokens` is assumed.

## Outputs

Image files are written to the current directory unless `--no-save` is provided:

- `prompt_token_counts.png`
- `input_token_counts.png`
- `output_token_counts.png`
- `ttft_seconds.png`
- `tps.png`

When multiple logs are provided, each plot includes one line per log with a legend naming each file.
