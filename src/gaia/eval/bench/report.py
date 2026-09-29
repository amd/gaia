# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The harness x model table, as markdown and as self-contained HTML (and a PNG).

One row per (model, harness); each cell is the mean over that row's runs, with
the min-max range beneath it when the runs disagreed. A range whose ends print
the same says nothing, so it is hidden. One run is noise: two Claude Code runs
on one model have differed by 2x in wall time.

Every cost cell names its source (``bench.metering``). An API-equivalent cost
is captioned as such under the number, and a footnote says what that means: a
price of compute, not money spent.
"""

from __future__ import annotations

import html
import os
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import metering
from gaia.logger import get_logger

log = get_logger(__name__)

Range = Tuple[float, float, float]

SOURCE_CAPTION = {
    metering.METERED: "",
    metering.API_EQUIVALENT: "API-equivalent",
    metering.HARNESS_COUNTS: "harness counts",
}
HARNESS_LABEL = {"gaia": "GAIA", "claude-code": "Claude Code"}

FOOTNOTES = {
    metering.METERED: "Metered: the provider's own billing meter, a snapshot before "
    "and after the run. Real charges.",
    metering.API_EQUIVALENT: "API-equivalent: Claude Code's own cost figure on "
    "Anthropic models. On a Claude subscription that is the list price of the "
    "tokens, not money spent: comparable as a price of compute, not as "
    "out-of-pocket cost.",
    metering.HARNESS_COUNTS: "Harness counts: the harness's own per-call token "
    "counts (GAIA's, or the model gateway's for Claude Code on an open model) "
    "priced with the published rate card, where no meter snapshot was taken.",
}


def fmt_time(seconds: float) -> str:
    """``45s`` under a minute, ``m:ss`` under an hour, ``h:mm:ss`` beyond."""
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@dataclass
class Row:
    """One (model, harness) pair over its runs."""

    model: str
    harness: str
    runs: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return HARNESS_LABEL.get(self.harness, self.harness)


def run_metrics(card: Mapping[str, Any]) -> Dict[str, Any]:
    """The numbers one run contributes to its row."""
    tasks = card["tasks"]
    judged = [t for t in tasks if ft._judged(t)]  # pylint: disable=protected-access
    cost = card.get("cost") or ft.run_cost(card)
    return {
        "tasks": len(tasks),
        "passed": sum(1 for t in tasks if t["passed"] is True),
        "judged": len(judged),
        "quality": (
            statistics.mean(
                statistics.mean(t["judge"][a] for a in ft.AXES) for t in judged
            )
            if judged
            else None
        ),
        "truthful": (
            statistics.mean(t["judge"]["fabrication_free"] for t in judged)
            if judged
            else None
        ),
        "steps": sum(t["steps"] for t in tasks),
        "tool_calls": sum(t["tool_calls"] for t in tasks),
        "tokens": cost.get("tokens")
        or sum(t["input_tokens"] + t["output_tokens"] for t in tasks),
        "seconds": sum(t["wall_seconds"] for t in tasks),
        "cost": cost.get("usd"),
        "cost_source": cost.get("source") or "",
        "web_uses": sum(len(t.get("web_uses") or []) for t in tasks),
        "timed_out": sum(1 for t in tasks if t.get("timed_out")),
    }


def collect(run_dirs: Sequence[Path]) -> List[Row]:
    """Rows in the order their first run was named."""
    rows: Dict[Tuple[str, str], Row] = {}
    for given in run_dirs:
        for run_dir in ft.run_dirs(Path(given)):
            card = ft.read_scorecard(run_dir)
            key = (card["model"], card.get("harness") or "gaia")
            rows.setdefault(key, Row(*key)).runs.append(run_metrics(card))
    if not rows:
        raise ValueError("No runs to report.")
    return list(rows.values())


def spread(row: Row, key: str) -> Optional[Range]:
    values = [r[key] for r in row.runs if r.get(key) is not None]
    if not values:
        return None
    return statistics.mean(values), min(values), max(values)


def cost_source(row: Row) -> Optional[str]:
    """The row's one cost source; ``None`` when its runs were priced differently."""
    sources = {r["cost_source"] for r in row.runs}
    return sources.pop() if len(sources) == 1 else None


def _num(dec: int, unit: str = "", scale: float = 1.0) -> Callable[[float], str]:
    return lambda v: f"{v / scale:.{dec}f}{unit}"


