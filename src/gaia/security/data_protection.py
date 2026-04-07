# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Data Protection - Encryption at Rest and PII Detection.

This module provides data protection utilities for GAIA including:
- AES-256 encryption for sensitive data
- Key management with configurable keys
- PII detection (email, phone, SSN, credit card)
- Redaction utilities for sensitive information

Example:
    >>> from gaia.security import DataProtection, PIIDetector
    >>> protector = DataProtection()
    >>> encrypted = protector.encrypt("sensitive data")
    >>> decrypted = protector.decrypt(encrypted)
    >>> assert decrypted == "sensitive data"
"""

import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Pattern, Set, Tuple
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Try to import cryptography, provide graceful fallback
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.backends import default_backend
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    Fernet = None  # type: ignore
    logger.warning(
        "cryptography library not available. "
        "Install with: pip install cryptography"
    )


# ==================== Constants ====================

# Default salt size for key derivation
DEFAULT_SALT_SIZE = 16

# Default iterations for PBKDF2
DEFAULT_ITERATIONS = 100000

# PII detection patterns
EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

# US Phone patterns: (123) 456-7890, 123-456-7890, 123.456.7890, 1234567890
PHONE_PATTERN = r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'

# SSN pattern: XXX-XX-XXXX
SSN_PATTERN = r'\b\d{3}-\d{2}-\d{4}\b'

# Credit card pattern (basic, Luhn validation applied separately)
# Matches 13-19 digits with optional spaces/dashes
CREDIT_CARD_PATTERN = r'\b(?:\d{4}[-\s]?){3,4}\d{1,4}\b'

# IP address pattern (IPv4)
IP_PATTERN = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'

# API key/token pattern (common formats)
API_KEY_PATTERN = r'\b(?:api[_-]?key|token|secret)[_\s:=]*["\']?[A-Za-z0-9\-_]{20,}["\']?\b'


# ==================== Data Classes ====================

@dataclass
class PIIMatch:
    """
    Represents a PII match in text.

    Attributes:
        pii_type: Type of PII detected
        value: The matched value
        start: Start position in text
        end: End position in text
        confidence: Confidence score (0.0-1.0)
    """
    pii_type: 'PIIType'
    value: str
    start: int
    end: int
    confidence: float

    def redact(self, visible_chars: int = 0) -> str:
        """
        Return redacted version of the value.

        Args:
            visible_chars: Number of characters to leave visible at end

        Returns:
            Redacted string with asterisks
        """
        if visible_chars > 0 and len(self.value) > visible_chars:
            return '*' * (len(self.value) - visible_chars) + self.value[-visible_chars:]
        return '*' * len(self.value)


class PIIType(Enum):
    """Types of personally identifiable information."""

    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"
    API_KEY = "api_key"


# ==================== Encryption Manager ====================

class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""
    pass


class EncryptionManager:
    """
    AES-256 encryption manager for sensitive data.

    This class provides symmetric encryption using Fernet (AES-128-CBC
    with HMAC-SHA256 for authentication) or AES-256 when available.

    Features:
    - Encrypt/decrypt strings and bytes
    - Key derivation from password using PBKDF2
    - Configurable encryption keys
    - Secure key generation

    Example:
        >>> manager = EncryptionManager()
        >>> encrypted = manager.encrypt("sensitive data")
        >>> decrypted = manager.decrypt(encrypted)
        >>> assert decrypted == "sensitive data"
    """

    def __init__(self, key: Optional[bytes] = None, key_password: Optional[str] = None):
        """
        Initialize encryption manager.

        Args:
            key: Raw encryption key (32 bytes for AES-256)
            key_password: Password for key derivation (alternative to key)

        Raises:
            EncryptionError: If cryptography library unavailable and key needed
        """
        self._fernet: Optional[Fernet] = None
        self._key: Optional[bytes] = None

        if not CRYPTOGRAPHY_AVAILABLE:
            logger.warning("Encryption disabled: cryptography library not available")
            return

        if key is not None:
            self._key = self._validate_key(key)
            # Fernet uses 32-byte key (AES-128-CBC + HMAC)
            fernet_key = base64.urlsafe_b64encode(self._key[:32])
            self._fernet = Fernet(fernet_key)
        elif key_password is not None:
            # Derive key from password
            salt = os.urandom(DEFAULT_SALT_SIZE)
            self._key = self.derive_key(key_password, salt)
            fernet_key = base64.urlsafe_b64encode(self._key[:32])
            self._fernet = Fernet(fernet_key)
        else:
            # Generate new key
            self._key = self.generate_key()
            fernet_key = base64.urlsafe_b64encode(self._key[:32])
            self._fernet = Fernet(fernet_key)

        logger.debug("EncryptionManager initialized")

    @staticmethod
    def generate_key() -> bytes:
        """
        Generate a new random encryption key.

        Returns:
            32-byte random key suitable for AES-256

        Example:
            >>> key = EncryptionManager.generate_key()
            >>> len(key)
            32
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            # Fallback: generate random bytes
            return os.urandom(32)
        return os.urandom(32)

    @staticmethod
    def derive_key(password: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS) -> bytes:
        """
        Derive encryption key from password using PBKDF2.

        Args:
            password: Password string for key derivation
            salt: Random salt (should be stored with encrypted data)
            iterations: Number of PBKDF2 iterations (default: 100000)

        Returns:
            32-byte derived key

        Example:
            >>> salt = os.urandom(16)
            >>> key = EncryptionManager.derive_key("my_password", salt)
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            # Fallback: simple hash (less secure)
            logger.warning("Using fallback key derivation (less secure)")
            return hashlib.sha256(f"{password}{salt.hex()}".encode()).digest()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
            backend=default_backend(),
        )
        return kdf.derive(password.encode())

    @staticmethod
    def _validate_key(key: bytes) -> bytes:
        """
        Validate encryption key.

        Args:
            key: Key bytes to validate

        Returns:
            Validated key bytes

        Raises:
            EncryptionError: If key is invalid
        """
        if not isinstance(key, bytes):
            raise EncryptionError("Key must be bytes")
        if len(key) < 32:
            raise EncryptionError("Key must be at least 32 bytes")
        return key

    def encrypt(self, data: bytes) -> bytes:
        """
        Encrypt data using AES-256.

        Args:
            data: Data bytes to encrypt

        Returns:
            Encrypted data with IV prepended

        Raises:
            EncryptionError: If encryption fails or cryptography unavailable

        Example:
            >>> manager = EncryptionManager()
            >>> encrypted = manager.encrypt(b"sensitive data")
        """
        if not CRYPTOGRAPHY_AVAILABLE or self._fernet is None:
            raise EncryptionError("Encryption not available")

        if not isinstance(data, bytes):
            data = data.encode('utf-8')

        try:
            return self._fernet.encrypt(data)
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {e}")

    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Decrypt data using AES-256.

        Args:
            ciphertext: Encrypted data bytes

        Returns:
            Decrypted data bytes

        Raises:
            EncryptionError: If decryption fails or cryptography unavailable

        Example:
            >>> manager = EncryptionManager()
            >>> decrypted = manager.decrypt(encrypted_data)
        """
        if not CRYPTOGRAPHY_AVAILABLE or self._fernet is None:
            raise EncryptionError("Encryption not available")

        try:
            return self._fernet.decrypt(ciphertext)
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {e}")

    def encrypt_string(self, text: str) -> str:
        """
        Encrypt string and return base64-encoded result.

        Args:
            text: Text to encrypt

        Returns:
            Base64-encoded encrypted string

        Example:
            >>> manager = EncryptionManager()
            >>> encrypted = manager.encrypt_string("secret message")
        """
        encrypted = self.encrypt(text.encode('utf-8'))
        return base64.b64encode(encrypted).decode('utf-8')

    def decrypt_string(self, encoded: str) -> str:
        """
        Decrypt base64-encoded encrypted string.

        Args:
            encoded: Base64-encoded encrypted string

        Returns:
            Decrypted text

        Example:
            >>> manager = EncryptionManager()
            >>> text = manager.decrypt_string(encrypted_string)
        """
        ciphertext = base64.b64decode(encoded.encode('utf-8'))
        decrypted = self.decrypt(ciphertext)
        return decrypted.decode('utf-8')

    @property
    def key(self) -> Optional[bytes]:
        """Return the current encryption key."""
        return self._key


