"""
Inbox Zero Agent

GAIA Python equivalent of OpenClaw's inbox-zero-helper.yaml ClawFlow.
Manages email triage, categorization, archiving, and inbox organization.

Uses real Gmail data from the user's MBOX takeout file.
ClawFlow steps mapped to GAIA Agent tools:
  1. fetch_emails -> fetch_unread_emails tool (reads from real MBOX)
  2. classify -> LLM reasoning in process_query()
  3. group_by_category -> group_by_category tool
  4. archive -> archive_emails tool
  5. draft_response -> LLM reasoning in process_query()
  6. notify -> Final answer from process_query()
"""

from typing import Dict, Any

# Support both package mode and direct execution
try:
    from ..data.email_loader import load_mbox, DEFAULT_MBOX_PATH, count_mbox
except ImportError:
    from gaia_inbox_zero.data.email_loader import load_mbox, DEFAULT_MBOX_PATH, count_mbox

# GAIA base classes - only available when GAIA framework is installed
try:
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import tool
    from gaia.agents.base.console import SilentConsole
    _GAIA_AVAILABLE = True
except ImportError:
    _GAIA_AVAILABLE = False
    Agent = object  # type: ignore
    tool = lambda f: f  # type: ignore
    SilentConsole = None  # type: ignore


class InboxZeroAgent(Agent):  # type: ignore
    """Agent for email triage, categorization, and inbox management.

    Equivalent to OpenClaw's inbox-zero-helper.yaml ClawFlow:
    - Fetches real emails from Gmail MBOX takeout
    - Classifies emails by priority and type
    - Groups emails by category
    - Archives processed emails
    - Generates inbox summary reports

    Requires the GAIA framework to be installed. Install with:
        pip install gaia-inbox-zero[gaia]
    """

    def __init__(
        self,
        mbox_path: str = DEFAULT_MBOX_PATH,
        model_id: str = None,
        base_url: str = None,
        skip_lemonade: bool = False,
        **kwargs,
    ):
        kwargs.setdefault("silent_mode", True)
        self.mbox_path = mbox_path
        self.mbox_count = count_mbox(mbox_path) if mbox_path else 0
        if model_id:
            kwargs["model_id"] = model_id
        if base_url:
            kwargs["base_url"] = base_url
        if skip_lemonade:
            kwargs["skip_lemonade"] = skip_lemonade
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        tools_desc = self._format_tools_for_prompt()
        return f"""You are an Inbox Zero Agent responsible for managing email triage, categorization, and inbox organization.

Data source: Real Gmail takeout ({self.mbox_count} messages available).

Your responsibilities:
1. Fetch emails from the Gmail MBOX and assess their content
2. Classify emails into categories: URGENT, NEEDS_RESPONSE, FYI, PROMOTIONAL, PERSONAL
3. Group similar emails by topic or sender
4. Archive emails that have been processed
5. Generate concise inbox summaries with action items

Available Tools:
{tools_desc}

Response Guidelines:
- Always identify URGENT items first and highlight them
- Prioritize emails from known contacts over automated notifications
- Provide clear action recommendations for each email category
- Keep summaries brief but actionable
- Include email count by category in reports
"""

    def _create_console(self):
        if SilentConsole is not None:
            return SilentConsole()
        # Fallback for environments without GAIA console
        class _SilentConsole:
            def log(self, *args, **kwargs):
                pass
            def error(self, *args, **kwargs):
                pass
        return _SilentConsole()

    def _register_tools(self):
        mbox_path = self.mbox_path

        @tool
        def fetch_unread_emails(limit: int = 20, offset: int = 0, enable_timing: bool = False) -> dict:
            """Fetch emails from the Gmail MBOX takeout.

            Args:
                limit: Maximum number of emails to fetch. Default 20.
                offset: Skip first N emails (for pagination). Default 0.
                enable_timing: If True, track per-email load timing.

            Returns:
                Dictionary with 'emails' list and 'count'.
                Each email has: id, from, to, subject, date, labels, category, body_preview.
                If enable_timing is True, also includes 'timing' dict with per-email times.
            """
            result = load_mbox(path=mbox_path, limit=limit, offset=offset, reverse=True, enable_timing=enable_timing)

            if enable_timing and isinstance(result, tuple):
                emails, timing_data = result
                return {"emails": emails, "count": len(emails), "timing": timing_data, "source": "mbox_takeout"}
            else:
                emails = result
                return {"emails": emails, "count": len(emails), "source": "mbox_takeout"}

        @tool
        def archive_emails(email_ids: list[str]) -> dict:
            """Record emails as archived by their IDs.

            Args:
                email_ids: List of email IDs to archive.

            Returns:
                Dictionary with 'archived' count and 'archived_ids'.
            """
            # In a real implementation this would update Gmail labels.
            # For now, track archived IDs in session state.
            return {
                "archived": len(email_ids),
                "archived_ids": email_ids,
                "status": "success",
            }

        @tool
        def group_by_category(emails: list[dict]) -> dict:
            """Group emails into priority categories based on content analysis.

            Categories: URGENT, NEEDS_RESPONSE, FYI, PROMOTIONAL, PERSONAL

            Args:
                emails: List of email dicts to categorize.

            Returns:
                Dictionary with emails grouped by category.
            """
            categories = {
                "URGENT": [],
                "NEEDS_RESPONSE": [],
                "FYI": [],
                "PROMOTIONAL": [],
                "PERSONAL": [],
            }

            for email in emails:
                subject = email.get("subject", "").lower()
                sender = email.get("from", "").lower()
                labels = [l.lower() for l in email.get("labels", [])]
                pre_category = email.get("category", "")

                # Leverage Gmail's built-in categories as signals
                if "promotions" in labels or pre_category == "promotions":
                    categories["PROMOTIONAL"].append(email["id"])
                elif "social" in labels or pre_category == "social":
                    categories["PERSONAL"].append(email["id"])
                elif "purchases" in labels or pre_category == "purchases":
                    categories["FYI"].append(email["id"])
                elif "security" in pre_category or any(
                    kw in subject for kw in ["security", "password", "unusual login", "account compromised"]
                ):
                    categories["URGENT"].append(email["id"])
                elif any(w in subject for w in ["urgent", "asap", "emergency", "critical"]):
                    categories["URGENT"].append(email["id"])
                elif any(w in subject for w in ["response needed", "action required", "confirm"]):
                    categories["NEEDS_RESPONSE"].append(email["id"])
                elif any(w in sender for w in ["noreply", "no-reply", "auto-confirm", "store-news"]):
                    categories["PROMOTIONAL"].append(email["id"])
                else:
                    categories["FYI"].append(email["id"])

            return {
                "groups": categories,
                "total": sum(len(v) for v in categories.values()),
            }

    def process_in_batches(
        self,
        batch_size: int = 20,
        total_emails: int = 100,
        timeout: int = 1200,
        max_steps: int = 3,
    ) -> Dict[str, Any]:
        """
        Process emails in batches, reusing the run_batch() function from inbox_zero_batch.

        Each batch is a separate LLM call with independent context. The agent instance
        is reused across batches (unlike inbox_zero_batch.py which creates fresh agents).

        Args:
            batch_size: Number of emails per batch (default: 20)
            total_emails: Total number of emails to process (default: 100)
            timeout: Timeout per batch in seconds (default: 1200 = 20 min)
            max_steps: Max reasoning steps per batch (default: 3)

        Returns:
            Dictionary with:
                - batch_results: List of per-batch metrics dicts
                - aggregated: Aggregated totals (tokens, duration, etc.)
                - per_batch_metrics: List of BatchMetrics dataclasses
        """
        import time
        from pathlib import Path

        # Import run_batch locally to avoid circular import
        try:
            from ..cli.batch_classifier import run_batch, generate_run_id
        except ImportError:
            from gaia_inbox_zero.cli.batch_classifier import run_batch, generate_run_id

        # Validate batch_size
        if batch_size < 1 or batch_size > 100:
            raise ValueError(f"batch_size must be between 1 and 100, got {batch_size}")

        # Verify MBOX file exists and has emails
        if not Path(self.mbox_path).exists():
            raise FileNotFoundError(f"MBOX file not found: {self.mbox_path}")

        mbox_count = count_mbox(self.mbox_path)
        if mbox_count == 0:
            raise ValueError(f"MBOX file is empty: {self.mbox_path}")

        # Calculate number of batches
        total_batches = (min(total_emails, mbox_count) + batch_size - 1) // batch_size

        batch_results = []
        per_batch_metrics = []

        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        total_duration_ms = 0
        total_steps = 0
        all_categories = set()

        run_id = generate_run_id(self.model_id or "unknown", "inbox-zero-batch")

        for i in range(total_batches):
            batch_num = i + 1
            offset = i * batch_size

            # Fetch emails for this batch
            result = load_mbox(path=self.mbox_path, limit=batch_size, offset=offset, reverse=True)
            if isinstance(result, tuple):
                emails, _ = result
            else:
                emails = result

            if not emails:
                # No more emails - stop early (partial batch handled)
                break

            # Reuse run_batch() from batch_classifier
            batch_result = run_batch(
                agent=self,  # Reuse same agent instance across batches
                emails=emails,
                batch_num=batch_num,
                total_batches=total_batches,
                model=self.model_id or "unknown",
                run_id=run_id,
                mbox_path=self.mbox_path,
                provider="lemonade",
                timeout=timeout,
            )

            # Build batch metrics dict (align with batch_classifier field naming)
            batch_metrics = {
                "batch_num": batch_result["batch_num"],
                "total_batches": batch_result["total_batches"],
                "email_count": batch_result["email_count"],
                "est_tokens": batch_result["est_tokens"],
                "duration_ms": batch_result["duration_ms"],
                "duration_min": batch_result["duration_min"],
                "input_tokens": batch_result["input_tokens"],
                "output_tokens": batch_result["output_tokens"],
                "total_tokens": batch_result["total_tokens"],
                "steps": batch_result["steps"],
                "categories": batch_result["categories"],
                "status": batch_result["status"],
            }

            batch_results.append(batch_metrics)

            # Import BatchMetrics if available
            try:
                from ..schema.result_schema import BatchMetrics as _BatchMetrics
                per_batch_metrics.append(_BatchMetrics(
                    batch_num=batch_result["batch_num"],
                    total_batches=batch_result["total_batches"],
                    email_count=batch_result["email_count"],
                    est_tokens=batch_result["est_tokens"],
                    duration_ms=batch_result["duration_ms"],
                    duration_min=batch_result["duration_min"],
                    input_tokens=batch_result["input_tokens"],
                    output_tokens=batch_result["output_tokens"],
                    total_tokens=batch_result["total_tokens"],
                    steps=batch_result["steps"],
                    categories=batch_result["categories"],
                    status=batch_result["status"],
                ))
            except ImportError:
                # result_schema not available - skip dataclass creation
                pass

            # Aggregate totals
            total_input_tokens += batch_result["input_tokens"]
            total_output_tokens += batch_result["output_tokens"]
            total_tokens += batch_result["total_tokens"]
            total_duration_ms += batch_result["duration_ms"]
            total_steps += batch_result["steps"]

            # Extract categories for aggregation
            if batch_result["categories"]:
                for cat in batch_result["categories"].split(", "):
                    if cat.strip():
                        all_categories.add(cat.strip())

        # Build aggregated metrics
        aggregated = {
            "total_emails_processed": sum(b["email_count"] for b in batch_results),
            "total_batches": len(batch_results),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "total_steps": total_steps,
            "total_duration_ms": total_duration_ms,
            "total_duration_min": round(total_duration_ms / 60000, 2),
            "all_categories": sorted(all_categories),
            "batch_size": batch_size,
            "timeout_per_batch": timeout,
            "max_steps_per_batch": max_steps,
        }

        return {
            "batch_results": batch_results,
            "aggregated": aggregated,
            "per_batch_metrics": per_batch_metrics,
        }
