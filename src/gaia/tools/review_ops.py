# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Review Operations Tool for Quality Supervisor Agent.

This module provides tools for aggregating quality reviews, retrieving
review history, and integrating with Chronicle via NexusService.

Features:
    - Thread-safe review consensus aggregation
    - Chronicle digest retrieval via NexusService
    - Review history tracking and analysis
    - Quality decision support

Example:
    >>> from gaia.tools.review_ops import review_consensus, get_review_history
    >>> result = review_consensus(
    ...     reviews=[{"score": 85, "defects": []}, {"score": 90, "defects": ["minor"]}],
    ...     min_consensus=0.75
    ... )
    >>> print(result["consensus_score"])
"""

import copy
import hashlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.tools import tool
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


# Thread-safe review history storage
_review_history_lock = threading.RLock()
_review_history: List[Dict[str, Any]] = []
_review_history_max_size = 1000


@dataclass
class ReviewRecord:
    """Record of a quality review.

    Attributes:
        review_id: Unique review identifier
        timestamp: When review was conducted
        quality_score: Overall quality score (0-100)
        validator_feedback: List of validator feedback items
        defects: List of identified defects
        agent_id: ID of agent that produced reviewed artifact
        phase: Pipeline phase when review occurred
        loop_id: Loop iteration ID
        consensus_score: Aggregated consensus score if applicable
    """
    review_id: str
    timestamp: datetime
    quality_score: float
    validator_feedback: List[Dict[str, Any]]
    defects: List[Dict[str, Any]]
    agent_id: str
    phase: str
    loop_id: Optional[str] = None
    consensus_score: Optional[float] = None


@tool
def review_consensus(
    reviews: List[Dict[str, Any]],
    min_consensus: float = 0.75,
    weighting: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Aggregate multiple quality reviews into consensus decision.

    This tool analyzes multiple quality reviews and determines if there
    is sufficient consensus for a quality decision. It uses weighted
    averaging when weights are provided, otherwise uses simple averaging.

    Args:
        reviews: List of review dictionaries, each containing:
            - score: Quality score (0-100)
            - defects: List of defects found
            - validator_id: ID of validator (optional)
            - feedback: Text feedback (optional)
        min_consensus: Minimum consensus threshold (0-1) for agreement.
            Reviews within 20% of mean are considered "in agreement".
        weighting: Optional dict mapping validator_id to weight (0-1).
            If None, uses equal weighting.

    Returns:
        Dictionary containing:
            - status: "success" or "error"
            - consensus_score: Aggregated score (0-100)
            - consensus_reached: Boolean indicating if consensus met
            - agreement_ratio: Ratio of reviews in agreement (0-1)
            - weighted_score: Weighted average score
            - defect_summary: Aggregated defect information
            - recommendations: List of recommendations

    Example:
        >>> reviews = [
        ...     {"score": 85, "defects": ["minor-style"], "validator_id": "v1"},
        ...     {"score": 88, "defects": [], "validator_id": "v2"},
        ...     {"score": 82, "defects": ["missing-docstring"], "validator_id": "v3"},
        ... ]
        >>> result = review_consensus(reviews, min_consensus=0.75)
        >>> print(result["consensus_reached"])
        True
    """
    try:
        if not reviews:
            return {
                "status": "error",
                "error": "No reviews provided",
                "consensus_score": 0.0,
                "consensus_reached": False,
            }

        # Extract scores
        scores = []
        for review in reviews:
            score = review.get("score")
            if score is not None:
                scores.append(float(score))

        if not scores:
            return {
                "status": "error",
                "error": "No valid scores in reviews",
                "consensus_score": 0.0,
                "consensus_reached": False,
            }

        # Calculate weighted or simple average
        if weighting:
            weights = []
            weighted_scores = []
            for review in reviews:
                validator_id = review.get("validator_id", "default")
                weight = weighting.get(validator_id, 0.5)  # Default weight 0.5
                score = review.get("score", 0)
                weights.append(weight)
                weighted_scores.append(score * weight)

            total_weight = sum(weights)
            if total_weight > 0:
                weighted_score = sum(weighted_scores) / total_weight
            else:
                weighted_score = sum(scores) / len(scores)
        else:
            weighted_score = sum(scores) / len(scores)

        # Calculate agreement ratio
        # Reviews within 20% of mean are considered "in agreement"
        mean_score = weighted_score
        agreement_threshold = mean_score * 0.2  # 20% tolerance
        agreeing_reviews = sum(
            1 for score in scores
            if abs(score - mean_score) <= agreement_threshold
        )
        agreement_ratio = agreeing_reviews / len(scores)

        # Determine if consensus reached
        consensus_reached = agreement_ratio >= min_consensus

        # Aggregate defects
        all_defects = []
        defect_counts = {}
        for review in reviews:
            for defect in review.get("defects", []):
                defect_key = defect if isinstance(defect, str) else defect.get("description", "unknown")
                if defect_key not in defect_counts:
                    defect_counts[defect_key] = 0
                    all_defects.append({
                        "description": defect_key,
                        "occurrence_count": 0,
                        "severity": defect.get("severity", "medium") if isinstance(defect, dict) else "medium",
                    })
                defect_counts[defect_key] += 1

        # Update occurrence counts
        for defect in all_defects:
            defect["occurrence_count"] = defect_counts[defect["description"]]
            defect["agreement_ratio"] = defect["occurrence_count"] / len(reviews)

        # Generate recommendations
        recommendations = []
        if consensus_reached:
            if weighted_score >= 90:
                recommendations.append("Quality is excellent, proceed to next phase")
            elif weighted_score >= 75:
                recommendations.append("Quality is acceptable, consider minor improvements")
            else:
                recommendations.append("Quality needs improvement, recommend loop back")
        else:
            recommendations.append("Insufficient consensus, additional review recommended")

        if all_defects:
            high_agreement_defects = [d for d in all_defects if d["agreement_ratio"] >= 0.5]
            if high_agreement_defects:
                recommendations.append(
                    f"Address {len(high_agreement_defects)} high-agreement defects before proceeding"
                )

        # Build result
        result = {
            "status": "success",
            "consensus_score": round(weighted_score, 2),
            "consensus_reached": consensus_reached,
            "agreement_ratio": round(agreement_ratio, 2),
            "weighted_score": round(weighted_score, 2),
            "defect_summary": {
                "total_unique_defects": len(all_defects),
                "high_agreement_defects": len([d for d in all_defects if d["agreement_ratio"] >= 0.5]),
                "defects": all_defects,
            },
            "recommendations": recommendations,
            "metadata": {
                "review_count": len(reviews),
                "score_range": {
                    "min": min(scores),
                    "max": max(scores),
                    "std_dev": _calculate_std_dev(scores),
                },
            },
        }

        # Record to history
        _record_review_to_history(result)

        return result

    except Exception as exc:
        logger.exception(f"review_consensus failed: {exc}")
        return {
            "status": "error",
            "error": str(exc),
            "consensus_score": 0.0,
            "consensus_reached": False,
        }