FORMATS: Dict[str, Callable[[float], str]] = {
    "passed": _num(1),
    "quality": _num(2),
    "truthful": _num(2),
    "steps": _num(0),
    "tool_calls": _num(0),
    "tokens": _num(2, "M", 1e6),
    "seconds": fmt_time,
    "cost": lambda v: f"${v:.3f}" if v < 1 else f"${v:.2f}",
    "web_uses": _num(0),
}


def cell_text(value: Optional[Range], fmt: Callable[[float], str]) -> Tuple[str, str]:
    """``(mean, "lo–hi")``; the range is empty when both ends print the same."""
    if value is None:
        return "", ""
    mean, lo, hi = value
    lo_s, hi_s = fmt(lo), fmt(hi)
    return fmt(mean), "" if lo_s == hi_s else f"{lo_s}–{hi_s}"


def _passed(row: Row) -> Tuple[str, str]:
    value = spread(row, "passed")
    tasks = row.runs[0]["tasks"]
    if value is None:
        return "", ""
    mean, rng = cell_text(value, lambda v: f"{v:g}" if v == int(v) else f"{v:.1f}")
    return f"{mean}/{tasks}", rng


def _cost(row: Row) -> Tuple[str, str, str]:
    """``(mean, range, caption)`` — the caption names a non-metered source."""
    source = cost_source(row)
    if source is None:
        return "n/a", "", "mixed cost sources"
    value = spread(row, "cost")
    if value is None or not source:
        return "n/a", "", ""
    mean, rng = cell_text(value, FORMATS["cost"])
    return mean, rng, SOURCE_CAPTION.get(source, source)


def _pct(row: Row, base: Row, key: str) -> str:
    a, b = spread(row, key), spread(base, key)
    if not a or not b or not b[0]:
        return ""
    return f"{a[0] / b[0]:.0%}"


COLUMNS = (
    ("Passed", "passed"),
    ("Quality", "quality"),
    ("Truthful", "truthful"),
    ("Steps", "steps"),
    ("Tool calls", "tool_calls"),
    ("Tokens", "tokens"),
    ("% tokens", "%tokens"),
    ("Time", "seconds"),
    ("% time", "%seconds"),
    ("Cost", "cost"),
    ("% cost", "%cost"),
    ("Web uses", "web_uses"),
)


def _cells(row: Row, base: Row) -> List[Tuple[str, str, str]]:
    """``(value, range, caption)`` per column."""
    out: List[Tuple[str, str, str]] = []
    for _, key in COLUMNS:
        if key == "passed":
            out.append((*_passed(row), ""))
        elif key == "cost":
            out.append(_cost(row))
        elif key.startswith("%"):
            pct = _pct(row, base, key[1:])
            caption = ""
            if key == "%cost" and pct and cost_source(row) != cost_source(base):
                caption = (
                    f"vs {SOURCE_CAPTION.get(cost_source(base) or '', '') or 'metered'}"
                )
            out.append((pct, "", caption))
        else:
            out.append((*cell_text(spread(row, key), FORMATS[key]), ""))
    return out


def _sources(rows: Sequence[Row]) -> List[str]:
    found = {cost_source(r) for r in rows}
    return [s for s in metering.COST_SOURCES if s in found]


