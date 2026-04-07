"""
Metrics exporters for GAIA observability.

This module provides exporters for metrics in various formats:
- Prometheus text format
- Console output for debugging

Example:
    >>> from gaia.observability.metrics import MetricsCollector
    >>> from gaia.observability.exporters import PrometheusExporter
    >>>
    >>> metrics = MetricsCollector()
    >>> metrics.counter("requests").inc(5)
    >>>
    >>> exporter = PrometheusExporter()
    >>> output = exporter.export(metrics)
    >>> print(output)
"""

from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .metrics import MetricsCollector, Counter, Gauge, Histogram


class PrometheusExporter:
    """
    Export metrics in Prometheus text exposition format.

    Format specification:
    https://github.com/prometheus/docs/blob/main/content/docs/instrumenting/exposition_formats.md

    Example:
        >>> from gaia.observability.metrics import MetricsCollector
        >>>
        >>> metrics = MetricsCollector(prefix="gaia")
        >>> metrics.counter("requests").inc(5)
        >>>
        >>> exporter = PrometheusExporter()
        >>> output = exporter.export(metrics)
    """

    def __init__(self, prefix: str = "gaia") -> None:
        """
        Initialize Prometheus exporter.

        Args:
            prefix: Metric name prefix (default: "gaia")

        Example:
            >>> exporter = PrometheusExporter(prefix="myapp")
        """
        self.prefix = prefix

    def export(self, collector: "MetricsCollector") -> str:
        """
        Export all metrics to Prometheus format.

        Args:
            collector: MetricsCollector instance

        Returns:
            Prometheus-formatted metrics string

        Example:
            >>> metrics = MetricsCollector()
            >>> metrics.counter("requests").inc(5)
            >>> exporter = PrometheusExporter()
            >>> output = exporter.export(metrics)
            >>> "# HELP gaia_requests" in output
            True
        """
        return collector.to_prometheus()

    def _format_counter(self, name: str, counter: "Counter") -> str:
        """
        Format counter metric.

        Args:
            name: Metric name
            counter: Counter instance

        Returns:
            Prometheus-formatted counter string
        """
        lines = [
            f"# HELP {name} {counter.description}",
            f"# TYPE {name} counter",
        ]

        for key, value in counter.get_all().items():
            if key:
                lines.append(f'{name}{{{key}}} {value}')
            else:
                lines.append(f"{name} {value}")

        return "\n".join(lines)

    def _format_gauge(self, name: str, gauge: "Gauge") -> str:
        """
        Format gauge metric.

        Args:
            name: Metric name
            gauge: Gauge instance

        Returns:
            Prometheus-formatted gauge string
        """
        lines = [
            f"# HELP {name} {gauge.description}",
            f"# TYPE {name} gauge",
        ]

        for key, value in gauge.get_all().items():
            if key:
                lines.append(f'{name}{{{key}}} {value}')
            else:
                lines.append(f"{name} {value}")

        return "\n".join(lines)

    def _format_histogram(self, name: str, histogram: "Histogram") -> str:
        """
        Format histogram metric.

        Args:
            name: Metric name
            histogram: Histogram instance

        Returns:
            Prometheus-formatted histogram string
        """
        lines = [
            f"# HELP {name} {histogram.description}",
            f"# TYPE {name} histogram",
        ]

        for key, summary in histogram.get_all_summaries().items():
            # Output bucket counts
            for bucket, count in sorted(
                summary["buckets"].items(),
                key=lambda x: float(x[0]) if x[0] != "+Inf" else float("inf")
            ):
                label_part = f'{{{key},le="{bucket}"}}' if key else f'{{le="{bucket}"}}'
                lines.append(f"{name}_bucket{label_part} {count}")

            # Output +Inf bucket
            label_part = f'{{{key},le="+Inf"}}' if key else f'{{le="+Inf"}}'
            lines.append(f"{name}_bucket{label_part} {summary['count']}")

            # Output sum and count
            if key:
                lines.append(f'{name}_sum{{{key}}} {summary["sum"]}')
                lines.append(f'{name}_count{{{key}}} {summary["count"]}')
            else:
                lines.append(f"{name}_sum {summary['sum']}")
                lines.append(f"{name}_count {summary['count']}")

        return "\n".join(lines)

    def _format_labels(self, labels: Dict[str, str]) -> str:
        """
        Format labels as {key="value", ...}.

        Args:
            labels: Label key-value pairs

        Returns:
            Formatted labels string

        Example:
            >>> exporter = PrometheusExporter()
            >>> exporter._format_labels({"method": "GET", "status": "200"})
            'method="GET",status="200"'
        """
        if not labels:
            return ""

        sorted_labels = sorted(labels.items())
        return ",".join(f'{k}="{v}"' for k, v in sorted_labels)