@tool
def get_review_history(
    agent_id: Optional[str] = None,
    phase: Optional[str] = None,
    limit: int = 50,
    include_defects: bool = True,
) -> Dict[str, Any]:
    """Retrieve past quality decisions and reviews.

    This tool retrieves historical review data for analysis and
    trend identification. Supports filtering by agent and phase.

    Args:
        agent_id: Optional agent ID to filter by
        phase: Optional phase name to filter by (e.g., "QUALITY", "DECISION")
        limit: Maximum number of records to return (default: 50)
        include_defects: Whether to include defect details (default: True)

    Returns:
        Dictionary containing:
            - status: "success" or "error"
            - history: List of review records
            - total_count: Total matching records
            - statistics: Aggregate statistics

    Example:
        >>> history = get_review_history(phase="QUALITY", limit=10)
        >>> print(history["total_count"])
        10
    """
    try:
        with _review_history_lock:
            # Filter history
            filtered = _review_history.copy()

            if agent_id:
                filtered = [r for r in filtered if r.get("agent_id") == agent_id]

            if phase:
                filtered = [r for r in filtered if r.get("phase") == phase]

            # Apply limit (most recent first)
            filtered = list(reversed(filtered[-limit:]))

            # Optionally remove defect details for lightweight queries
            if not include_defects:
                for record in filtered:
                    if "defects" in record:
                        record["defects"] = [{"summary": "defect present"} for _ in record["defects"]]

            # Calculate statistics
            scores = [r.get("consensus_score", 0) for r in filtered if r.get("consensus_score")]
            stats = {
                "total_reviews": len(filtered),
                "average_score": sum(scores) / len(scores) if scores else 0,
                "min_score": min(scores) if scores else 0,
                "max_score": max(scores) if scores else 0,
            }

            return {
                "status": "success",
                "history": filtered,
                "total_count": len(filtered),
                "statistics": stats,
            }

    except Exception as exc:
        logger.exception(f"get_review_history failed: {exc}")
        return {
            "status": "error",
            "error": str(exc),
            "history": [],
            "total_count": 0,
        }


