# widgetlib (eval fixture)

A deliberately tiny Python project for gaia-agent code-index eval scenarios.
Function names are distinctive on purpose — a correct semantic-search answer
names the exact symbol, and there is exactly one right answer per query:

| Query intent | Correct symbol | File |
|---|---|---|
| reorder threshold for stock | `calculate_reorder_threshold` | `widgetlib/inventory.py` |
| merge duplicate SKUs | `merge_duplicate_skus` | `widgetlib/inventory.py` |
| seasonal price discount | `apply_seasonal_discount` | `widgetlib/pricing.py` |
| currency conversion with rounding | `convert_currency_rounded` | `widgetlib/pricing.py` |
| quarterly summary rendering | `render_quarterly_summary` | `widgetlib/reports.py` |

There is intentionally **no** function for tax calculation, so "find the
function that computes sales tax" has the honest answer "it does not exist"
(honest-miss scenarios).
