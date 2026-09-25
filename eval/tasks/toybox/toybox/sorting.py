"""Ordering helpers."""


def sort_items(items):
    """Newest first. Items with equal timestamps must keep input order."""
    # BUG: reverse=True on a non-stable key flips ties between runs.
    return sorted(items, key=lambda i: i["ts"], reverse=True)
