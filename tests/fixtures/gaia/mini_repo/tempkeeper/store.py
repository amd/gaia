"""In-memory collection of temperature readings."""

from __future__ import annotations

import statistics


class ReadingStore:
    """Collects temperature readings and reports summary statistics."""

    def __init__(self) -> None:
        self._readings: list[float] = []

    def add(self, reading: float) -> None:
        """Record one reading."""
        self._readings.append(float(reading))

    def median(self) -> float:
        """Median of all recorded readings.

        Raises ValueError when the store is empty — there is no median of
        nothing, and returning a placeholder would poison downstream math.
        """
        if not self._readings:
            raise ValueError("ReadingStore is empty; add readings before median()")
        return statistics.median(self._readings)
