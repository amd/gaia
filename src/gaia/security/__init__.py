"""
Security module for GAIA workspace sandboxing and data protection.

Provides secure file operations with hard filesystem boundaries,
path traversal prevention, audit logging, and data protection.

Example:
    >>> from gaia.security import WorkspacePolicy, SecurityValidator, DataProtection
    >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
    >>> policy.write_file("file.txt", "content")
    >>> content = policy.read_file("file.txt")
    >>> protector = DataProtection()
    >>> encrypted = protector.encrypt("sensitive data")
"""

from gaia.security.workspace import WorkspacePolicy, WorkspaceSecurityError
from gaia.security.validator import SecurityValidator, SecurityAuditEvent
from gaia.security.data_protection import (
    DataProtection,
    EncryptionManager,
    EncryptionError,
    PIIDetector,
    PIIMatch,
    PIIType,
    CRYPTOGRAPHY_AVAILABLE,
)

__all__ = [
    # Workspace security
    "WorkspacePolicy",
    "WorkspaceSecurityError",
    "SecurityValidator",
    "SecurityAuditEvent",
    # Data protection
    "DataProtection",
    "EncryptionManager",
    "EncryptionError",
    "PIIDetector",
    "PIIMatch",
    "PIIType",
    "CRYPTOGRAPHY_AVAILABLE",
]