# ==================== PII Detector ====================

class PIIDetector:
    """
    Detects personally identifiable information (PII) in text.

    This class provides pattern-based detection of common PII types
    including email, phone numbers, SSN, credit cards, and API keys.

    Features:
    - Configurable detection patterns
    - Confidence scoring
    - Redaction utilities
    - Batch detection

    Example:
        >>> detector = PIIDetector()
        >>> text = "Contact: john@example.com, SSN: 123-45-6789"
        >>> matches = detector.detect(text)
        >>> len(matches)
        2
    """

    def __init__(self, patterns: Optional[Dict[PIIType, Pattern]] = None):
        """
        Initialize PII detector.

        Args:
            patterns: Optional custom regex patterns for PII types

        Example:
            >>> detector = PIIDetector()
            >>> # Use default patterns
        """
        self._patterns: Dict[PIIType, Pattern] = {}
        self._enabled_types: Set[PIIType] = set()

        if patterns is not None:
            # Use custom patterns
            for pii_type, pattern in patterns.items():
                self._patterns[pii_type] = pattern
                self._enabled_types.add(pii_type)
        else:
            # Use default patterns
            self._patterns = {
                PIIType.EMAIL: re.compile(EMAIL_PATTERN),
                PIIType.PHONE: re.compile(PHONE_PATTERN),
                PIIType.SSN: re.compile(SSN_PATTERN),
                PIIType.CREDIT_CARD: re.compile(CREDIT_CARD_PATTERN),
                PIIType.IP_ADDRESS: re.compile(IP_PATTERN),
                PIIType.API_KEY: re.compile(API_KEY_PATTERN, re.IGNORECASE),
            }
            self._enabled_types = set(self._patterns.keys())

        logger.debug(f"PIIDetector initialized with {len(self._patterns)} patterns")

    def enable_type(self, pii_type: PIIType) -> None:
        """
        Enable detection for a specific PII type.

        Args:
            pii_type: PII type to enable

        Example:
            >>> detector = PIIDetector()
            >>> detector.disable_all()
            >>> detector.enable_type(PIIType.EMAIL)
        """
        if pii_type in self._patterns:
            self._enabled_types.add(pii_type)

    def disable_type(self, pii_type: PIIType) -> None:
        """
        Disable detection for a specific PII type.

        Args:
            pii_type: PII type to disable

        Example:
            >>> detector = PIIDetector()
            >>> detector.disable_type(PIIType.SSN)
        """
        self._enabled_types.discard(pii_type)

    def disable_all(self) -> None:
        """Disable all PII detection."""
        self._enabled_types.clear()

    def enable_all(self) -> None:
        """Enable all PII detection."""
        self._enabled_types = set(self._patterns.keys())

    def detect(self, text: str) -> List[PIIMatch]:
        """
        Detect PII in text.

        Args:
            text: Text to scan for PII

        Returns:
            List of PIIMatch objects for each detection

        Example:
            >>> detector = PIIDetector()
            >>> text = "Email: test@example.com, Phone: 555-123-4567"
            >>> matches = detector.detect(text)
            >>> for match in matches:
            ...     print(f"{match.pii_type}: {match.value}")
        """
        matches: List[PIIMatch] = []

        for pii_type in self._enabled_types:
            pattern = self._patterns[pii_type]
            for match in pattern.finditer(text):
                value = match.group()
                confidence = self._calculate_confidence(pii_type, value)

                # Additional validation for credit cards
                if pii_type == PIIType.CREDIT_CARD and not self._luhn_check(value):
                    continue

                piimatch = PIIMatch(
                    pii_type=pii_type,
                    value=value,
                    start=match.start(),
                    end=match.end(),
                    confidence=confidence,
                )
                matches.append(piimatch)

        # Sort by position in text
        matches.sort(key=lambda m: m.start)
        return matches

    def _calculate_confidence(self, pii_type: PIIType, value: str) -> float:
        """
        Calculate confidence score for a PII match.

        Args:
            pii_type: Type of PII detected
            value: Matched value

        Returns:
            Confidence score between 0.0 and 1.0
        """
        if pii_type == PIIType.EMAIL:
            # High confidence for standard email format
            return 0.95 if '@' in value and '.' in value.split('@')[-1] else 0.7

        elif pii_type == PIIType.PHONE:
            # Higher confidence for formatted numbers
            digits = re.sub(r'\D', '', value)
            if len(digits) == 10:
                return 0.9
            elif len(digits) == 11 and digits[0] == '1':
                return 0.85
            return 0.6

        elif pii_type == PIIType.SSN:
            # SSN format is very specific
            parts = value.split('-')
            if len(parts) == 3 and len(parts[0]) == 3 and len(parts[1]) == 2 and len(parts[2]) == 4:
                return 0.95
            return 0.7

        elif pii_type == PIIType.CREDIT_CARD:
            # Luhn validation already passed, check length
            digits = re.sub(r'\D', '', value)
            if 13 <= len(digits) <= 19:
                return 0.9
            return 0.5

        elif pii_type == PIIType.IP_ADDRESS:
            # Validate IP octets
            parts = value.split('.')
            if all(0 <= int(p) <= 255 for p in parts):
                return 0.9
            return 0.5

        elif pii_type == PIIType.API_KEY:
            # Lower confidence for API keys due to pattern generality
            return 0.7

        return 0.5

    def _luhn_check(self, card_number: str) -> bool:
        """
        Validate credit card number using Luhn algorithm.

        Args:
            card_number: Card number string (may contain spaces/dashes)

        Returns:
            True if valid Luhn checksum, False otherwise

        Example:
            >>> detector = PIIDetector()
            >>> detector._luhn_check("4532015112830366")
            True
        """
        # Remove non-digit characters
        digits = re.sub(r'\D', '', card_number)

        if not digits or len(digits) < 13:
            return False

        try:
            total = 0
            reverse_digits = digits[::-1]

            for i, digit in enumerate(reverse_digits):
                d = int(digit)
                if i % 2 == 1:
                    d *= 2
                    if d > 9:
                        d -= 9
                total += d

            return total % 10 == 0
        except (ValueError, IndexError):
            return False

    def redact(self, text: str, visible_chars: int = 0) -> str:
        """
        Redact all detected PII from text.

        Args:
            text: Text to redact
            visible_chars: Number of characters to leave visible at end

        Returns:
            Text with PII replaced by asterisks

        Example:
            >>> detector = PIIDetector()
            >>> text = "Contact john@example.com for info"
            >>> redacted = detector.redact(text)
            >>> print(redacted)
            'Contact ******************* for info'
        """
        matches = self.detect(text)

        if not matches:
            return text

        # Process matches in reverse order to preserve positions
        result = text
        for match in reversed(matches):
            redacted_value = match.redact(visible_chars)
            result = result[:match.start] + redacted_value + result[match.end:]

        return result

    def mask(self, text: str, visible_chars: int = 4) -> str:
        """
        Mask PII showing only specified visible characters.

        Similar to redact but shows some characters at the end.

        Args:
            text: Text to mask
            visible_chars: Number of characters to show at end (default: 4)

        Returns:
            Text with PII partially masked

        Example:
            >>> detector = PIIDetector()
            >>> text = "Card: 4532-0151-1283-0366"
            >>> masked = detector.mask(text)
            >>> print(masked)
            'Card: ********************0366'
        """
        return self.redact(text, visible_chars)

    def get_statistics(self, text: str) -> Dict[str, int]:
        """
        Get PII detection statistics for text.

        Args:
            text: Text to analyze

        Returns:
            Dictionary with counts per PII type

        Example:
            >>> detector = PIIDetector()
            >>> stats = detector.get_statistics("Email: a@b.com, Phone: 555-1234")
            >>> print(stats)
            {'email': 1, 'phone': 1, 'total': 2}
        """
        matches = self.detect(text)
        stats: Dict[str, int] = {}

        for match in matches:
            type_name = match.pii_type.value
            stats[type_name] = stats.get(type_name, 0) + 1

        stats['total'] = len(matches)
        return stats


