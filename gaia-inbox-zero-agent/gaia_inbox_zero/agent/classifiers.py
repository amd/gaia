"""
Email Classification Engine

Heuristic and LLM-powered email classification for the Inbox Zero Agent.
Contains the canonical category assignment logic used by both the batch
classifier CLI and the GAIA agent tools.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, Any, List, Optional

from gaia_inbox_zero.agent.config import (
    CATEGORIES,
    LEMONADE_URL,
    ANTHROPIC_URL,
    DEFAULT_MODELS,
    CLASSIFICATION_PROMPT,
)


def classify_category_heuristic(subject: str, sender: str, labels: List[str]) -> str:
    """Heuristic email categorization based on content signals.

    Uses Gmail labels and subject/sender keywords to assign a category.
    Labels take priority over keyword matching.

    Args:
        subject: Email subject line
        sender: Email sender address
        labels: List of Gmail labels

    Returns:
        One of: promotions, purchases, updates, social, forums, security, inbox
    """
    labels_lower = [l.lower() for l in labels]
    subject_lower = subject.lower()
    sender_lower = sender.lower()

    # Gmail category labels (priority)
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


def classify_email_llm(
    email: Dict[str, Any],
    timeout: int = 45,
    provider: str = "lemonade",
    api_key: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Classify a single email via LLM API.

    Supports Lemonade (OpenAI-compatible) and Anthropic (Messages API).
    Returns dict with category + token usage from the API response.

    Args:
        email: Email dict with from, subject, body_preview
        timeout: Request timeout in seconds
        provider: "lemonade" or "anthropic"
        api_key: API key for Anthropic provider (reads from ANTHROPIC_API_KEY env var if not provided)
        max_retries: Maximum number of retry attempts on transient errors

    Returns:
        Dict with keys: category, input_tokens, output_tokens, total_tokens
    """
    if api_key is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    prompt = CLASSIFICATION_PROMPT.format(
        sender=email["from"][:200],
        subject=email["subject"],
        body_preview=email["body_preview"][:300],
    )

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            model = DEFAULT_MODELS.get(provider, DEFAULT_MODELS["lemonade"])

            if provider == "anthropic":
                payload = {
                    "model": model,
                    "system": "You are an email classification assistant. Respond with only the category name.",
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 10,
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    ANTHROPIC_URL,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    method="POST",
                )
            else:
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "You are an email classification assistant. Respond with only the category name."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 10,
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    LEMONADE_URL,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if provider == "anthropic":
                content = result.get("content", [{}])[0].get("text", "").strip()
                usage = result.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                total_tokens = input_tokens + output_tokens
            else:
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                usage = result.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)

            # Normalize category
            content = content.upper().replace(" ", "_").strip(".")
            if content in CATEGORIES:
                category = content
            else:
                category = None
                for cat in CATEGORIES:
                    if cat in content:
                        category = cat
                        break
                if category is None:
                    category = "FYI"

            return {
                "category": category,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                retry_wait = 2 ** attempt
                print(f"  WARNING: LLM classification failed (attempt {attempt+1}/{max_retries+1}) for '{email['subject'][:50]}': {e}")
                print(f"  Retrying in {retry_wait}s...")
                time.sleep(retry_wait)
            else:
                print(f"  WARNING: LLM classification failed after {max_retries+1} attempts for '{email['subject'][:50]}': {e}")

    return {"category": "FYI", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def group_by_category(emails: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Group emails into priority categories based on content analysis.

    Categories: URGENT, NEEDS_RESPONSE, FYI, PROMOTIONAL, PERSONAL

    Args:
        emails: List of email dicts to categorize

    Returns:
        Dictionary with emails grouped by category, plus total count
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
