# Performance Analysis Plotter

Generate plots of llama.cpp telemetry from one or more server log files. The script pulls prompt tokens (`n_prompt_tokens`), input tokens, output tokens, TTFT, TPS, and an aggregate prefill vs decode time split, producing one line per log file for each metric.

## Requirements

- Python 3.8+
- `matplotlib` (`pip install matplotlib`)

## Usage

```bash
python perf_analysis.py [--show] <log_file> [<log_file> ...]
```

- All plots are generated: prompt tokens, input tokens, output tokens, TTFT, TPS, and a prefill vs decode pie chart.
- `--show` — display plots interactively in addition to saving images.

## Outputs

Image files are written to the current directory:

- `prompt_token_counts.png`
- `input_token_counts.png`
- `output_token_counts.png`
- `ttft_seconds.png`
- `tps.png`
- `prefill_decode_split.png` (one pie per log showing prefill time from TTFT vs decode time from output tokens / TPS, with a legend mapping pies to logs)

When multiple logs are provided, each plot includes one line per log with a legend naming each file, and the prefill/decode figure includes one pie per log plus a legend mapping pies to logs.
