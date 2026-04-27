"""Email data loading module."""

from .email_loader import load_mbox, count_mbox, DEFAULT_MBOX_PATH

__all__ = ["load_mbox", "count_mbox", "DEFAULT_MBOX_PATH"]
