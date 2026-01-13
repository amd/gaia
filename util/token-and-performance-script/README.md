# Performance Visualizer

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

You can also run the same visualizer directly from the GAIA CLI:

```bash
gaia perf-vis <log_file> [<log_file> ...]
```

## Getting llama.cpp logs

The script expects llama.cpp server logs. You can collect them from [Lemonade](https://lemonade-server.ai/) by running:

```bash
lemonade-server serve --ctx-size 32768 2>&1 | tee agent.log
```

This writes the llama.cpp telemetry to `agent.log`, which you can then feed into the plotter:

```bash
python perf_analysis.py agent.log
```

## Outputs

Image files are written to the current directory:

- `prompt_token_counts.png`
- `input_token_counts.png`
- `output_token_counts.png`
- `ttft_seconds.png`
- `tps.png`
- `prefill_decode_split.png` (one pie per log showing prefill time from TTFT vs decode time from output tokens / TPS, with a legend mapping pies to logs)

When multiple logs are provided, each plot includes one line per log with a legend naming each file, and the prefill/decode figure includes one pie per log plus a legend mapping pies to logs.
