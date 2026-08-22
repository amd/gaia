"""Reading temperature data from disk."""

from __future__ import annotations

import csv
from pathlib import Path


def load_readings(path: str | Path) -> list[float]:
    """Parse a CSV of temperature readings.

    Expects a header row with a ``temperature`` column; every value is
    returned as a float, in file order. Raises ValueError when the column is
    missing or a value is not numeric.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "temperature" not in reader.fieldnames:
            raise ValueError(f"{path}: expected a 'temperature' column")
        readings = []
        for line_number, row in enumerate(reader, start=2):
            raw = row["temperature"]
            try:
                readings.append(float(raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}:{line_number}: not a number: {raw!r}"
                ) from exc
    return readings