class ConsoleExporter:
    """
    Export metrics to console for debugging.

    Example:
        >>> from gaia.observability.metrics import MetricsCollector
        >>> from gaia.observability.exporters import ConsoleExporter
        >>>
        >>> metrics = MetricsCollector()
        >>> metrics.counter("requests").inc(5)
        >>>
        >>> exporter = ConsoleExporter()
        >>> exporter.export(metrics)  # Prints to console
    """

    def __init__(self, show_timestamp: bool = True) -> None:
        """
        Initialize console exporter.

        Args:
            show_timestamp: Show export timestamp

        Example:
            >>> exporter = ConsoleExporter(show_timestamp=False)
        """
        self.show_timestamp = show_timestamp

    def export(self, collector: "MetricsCollector") -> str:
        """
        Export metrics to formatted console output.

        Args:
            collector: MetricsCollector instance

        Returns:
            Formatted metrics string for console display
        """
        import time
        from datetime import datetime

        lines = []

        if self.show_timestamp:
            lines.append(f"Metrics exported at: {datetime.now().isoformat()}")
            lines.append("=" * 60)

        summary = collector.get_summary()

        # Counters
        if summary["counters"]:
            lines.append("\nCOUNTERS:")
            lines.append("-" * 40)
            for name, values in summary["counters"].items():
                for label_key, value in values.items():
                    if label_key:
                        lines.append(f"  {name}{{{label_key}}} = {value}")
                    else:
                        lines.append(f"  {name} = {value}")

        # Gauges
        if summary["gauges"]:
            lines.append("\nGAUGES:")
            lines.append("-" * 40)
            for name, values in summary["gauges"].items():
                for label_key, value in values.items():
                    if label_key:
                        lines.append(f"  {name}{{{label_key}}} = {value:.2f}")
                    else:
                        lines.append(f"  {name} = {value:.2f}")

        # Histograms
        if summary["histograms"]:
            lines.append("\nHISTOGRAMS:")
            lines.append("-" * 40)
            for name, summaries in summary["histograms"].items():
                for label_key, data in summaries.items():
                    prefix = f"{name}{{{label_key}}}" if label_key else name
                    lines.append(f"  {prefix}:")
                    lines.append(f"    count = {data['count']}")
                    lines.append(f"    sum = {data['sum']:.4f}")
                    if data["count"] > 0:
                        lines.append(f"    avg = {data['sum']/data['count']:.4f}")

        return "\n".join(lines)

    def print(self, collector: "MetricsCollector") -> None:
        """
        Print metrics directly to console.

        Args:
            collector: MetricsCollector instance
        """
        print(self.export(collector))


def get_exporter(format: str = "prometheus", **kwargs) -> Any:
    """
    Get an exporter by format name.

    Args:
        format: Exporter format ("prometheus" or "console")
        **kwargs: Additional arguments for exporter initialization

    Returns:
        Exporter instance

    Raises:
        ValueError: If format is not recognized

    Example:
        >>> exporter = get_exporter("prometheus")
        >>> isinstance(exporter, PrometheusExporter)
        True
    """
    exporters = {
        "prometheus": PrometheusExporter,
        "console": ConsoleExporter,
    }

    if format not in exporters:
        raise ValueError(f"Unknown exporter format: {format}. Available: {list(exporters.keys())}")

    return exporters[format](**kwargs)
