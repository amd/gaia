"""Temperature unit conversions."""

from __future__ import annotations


def celsius_to_fahrenheit(c: float) -> float:
    """Convert degrees Celsius to degrees Fahrenheit."""
    return c * 9.0 / 5.0 + 32.0


def fahrenheit_to_celsius(f: float) -> float:
    """Convert degrees Fahrenheit to degrees Celsius."""
    return (f - 32.0) * 5.0 / 9.0
