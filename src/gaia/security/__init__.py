"""
Security module for GAIA workspace sandboxing.

Provides secure file operations with hard filesystem boundaries,
path traversal prevention, and audit logging.

Example:
    >>> from gaia.security import WorkspacePolicy, SecurityValidator
    >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
    >>> policy.write_file("file.txt", "content")
    >>> content = policy.read_file("file.txt")
"""

from gaia.security.workspace import WorkspacePolicy, WorkspaceSecurityError
from gaia.security.validator import SecurityValidator, SecurityAuditEvent

__all__ = [
    "WorkspacePolicy",
    "WorkspaceSecurityError",
    "SecurityValidator",
    "SecurityAuditEvent",
]