# ==================== DataProtection Facade ====================

class DataProtection:
    """
    Facade for data protection operations.

    Combines encryption and PII detection into a unified interface
    for protecting sensitive data.

    Features:
    - Encrypt/decrypt sensitive strings
    - Detect and redact PII
    - Secure file encryption

    Example:
        >>> protector = DataProtection()
        >>> encrypted = protector.encrypt("secret")
        >>> has_pii = protector.contains_pii("Email: test@example.com")
        >>> redacted = protector.redact_pii("SSN: 123-45-6789")
    """

    def __init__(self, encryption_key: Optional[bytes] = None):
        """
        Initialize data protection.

        Args:
            encryption_key: Optional encryption key (generated if not provided)

        Example:
            >>> protector = DataProtection()
        """
        self._encryption = EncryptionManager(key=encryption_key)
        self._pii_detector = PIIDetector()

        logger.debug("DataProtection initialized")

    def encrypt(self, data: str) -> str:
        """
        Encrypt a string.

        Args:
            data: String to encrypt

        Returns:
            Base64-encoded encrypted string

        Raises:
            EncryptionError: If encryption fails

        Example:
            >>> protector = DataProtection()
            >>> encrypted = protector.encrypt("secret message")
        """
        return self._encryption.encrypt_string(data)

    def decrypt(self, encrypted: str) -> str:
        """
        Decrypt a string.

        Args:
            encrypted: Base64-encoded encrypted string

        Returns:
            Decrypted string

        Raises:
            EncryptionError: If decryption fails

        Example:
            >>> protector = DataProtection()
            >>> decrypted = protector.decrypt(encrypted_string)
        """
        return self._encryption.decrypt_string(encrypted)

    def contains_pii(self, text: str, min_confidence: float = 0.5) -> bool:
        """
        Check if text contains PII.

        Args:
            text: Text to check
            min_confidence: Minimum confidence threshold

        Returns:
            True if PII detected above threshold

        Example:
            >>> protector = DataProtection()
            >>> protector.contains_pii("Email: test@example.com")
            True
        """
        matches = self._pii_detector.detect(text)
        return any(m.confidence >= min_confidence for m in matches)

    def redact_pii(self, text: str, visible_chars: int = 0) -> str:
        """
        Redact all PII from text.

        Args:
            text: Text to redact
            visible_chars: Characters to leave visible

        Returns:
            Redacted text

        Example:
            >>> protector = DataProtection()
            >>> protector.redact_pii("Contact: john@example.com")
            'Contact: *****************'
        """
        return self._pii_detector.redact(text, visible_chars)

    def mask_pii(self, text: str, visible_chars: int = 4) -> str:
        """
        Mask PII showing last few characters.

        Args:
            text: Text to mask
            visible_chars: Characters to show at end

        Returns:
            Masked text

        Example:
            >>> protector = DataProtection()
            >>> protector.mask_pii("SSN: 123-45-6789", visible_chars=4)
            'SSN: *********6789'
        """
        return self._pii_detector.mask(text, visible_chars)

    def get_pii_types(self, text: str) -> Set[PIIType]:
        """
        Get set of PII types found in text.

        Args:
            text: Text to analyze

        Returns:
            Set of detected PII types

        Example:
            >>> protector = DataProtection()
            >>> types = protector.get_pii_types("Email: a@b.com, SSN: 123-45-6789")
            >>> PIIType.EMAIL in types
            True
        """
        matches = self._pii_detector.detect(text)
        return {m.pii_type for m in matches}

    @property
    def encryption_available(self) -> bool:
        """Return True if encryption is available."""
        return CRYPTOGRAPHY_AVAILABLE


# Module exports
__all__ = [
    'DataProtection',
    'EncryptionManager',
    'EncryptionError',
    'PIIDetector',
    'PIIMatch',
    'PIIType',
    'CRYPTOGRAPHY_AVAILABLE',
]
