# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Sample data processing module for testing file analysis capabilities.
Provides utilities for loading, transforming, and summarizing tabular data.
"""

import csv
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class DataLoader:
    """Loads and validates data from CSV files."""

    SUPPORTED_FORMATS = (".csv", ".tsv", ".txt")

    def __init__(self, base_path: str, encoding: str = "utf-8"):
        self.base_path = base_path
        self.encoding = encoding
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        # TODO: Add support for Excel (.xlsx) file loading
        self._validators: List[callable] = []

    def load_csv(self, filename: str) -> List[Dict[str, Any]]:
        """Load a CSV file and return rows as list of dicts."""
        filepath = os.path.join(self.base_path, filename)

        if filepath in self._cache:
            return self._cache[filepath]

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Data file not found: {filepath}")

        ext = os.path.splitext(filename)[1].lower()
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {ext}")

        rows = []
        with open(filepath, "r", encoding=self.encoding) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # TODO: Implement row-level validation using self._validators
                rows.append(dict(row))

        self._cache[filepath] = rows
        return rows

    def clear_cache(self) -> None:
        """Clear the internal file cache."""
        self._cache.clear()

    def get_column_names(self, filename: str) -> List[str]:
        """Return column headers from a CSV file without loading all data."""
        filepath = os.path.join(self.base_path, filename)
        with open(filepath, "r", encoding=self.encoding) as f:
            reader = csv.reader(f)
            headers = next(reader)
        return headers

    def register_validator(self, validator: callable) -> None:
        """Register a validation function to apply during loading."""
        self._validators.append(validator)


class DataTransformer:
    """Applies transformations and filters to loaded datasets."""

    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data
        self._transform_log: List[str] = []

    def filter_rows(
        self, column: str, value: Any, operator: str = "eq"
    ) -> "DataTransformer":
        """Filter rows based on column value comparison.

        Args:
            column: Column name to filter on.
            value: Value to compare against.
            operator: One of 'eq', 'gt', 'lt', 'gte', 'lte', 'contains'.

        Returns:
            Self for method chaining.
        """
        ops = {
            "eq": lambda a, b: a == b,
            "gt": lambda a, b: float(a) > float(b),
            "lt": lambda a, b: float(a) < float(b),
            "gte": lambda a, b: float(a) >= float(b),
            "lte": lambda a, b: float(a) <= float(b),
            "contains": lambda a, b: str(b).lower() in str(a).lower(),
        }

        if operator not in ops:
            raise ValueError(f"Unknown operator: {operator}")

        # TODO: Add 'not_eq' and 'regex' operators for more flexible filtering
        self.data = [
            row for row in self.data if ops[operator](row.get(column, ""), value)
        ]
        self._transform_log.append(f"filter({column} {operator} {value})")
        return self

    def sort_by(self, column: str, descending: bool = False) -> "DataTransformer":
        """Sort dataset by the given column."""

        def sort_key(row):
            val = row.get(column, "")
            try:
                return float(val)
            except (ValueError, TypeError):
                return val

        self.data = sorted(self.data, key=sort_key, reverse=descending)
        self._transform_log.append(f"sort({column}, desc={descending})")
        return self

    def select_columns(self, columns: List[str]) -> "DataTransformer":
        """Keep only the specified columns in each row."""
        self.data = [{col: row.get(col) for col in columns} for row in self.data]
        self._transform_log.append(f"select({columns})")
        return self

    def add_computed_column(self, name: str, formula: callable) -> "DataTransformer":
        """Add a new column computed from existing row data."""
        for row in self.data:
            row[name] = formula(row)
        self._transform_log.append(f"computed({name})")
        return self

    def get_transform_history(self) -> List[str]:
        """Return the log of all transformations applied."""
        return list(self._transform_log)

    def to_list(self) -> List[Dict[str, Any]]:
        """Return the current dataset as a list of dicts."""
        return self.data


class StatsSummarizer:
    """Computes summary statistics over numeric columns."""

    def __init__(self, data: List[Dict[str, Any]]):
        self.data = data
        # TODO: Support weighted averages for more accurate aggregations
        self._numeric_columns: Optional[List[str]] = None

    def _detect_numeric_columns(self) -> List[str]:
        """Auto-detect columns that contain numeric data."""
        if self._numeric_columns is not None:
            return self._numeric_columns

        if not self.data:
            return []

        numeric_cols = []
        sample_row = self.data[0]
        for col, val in sample_row.items():
            try:
                float(val)
                numeric_cols.append(col)
            except (ValueError, TypeError):
                continue

        self._numeric_columns = numeric_cols
        return numeric_cols

    def mean(self, column: str) -> float:
        """Calculate the arithmetic mean of a numeric column."""
        values = self._extract_numeric(column)
        if not values:
            return 0.0
        return sum(values) / len(values)

    def median(self, column: str) -> float:
        """Calculate the median of a numeric column."""
        values = sorted(self._extract_numeric(column))
        n = len(values)
        if n == 0:
            return 0.0
        mid = n // 2
        if n % 2 == 0:
            return (values[mid - 1] + values[mid]) / 2
        return values[mid]

    def std_dev(self, column: str) -> float:
        """Calculate the standard deviation of a numeric column."""
        values = self._extract_numeric(column)
        if len(values) < 2:
            return 0.0
        avg = sum(values) / len(values)
        variance = sum((x - avg) ** 2 for x in values) / (len(values) - 1)
        return variance**0.5

    def summary(self, column: str) -> Dict[str, float]:
        """Return a full statistical summary for a column."""
        values = self._extract_numeric(column)
        if not values:
            return {"count": 0, "mean": 0, "median": 0, "std": 0, "min": 0, "max": 0}

        return {
            "count": len(values),
            "mean": self.mean(column),
            "median": self.median(column),
            "std": self.std_dev(column),
            "min": min(values),
            "max": max(values),
        }

    def group_summary(
        self, group_column: str, value_column: str
    ) -> Dict[str, Dict[str, float]]:
        """Compute summary stats grouped by a categorical column."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.data:
            key = row.get(group_column, "Unknown")
            if key not in groups:
                groups[key] = []
            groups[key].append(row)

        result = {}
        for key, group_rows in groups.items():
            group_stats = StatsSummarizer(group_rows)
            result[key] = group_stats.summary(value_column)

        return result

    def _extract_numeric(self, column: str) -> List[float]:
        """Extract numeric values from a column, skipping non-numeric entries."""
        values = []
        for row in self.data:
            try:
                values.append(float(row[column]))
            except (ValueError, TypeError, KeyError):
                continue
        return values

    def detect_outliers(
        self, column: str, threshold: float = 2.0
    ) -> List[Tuple[int, float]]:
        """Find values that are more than threshold std deviations from mean.

        Args:
            column: Numeric column to analyze.
            threshold: Number of standard deviations for outlier cutoff.

        Returns:
            List of (row_index, value) tuples for detected outliers.
        """
        values = self._extract_numeric(column)
        if len(values) < 3:
            return []

        avg = self.mean(column)
        sd = self.std_dev(column)
        if sd == 0:
            return []

        outliers = []
        for i, row in enumerate(self.data):
            try:
                val = float(row[column])
                if abs(val - avg) > threshold * sd:
                    outliers.append((i, val))
            except (ValueError, TypeError, KeyError):
                continue

        # TODO: Implement IQR-based outlier detection as an alternative method
        return outliers


def load_and_summarize(filepath: str, target_column: str) -> Dict[str, Any]:
    """Convenience function: load a CSV file and return summary stats.

    Args:
        filepath: Path to the CSV file.
        target_column: Numeric column to summarize.

    Returns:
        Dictionary containing file info and column statistics.
    """
    base_dir = os.path.dirname(filepath)
    filename = os.path.basename(filepath)

    loader = DataLoader(base_dir)
    data = loader.load_csv(filename)

    stats = StatsSummarizer(data)
    column_summary = stats.summary(target_column)

    return {
        "file": filename,
        "total_rows": len(data),
        "columns": loader.get_column_names(filename),
        "target_column": target_column,
        "statistics": column_summary,
    }


def parse_date_column(
    data: List[Dict[str, Any]],
    column: str,
    fmt: str = "%Y-%m-%d",
) -> List[Dict[str, Any]]:
    """Parse a string date column into datetime objects.

    Args:
        data: List of row dictionaries.
        column: Name of the date column.
        fmt: strftime format string.

    Returns:
        Data with the date column converted to datetime objects.
    """
    for row in data:
        if column in row and isinstance(row[column], str):
            try:
                row[column] = datetime.strptime(row[column], fmt)
            except ValueError:
                row[column] = None
    return data
