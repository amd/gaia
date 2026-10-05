"""Monthly order report. Needs the internal acme-widgetstats package."""

import csv
from pathlib import Path

import acme_widgetstats  # internal package; see requirements.txt

with (Path(__file__).parent / "orders.csv").open(newline="") as fh:
    rows = list(csv.DictReader(fh))
print(acme_widgetstats.monthly_summary(rows))
