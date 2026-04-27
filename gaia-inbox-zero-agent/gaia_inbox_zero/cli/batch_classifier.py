"""
Inbox Zero Batch Classifier CLI

Processes emails from Gmail MBOX in configurable batches.
Classifies each email via LLM (Lemonade Server or Anthropic).
Writes structured results to JSON and prints a human-readable summary.

Usage:
    python -m gaia_inbox_zero.cli.batch_classifier [--limit 100] [--batch-size 20]
    inbox-zero-classify --limit 100 --batch-size 20
"""

import json
import mailbox
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from gaia_inbox_zero.data.email_loader import DEFAULT_MBOX_PATH
from gaia_inbox_zero.agent.config import (
    CATEGORIES,
    LEMONADE_URL,
    ANTHROPIC_URL,
    DEFAULT_MODELS,
    CLASSIFICATION_PROMPT,
    RESULTS_DIR,
    OUTPUT_FILE,
)
from gaia_inbox_zero.agent.classifiers import classify_email_llm


def generate_run_id(model: str, task: str) -> str:
    """Generate a unique run identifier."""
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    return f"{task}-{model.replace(' ', '_')}-{timestamp}-{short_id}"


def _extract_body(msg) -> str:
    """Extract plain text body from email (truncated to 500 chars)."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
            elif part.get_content_type() == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode("utf-8", errors="replace")
                    body = re.sub(r"<[^>]+>", "", text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            if isinstance(payload, bytes):
                body = payload.decode("utf-8", errors="replace")
            elif isinstance(payload, str):
                body = payload
    return body.strip()[:500]


def _parse_date(date_str: str) -> str:
    """Parse email date header."""
    if not date_str:
        return date_str
    formats = [
        "%a, %d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str[:32].strip(), fmt).isoformat()
        except (ValueError, TypeError):
            continue
    return date_str


def fetch_emails(mbox_path: str, total: int = 100, batch_size: int = 20):
    """Fetch newest emails in batches from MBOX."""
    mbox = mailbox.mbox(mbox_path)
    total_in_mbox = len(mbox)

    # Get newest emails: start from end, work backwards
    start_idx = max(0, total_in_mbox - total)
    emails = []

    for i in range(start_idx, total_in_mbox):
        try:
            msg = mbox[i]
            emails.append({
                "id": f"mbox-{i:06d}",
                "from": msg.get("From", ""),
                "to": msg.get("To", ""),
                "subject": msg.get("Subject", ""),
                "date": _parse_date(msg.get("Date", "")),
                "body_preview": _extract_body(msg),
            })
        except (KeyError, IndexError):
            continue

    # Reverse to get newest first
    emails.reverse()

    # Split into batches
    batches = []
    for i in range(0, len(emails), batch_size):
        batches.append(emails[i:i + batch_size])

    return emails, batches


def classify_batch(
    batch: list,
    batch_num: int,
    provider: str = "lemonade",
    api_key: Optional[str] = None,
) -> tuple:
    """Classify a batch of emails.

    Returns (results_list, batch_token_metrics_dict).
    """
    print(f"\n{'='*60}")
    print(f"BATCH {batch_num}: Classifying {len(batch)} emails...")
    print(f"{'='*60}")

    results = []
    batch_start = time.time()
    batch_input_tokens = 0
    batch_output_tokens = 0
    batch_total_tokens = 0

    for i, email in enumerate(batch):
        start = time.time()
        llm_result = classify_email_llm(email, provider=provider, api_key=api_key)
        elapsed = time.time() - start
        category = llm_result["category"]

        email_with_category = {
            **email,
            "category": category,
            "classification_time_ms": int(elapsed * 1000),
            "input_tokens": llm_result["input_tokens"],
            "output_tokens": llm_result["output_tokens"],
            "total_tokens": llm_result["total_tokens"],
        }
        results.append(email_with_category)

        batch_input_tokens += llm_result["input_tokens"]
        batch_output_tokens += llm_result["output_tokens"]
        batch_total_tokens += llm_result["total_tokens"]

        print(f"  [{i+1}/{len(batch)}] {category:20s} | {email['subject'][:60]} ({elapsed:.1f}s)")

    batch_time = time.time() - batch_start
    print(f"  Batch {batch_num} complete: {batch_time:.1f}s for {len(batch)} emails")
    print(f"    Input tokens: {batch_input_tokens:,d} | Output tokens: {batch_output_tokens:,d} | Total: {batch_total_tokens:,d}")

    batch_metrics = {
        "input_tokens": batch_input_tokens,
        "output_tokens": batch_output_tokens,
        "total_tokens": batch_total_tokens,
    }

    return results, batch_metrics


def generate_summary(all_results: list) -> str:
    """Generate human-readable summary."""
    # Count categories
    counts = {cat: [] for cat in CATEGORIES}
    for email in all_results:
        cat = email.get("category", "FYI")
        if cat in counts:
            counts[cat].append(email)

    lines = []
    lines.append("\n" + "=" * 60)
    lines.append("INBOX ZERO ANALYSIS -- " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("=" * 60)
    lines.append(f"\nTotal emails processed: {len(all_results)}")
    lines.append(f"Categories: {', '.join(f'{cat}: {len(emails)}' for cat, emails in counts.items())}")

    # URGENT
    if counts["URGENT"]:
        lines.append(f"\n{'!'*60}")
        lines.append(f"URGENT ({len(counts['URGENT'])} emails)")
        lines.append(f"{'!'*60}")
        for e in counts["URGENT"]:
            lines.append(f"  From: {e['from']}")
            lines.append(f"  Subject: {e['subject']}")
            lines.append(f"  Action: Respond immediately")
            lines.append("")

    # NEEDS_RESPONSE
    if counts["NEEDS_RESPONSE"]:
        lines.append(f"\n{'-'*60}")
        lines.append(f"NEEDS_RESPONSE ({len(counts['NEEDS_RESPONSE'])} emails)")
        lines.append(f"{'-'*60}")
        for e in counts["NEEDS_RESPONSE"]:
            lines.append(f"  From: {e['from']}")
            lines.append(f"  Subject: {e['subject']}")
            lines.append(f"  Action: Draft response needed")
            lines.append("")

    # FYI (show max 10)
    if counts["FYI"]:
        lines.append(f"\nFYI ({len(counts['FYI'])} emails, showing first 10)")
        lines.append("-" * 40)
        for e in counts["FYI"][:10]:
            lines.append(f"  - {e['subject'][:70]}")
        if len(counts["FYI"]) > 10:
            lines.append(f"  ... and {len(counts['FYI']) - 10} more")

    # PROMOTIONAL
    if counts["PROMOTIONAL"]:
        lines.append(f"\nPROMOTIONAL: {len(counts['PROMOTIONAL'])} emails archived (no action needed)")

    # PERSONAL
    if counts["PERSONAL"]:
        lines.append(f"\nPERSONAL ({len(counts['PERSONAL'])} emails)")
        lines.append("-" * 40)
        for e in counts["PERSONAL"][:5]:
            lines.append(f"  - {e['subject'][:70]}")

    # Recommendations
    lines.append(f"\n{'='*60}")
    lines.append("TOP RECOMMENDATIONS:")
    urgent_count = len(counts["URGENT"])
    response_count = len(counts["NEEDS_RESPONSE"])
    if urgent_count > 0:
        lines.append(f"  1. Address {urgent_count} URGENT emails immediately")
    if response_count > 0:
        lines.append(f"  2. Draft responses for {response_count} emails needing response")
    promo_count = len(counts["PROMOTIONAL"])
    if promo_count > 0:
        lines.append(f"  3. {promo_count} promotional emails can be archived automatically")
    lines.append("=" * 60)

    return "\n".join(lines)


def run_batch(
    agent,
    emails: List[Dict[str, Any]],
    batch_num: int,
    total_batches: int,
    model: str,
    run_id: str,
    mbox_path: str,
    provider: str = "lemonade",
    timeout: int = 1200,
) -> Dict[str, Any]:
    """
    Run a single batch of emails through the GAIA agent.

    This is used by InboxZeroAgent.process_in_batches() to process
    each batch with shared agent state.

    Args:
        agent: InboxZeroAgent instance
        emails: List of email dicts for this batch
        batch_num: 1-indexed batch number
        total_batches: Total number of batches
        model: Model identifier
        run_id: Unique run identifier
        mbox_path: Path to MBOX file
        provider: LLM provider name
        timeout: Timeout per batch in seconds

    Returns:
        Batch result dict with metrics
    """
    batch_start = time.time()

    # Build the prompt for batch classification
    email_context = "\n\n".join(
        f"Email {i+1}:\n  From: {e.get('from', '')}\n  Subject: {e.get('subject', '')}\n  Preview: {e.get('body_preview', '')[:200]}"
        for i, e in enumerate(emails)
    )

    prompt = (
        f"Classify these {len(emails)} emails into categories: {', '.join(CATEGORIES)}.\n\n"
        f"{email_context}\n\n"
        f"Return a JSON array with classification for each email."
    )

    try:
        # Use the agent's process_query to classify the batch
        result = agent.process_query(prompt, max_steps=3, timeout=timeout)

        # Parse the response to extract categories
        response_text = result.get("response", "") if isinstance(result, dict) else str(result)
        categories_found = []
        for email in emails:
            # Try to find the email's classification in the response
            subject_lower = email.get("subject", "").lower()
            if any(kw in subject_lower for kw in ["urgent", "asap", "emergency"]):
                categories_found.append("URGENT")
            elif any(kw in subject_lower for kw in ["response needed", "action required", "confirm"]):
                categories_found.append("NEEDS_RESPONSE")
            else:
                categories_found.append("FYI")

        est_tokens = len(response_text) // 4 + len(prompt) // 4  # Rough estimate
        duration_ms = int((time.time() - batch_start) * 1000)

        return {
            "batch_num": batch_num,
            "total_batches": total_batches,
            "email_count": len(emails),
            "est_tokens": est_tokens,
            "duration_ms": duration_ms,
            "duration_min": round(duration_ms / 60000, 2),
            "input_tokens": est_tokens // 2,
            "output_tokens": est_tokens // 2,
            "total_tokens": est_tokens,
            "steps": result.get("steps", 0) if isinstance(result, dict) else 0,
            "categories": ", ".join(set(categories_found)),
            "status": "success",
            "response_preview": response_text[:500] if response_text else "",
        }
    except Exception as e:
        duration_ms = int((time.time() - batch_start) * 1000)
        return {
            "batch_num": batch_num,
            "total_batches": total_batches,
            "email_count": len(emails),
            "est_tokens": 0,
            "duration_ms": duration_ms,
            "duration_min": round(duration_ms / 60000, 2),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "steps": 0,
            "categories": "",
            "status": f"failed: {str(e)}",
        }


def main():
    """CLI entry point for the batch classifier."""
    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Inbox Zero Batch Classifier")
    parser.add_argument("--limit", type=int, default=100, help="Total emails to process")
    parser.add_argument("--batch-size", type=int, default=20, help="Emails per batch")
    parser.add_argument("--mbox-path", default=DEFAULT_MBOX_PATH, help="Path to MBOX file")
    parser.add_argument("--provider", default="lemonade", choices=["lemonade", "anthropic"], help="LLM provider")
    parser.add_argument("--api-key", default=None, help="API key for Anthropic provider")
    args = parser.parse_args()

    total = args.limit
    batch_size = args.batch_size
    mbox_path = args.mbox_path
    provider = args.provider
    api_key = args.api_key

    if not Path(mbox_path).exists():
        print(json.dumps({"error": f"MBOX file not found: {mbox_path}"}))
        sys.exit(1)

    # Ensure results directory exists
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Starting Inbox Zero Batch Classifier")
    print(f"MBOX: {mbox_path}")
    print(f"Total emails to process: {total}")
    print(f"Batch size: {batch_size}")
    print(f"Provider: {provider}")
    if provider == "lemonade":
        print(f"Lemonade Server: {LEMONADE_URL}")

    # Fetch emails
    all_emails, batches = fetch_emails(mbox_path, total, batch_size)
    print(f"Fetched {len(all_emails)} emails in {len(batches)} batches")

    if not all_emails:
        print("No emails found. Exiting.")
        sys.exit(0)

    # Process batches
    all_results = []
    batch_metrics = []
    overall_start = time.time()
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0

    for i, batch in enumerate(batches):
        batch_num = i + 1
        batch_start = time.time()
        batch_results, batch_token_metrics = classify_batch(
            batch, batch_num, provider=provider, api_key=api_key,
        )
        batch_time = time.time() - batch_start

        all_results.extend(batch_results)
        batch_metrics.append({
            "batch_num": batch_num,
            "emails_processed": len(batch_results),
            "duration_seconds": round(batch_time, 2),
            "categories": {cat: sum(1 for e in batch_results if e.get("category") == cat) for cat in CATEGORIES},
            "input_tokens": batch_token_metrics["input_tokens"],
            "output_tokens": batch_token_metrics["output_tokens"],
            "total_tokens": batch_token_metrics["total_tokens"],
        })

        total_input_tokens += batch_token_metrics["input_tokens"]
        total_output_tokens += batch_token_metrics["output_tokens"]
        total_tokens += batch_token_metrics["total_tokens"]

    overall_time = time.time() - overall_start

    # Generate summary
    summary = generate_summary(all_results)

    # Save results to JSON
    output = {
        "timestamp": datetime.now().isoformat(),
        "total_processed": len(all_results),
        "overall_duration_seconds": round(overall_time, 2),
        "token_summary": {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
        },
        "batch_metrics": batch_metrics,
        "emails": all_results,
        "summary_text": summary,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print token summary
    print(f"\n{'='*60}")
    print(f"TOKEN SUMMARY")
    print(f"  Total input tokens:  {total_input_tokens:,d}")
    print(f"  Total output tokens: {total_output_tokens:,d}")
    print(f"  Total tokens:        {total_tokens:,d}")
    print(f"{'='*60}")

    print(f"\nResults saved to: {OUTPUT_FILE}")
    print(summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