@tool
def get_chronicle_digest(
    max_events: int = 15,
    max_tokens: int = 3500,
    include_phases: Optional[List[str]] = None,
    include_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Retrieve Chronicle digest from NexusService.

    This tool delegates to NexusService.get_chronicle_digest() to
    retrieve a token-efficient summary of recent pipeline events.
    Useful for quality review context.

    Args:
        max_events: Maximum number of recent events (default: 15)
        max_tokens: Target maximum token count (default: 3500)
        include_phases: Filter to specific phases if specified
        include_agents: Filter to specific agents if specified

    Returns:
        Dictionary containing:
            - status: "success" or "error"
            - digest: Formatted string digest
            - event_count: Number of events included
            - phases_covered: List of phases in digest

    Example:
        >>> result = get_chronicle_digest(include_phases=["QUALITY", "DECISION"])
        >>> print(result["digest"][:200])
        '## Recent Events...'
    """
    try:
        from gaia.state.nexus import NexusService

        nexus = NexusService.get_instance()
        digest = nexus.get_chronicle_digest(
            max_events=max_events,
            max_tokens=max_tokens,
            include_phases=include_phases,
            include_agents=include_agents,
        )

        # Parse digest for metadata
        lines = digest.split("\n") if digest else []
        event_count = sum(1 for line in lines if line.strip().startswith("["))
        phases = set()
        for line in lines:
            if line.strip().startswith("[") and "]" in line:
                phase = line.split("]")[0].replace("[", "").strip()
                if phase and phase != "N/A":
                    phases.add(phase)

        return {
            "status": "success",
            "digest": digest,
            "event_count": event_count,
            "phases_covered": list(phases),
            "tokens_estimated": len(digest) // 4 if digest else 0,
        }

    except ImportError:
        return {
            "status": "error",
            "error": "NexusService not available",
            "digest": "",
            "event_count": 0,
        }
    except Exception as exc:
        logger.exception(f"get_chronicle_digest failed: {exc}")
        return {
            "status": "error",
            "error": str(exc),
            "digest": "",
            "event_count": 0,
        }


@tool
def workspace_validate() -> Dict[str, Any]:
    """Validate current workspace state.

    This tool retrieves the current workspace index from NexusService
    and validates file integrity.

    Returns:
        Dictionary containing:
            - status: "success" or "error"
            - workspace: Workspace index snapshot
            - validation: Validation results
            - file_count: Number of files tracked

    Example:
        >>> result = workspace_validate()
        >>> print(result["file_count"])
    """
    try:
        from gaia.state.nexus import NexusService

        nexus = NexusService.get_instance()
        snapshot = nexus.get_snapshot()
        workspace = snapshot.get("workspace", {})
        files = workspace.get("files", {})

        # Basic validation
        validation = {
            "valid": True,
            "issues": [],
            "files_validated": len(files),
        }

        # Check for path traversal attempts
        for path in files.keys():
            if ".." in path or path.startswith("/"):
                validation["valid"] = False
                validation["issues"].append(f"Path traversal detected: {path}")

        return {
            "status": "success",
            "workspace": workspace,
            "validation": validation,
            "file_count": len(files),
            "version": workspace.get("version", 0),
        }

    except ImportError:
        return {
            "status": "error",
            "error": "NexusService not available",
            "workspace": {},
            "validation": {"valid": False, "issues": ["NexusService unavailable"]},
            "file_count": 0,
        }
    except Exception as exc:
        logger.exception(f"workspace_validate failed: {exc}")
        return {
            "status": "error",
            "error": str(exc),
            "workspace": {},
            "validation": {"valid": False, "issues": [str(exc)]},
            "file_count": 0,
        }


# Internal helper functions

def _calculate_std_dev(values: List[float]) -> float:
    """Calculate standard deviation of a list of values.

    Args:
        values: List of numeric values

    Returns:
        Standard deviation
    """
    if len(values) < 2:
        return 0.0

    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return variance ** 0.5


def _record_review_to_history(result: Dict[str, Any]) -> None:
    """Record review result to history (thread-safe).

    Args:
        result: Review result dictionary
    """
    global _review_history

    with _review_history_lock:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "consensus_score": result.get("consensus_score"),
            "consensus_reached": result.get("consensus_reached"),
            "review_count": result.get("metadata", {}).get("review_count", 0),
            "defect_count": result.get("defect_summary", {}).get("total_unique_defects", 0),
        }

        _review_history.append(record)

        # Enforce max size
        if len(_review_history) > _review_history_max_size:
            _review_history = _review_history[-_review_history_max_size:]


def clear_review_history() -> None:
    """Clear review history (for testing).

    Thread-safe history reset.
    """
    global _review_history

    with _review_history_lock:
        _review_history.clear()


def get_review_history_count() -> int:
    """Get current review history count.

    Returns:
        Number of records in history
    """
    with _review_history_lock:
        return len(_review_history)