def render_markdown(rows: Sequence[Row], title: str) -> str:
    base = rows[0]
    head = ["Model", "Harness", "Runs", *(name for name, _ in COLUMNS)]
    lines = [
        f"## {title}",
        "",
        f"Mean over runs, min–max beside it when the runs disagreed. "
        f"Percentages are of `{base.model}` on {base.label}.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    for row in rows:
        cells = []
        for value, rng, caption in _cells(row, base):
            text = value + (f" ({rng})" if rng else "")
            if caption:
                text += f"<br><sub>{caption}</sub>"
            cells.append(text)
        lines.append(
            f"| `{row.model}` | {row.label} | {len(row.runs)} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    lines += [f"- {FOOTNOTES[s]}" for s in _sources(rows)]
    return "\n".join(lines) + "\n"


CSS = """
:root { --gold:#d4af37; --gold-dim:#8a7326; --ink:#ece5d0; --muted:#8a8578; --bg:#000; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
       padding:38px 40px 30px; }
h1 { font-size:22px; letter-spacing:.2px; color:var(--gold); margin:0 0 6px; font-weight:600; }
.sub { color:var(--muted); font-size:12px; margin-bottom:22px; }
table { border-collapse:collapse; width:100%; }
th { font-size:11px; font-weight:600; color:var(--gold); text-align:center; padding:0 9px 9px;
     border-bottom:1px solid var(--gold); white-space:pre-line; letter-spacing:.3px; }
th.l, td.l { text-align:left; }
td { padding:9px; text-align:center; border-bottom:1px solid #16150f;
     font-variant-numeric:tabular-nums; }
tr.cc td { background:#080808; }
tr.gaia td { background:#16120a; }
tr.gaia td.k { color:var(--gold); font-weight:600; }
tr.ref td { color:#fff; background:#0f0d06; }
tr.newmodel td { border-top:1px solid #2a2418; }
td.model { font-weight:500; }
td .rng { display:block; font-size:10px; color:var(--muted); margin-top:2px; }
td .src { display:block; font-size:10px; color:#9a8a4e; margin-top:2px; letter-spacing:.2px; }
.note { color:var(--muted); font-size:11px; margin-top:20px; line-height:1.6; }
.note p { margin:0 0 6px; }
"""


def render_html(rows: Sequence[Row], title: str) -> str:
    base = rows[0]
    esc = html.escape
    head = "".join(
        ["<th class='l'>Model</th><th class='l'>Harness</th><th>Runs</th>"]
        + [f"<th>{esc(name)}</th>" for name, _ in COLUMNS]
    )
    body = []
    previous = None
    for index, row in enumerate(rows):
        klass = "gaia" if row.harness == "gaia" else "cc"
        if index == 0:
            klass += " ref"
        if previous is not None and row.model != previous:
            klass += " newmodel"
        previous = row.model
        cells = [
            f'<td class="l model">{esc(row.model)}</td>',
            f'<td class="l k">{esc(row.label)}</td>',
            f"<td>{len(row.runs)}</td>",
        ]
        for (_, key), (value, rng, caption) in zip(COLUMNS, _cells(row, base)):
            k = ' class="k"' if key in ("cost", "%cost") else ""
            extra = f'<span class="rng">{esc(rng)}</span>' if rng else ""
            extra += f'<span class="src">{esc(caption)}</span>' if caption else ""
            cells.append(f"<td{k}>{esc(value)}{extra}</td>")
        body.append(f'<tr class="{klass}">' + "".join(cells) + "</tr>")
    notes = "".join(f"<p>{esc(FOOTNOTES[s])}</p>" for s in _sources(rows))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>{CSS}</style></head><body>
<h1>{esc(title)}</h1>
<div class="sub">Same tasks, fixtures, time limit and judge for every harness &middot;
mean over runs, min&ndash;max beneath it when the runs disagreed &middot;
percentages are of {esc(base.model)} on {esc(base.label)}</div>
<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>
<div class="note"><p>Quality is the mean of instruction compliance, work quality,
reasoning and fabrication-free (1&ndash;5, blind judge); truthful is fabrication-free
alone, scored against the tool record rather than the agent&rsquo;s claims.
A gap smaller than the range beneath it is noise.</p>{notes}</div>
</body></html>
"""


CHROME_ENV = "GAIA_BENCH_CHROME"
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_CHROME_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
)


def find_chrome() -> Optional[str]:
    explicit = os.environ.get(CHROME_ENV)
    if explicit:
        if not Path(explicit).exists():
            raise FileNotFoundError(f"{CHROME_ENV}={explicit} does not exist")
        return explicit
    for candidate in _CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in _CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def render_png(html_path: Path, png_path: Path, rows: int) -> Optional[Path]:
    """A screenshot of the HTML, or ``None`` (with a message) when there is no Chrome."""
    chrome = find_chrome()
    if chrome is None:
        print(
            f"PNG skipped: no headless Chrome or Chromium found. Set {CHROME_ENV} to "
            "one to render it; the HTML is complete without it.",
            file=sys.stderr,
        )
        return None
    height = 260 + 58 * rows
    proc = subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--screenshot={png_path}",
            f"--window-size=1760,{height}",
            "--default-background-color=000000",
            html_path.resolve().as_uri(),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0 or not png_path.is_file():
        raise RuntimeError(
            f"Chrome ({chrome}) failed to render {html_path}: {proc.stderr[-300:]}"
        )
    return png_path


def write_report(
    run_dirs: Sequence[Path], out_dir: Path, title: str, png: bool = True
) -> Dict[str, Optional[Path]]:
    rows = collect(run_dirs)
    out_dir.mkdir(parents=True, exist_ok=True)
    md, page = out_dir / "report.md", out_dir / "report.html"
    md.write_text(render_markdown(rows, title), encoding="utf-8")
    page.write_text(render_html(rows, title), encoding="utf-8")
    image = render_png(page, out_dir / "report.png", len(rows)) if png else None
    return {"markdown": md, "html": page, "png": image}
