"""
Email Data Loader

Loads emails from a local MBOX file (Gmail Takeout format).
Parses headers and body into structured dicts for GAIA agents.

Supports per-email timing for benchmark analysis.
"""

import mailbox
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

# ── Default MBOX path (overridable via MBOX_PATH env var) ──────────────────
DEFAULT_MBOX_PATH = (
    r"C:\Users\antmi\Downloads\takeout-20260420T224647Z-3-001"
    r"\Takeout\Mail\All mail Including Spam and Trash.mbox"
)


def _validate_path(path: str) -> Path:
    """Validate and resolve MBOX file path.

    Args:
        path: Path string to validate

    Returns:
        Resolved Path object

    Raises:
        FileNotFoundError: If path doesn't exist
        ValueError: If path is not a file
    """
    mbox_path = Path(path).expanduser().resolve()
    if not mbox_path.exists():
        raise FileNotFoundError(f"MBOX file not found: {path}")
    if not mbox_path.is_file():
        raise ValueError(f"Path is not a file: {path}")
    return mbox_path


def _extract_body(msg) -> str:
    """Extract plain text body from an email message.

    Security: Uses errors='replace' to handle malformed encoding safely.
    """
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        body = payload.decode("utf-8", errors="replace")
                    except (UnicodeDecodeError, LookupError):
                        body = payload.decode("latin-1", errors="replace")
                    break
            elif ct == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        text = payload.decode("utf-8", errors="replace")
                    except (UnicodeDecodeError, LookupError):
                        text = payload.decode("latin-1", errors="replace")
                    # Strip HTML tags
                    body = re.sub(r"<[^>]+>", "", text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            if isinstance(payload, bytes):
                try:
                    body = payload.decode("utf-8", errors="replace")
                except (UnicodeDecodeError, LookupError):
                    body = payload.decode("latin-1", errors="replace")
            elif isinstance(payload, str):
                body = payload

    return body.strip()


def _parse_labels(raw_labels: Optional[str]) -> List[str]:
    """Parse X-Gmail-Labels header into a list."""
    if not raw_labels:
        return []
    return [label.strip() for label in raw_labels.split(",") if label.strip()]


def _classify_category(subject: str, sender: str, labels: List[str]) -> str:
    """Heuristic email categorization based on content."""
    labels_lower = [l.lower() for l in labels]
    subject_lower = subject.lower()
    sender_lower = sender.lower()

    # Gmail category labels
    if any("promotions" in l for l in labels_lower):
        return "promotions"
    if any("purchases" in l for l in labels_lower):
        return "purchases"
    if any("updates" in l for l in labels_lower):
        return "updates"
    if any("social" in l for l in labels_lower):
        return "social"
    if any("forums" in l for l in labels_lower):
        return "forums"

    # Keyword-based fallback
    if any(kw in subject_lower for kw in ["invoice", "receipt", "order confirmed", "payment"]):
        return "purchases"
    if any(kw in subject_lower for kw in ["50% off", "sale", "deal", "discount", "coupon"]):
        return "promotions"
    if any(kw in sender_lower for kw in ["noreply", "no-reply", "auto-confirm"]):
        return "updates"
    if any(kw in subject_lower for kw in ["security", "password", "account", "login"]):
        return "security"

    return "inbox"


def _parse_date(date_str: str) -> str:
    """Parse email date header with multiple format fallbacks.

    Args:
        date_str: Raw date header string

    Returns:
        ISO format date string, or original string if parsing fails
    """
    if not date_str:
        return date_str

    # Common email date formats to try
    formats = [
        "%a, %d %b %Y %H:%M:%S",      # RFC 2822 standard
        "%d %b %Y %H:%M:%S",          # RFC 2822 without weekday
        "%a, %d %b %Y %H:%M:%S %z",   # With timezone
        "%d %b %Y %H:%M:%S %z",       # Without weekday, with timezone
    ]

    for fmt in formats:
        try:
            # Handle timezone offset in various formats
            clean_date = date_str[:32].strip()
            return datetime.strptime(clean_date, fmt).isoformat()
        except (ValueError, TypeError):
            continue

    # All formats failed, return original string
    return date_str


def load_mbox(
    path: str = DEFAULT_MBOX_PATH,
    limit: Optional[int] = None,
    offset: int = 0,
    reverse: bool = False,
    enable_timing: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Load emails from an MBOX file.

    Args:
        path: Path to .mbox file.
        limit: Max emails to return (None = all). Must be positive if provided.
        offset: Skip first N emails. Must be non-negative.
        reverse: Return newest first (True) or oldest first (False).
        enable_timing: If True, track per-email load timing.

    Returns:
        If enable_timing is False:
            List of structured email dicts with keys:
            id, from, to, subject, date, labels, category, body
        If enable_timing is True:
            Tuple of (list of email dicts, timing metadata dict)

        Timing metadata contains:
            total_load_time_ms: Total time to load all emails
            per_email_times: List of {email_id, load_time_ms} dicts
            email_count: Number of emails loaded

    Raises:
        FileNotFoundError: If MBOX file doesn't exist
        ValueError: If offset is negative or limit is non-positive
    """
    # Validate inputs
    if offset < 0:
        raise ValueError(f"offset must be non-negative, got {offset}")
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    mbox_path = _validate_path(path)

    try:
        mbox = mailbox.mbox(str(mbox_path))
    except mailbox.NoSuchMailboxError:
        raise ValueError(f"Invalid MBOX file format: {path}")
    except Exception as e:
        raise RuntimeError(f"Failed to open MBOX file: {e}")

    indices = range(offset, min(len(mbox), offset + limit) if limit else len(mbox))

    if reverse:
        indices = list(reversed(list(indices)))

    emails = []
    timing_data = {
        "total_load_time_ms": 0,
        "per_email_times": [],
        "email_count": 0,
    } if enable_timing else None

    overall_start = time.time() if enable_timing else None

    for i in indices:
        email_start = time.time() if enable_timing else None

        try:
            msg = mbox[i]
        except (KeyError, IndexError):
            # Skip invalid message indices
            continue

        body = _extract_body(msg)
        raw_labels = msg.get("X-Gmail-Labels", msg.get("Labels", ""))
        labels = _parse_labels(raw_labels)
        subject = msg.get("Subject", "") or ""
        sender = msg.get("From", "") or ""
        recipient = msg.get("To", "") or ""
        date_str = msg.get("Date", "") or ""

        # Parse date with multiple format fallbacks
        date_parsed = _parse_date(date_str)

        category = _classify_category(subject, sender, labels)

        email_dict = {
            "id": f"mbox-{i:06d}",
            "from": sender,
            "to": recipient,
            "subject": subject,
            "date": date_parsed,
            "labels": labels,
            "category": category,
            "body": body,
            "body_preview": body[:200] if body else "",
        }
        emails.append(email_dict)

        # Track timing for this email
        if enable_timing and email_start is not None:
            email_end = time.time()
            email_time_ms = int((email_end - email_start) * 1000)
            timing_data["per_email_times"].append({
                "email_id": f"mbox-{i:06d}",
                "subject": subject[:50] if subject else "",
                "load_time_ms": email_time_ms,
            })

    if enable_timing and overall_start is not None:
        timing_data["total_load_time_ms"] = int((time.time() - overall_start) * 1000)
        timing_data["email_count"] = len(emails)

    if enable_timing:
        return emails, timing_data
    return emails


def count_mbox(path: str = DEFAULT_MBOX_PATH) -> int:
    """Return total message count in the MBOX file.

    Args:
        path: Path to .mbox file

    Returns:
        Number of messages in the file, or 0 if file doesn't exist

    Raises:
        ValueError: If file exists but is not a valid MBOX
    """
    try:
        mbox_path = _validate_path(path)
    except FileNotFoundError:
        return 0

    try:
        return len(mailbox.mbox(str(mbox_path)))
    except mailbox.NoSuchMailboxError:
        raise ValueError(f"Invalid MBOX file format: {path}")
