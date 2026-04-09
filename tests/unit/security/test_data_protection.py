# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for Data Protection Components.

This test suite validates the data protection implementation including:
- EncryptionManager: AES-256 encryption/decryption
- PIIDetector: PII detection and redaction
- DataProtection: Unified facade for data protection

Quality Gate 6 Criteria Covered:
- SEC-001: Encryption at rest functioning (encrypt/decrypt roundtrip)
- SEC-002: PII detection accuracy (email, phone, SSN, credit card)
- THREAD-006: Thread safety for concurrent operations
"""

import base64
import os
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from gaia.security.data_protection import (
    DataProtection,
    EncryptionManager,
    EncryptionError,
    PIIDetector,
    PIIMatch,
    PIIType,
    CRYPTOGRAPHY_AVAILABLE,
)


# =============================================================================
# EncryptionManager Tests
# =============================================================================

class TestEncryptionManagerInitialization:
    """Tests for EncryptionManager initialization."""

    def test_init_default(self):
        """Test default initialization generates a key."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        assert manager is not None
        assert manager.key is not None
        assert len(manager.key) == 32

    def test_init_with_key(self):
        """Test initialization with custom key."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        custom_key = os.urandom(32)
        manager = EncryptionManager(key=custom_key)
        assert manager.key == custom_key

    def test_init_with_password(self):
        """Test initialization with password for key derivation."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager(key_password="my_secret_password")
        assert manager.key is not None
        assert len(manager.key) == 32

    def test_init_invalid_key_too_short(self):
        """Test that short keys are rejected."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        with pytest.raises(EncryptionError) as exc_info:
            EncryptionManager(key=b"short")
        assert "at least 32 bytes" in str(exc_info.value)

    def test_init_invalid_key_type(self):
        """Test that non-bytes keys are rejected."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        with pytest.raises(EncryptionError) as exc_info:
            EncryptionManager(key="not bytes")  # type: ignore
        assert "must be bytes" in str(exc_info.value)


class TestEncryptionManagerGenerateKey:
    """Tests for key generation."""

    def test_generate_key_returns_bytes(self):
        """Test that generate_key returns bytes."""
        key = EncryptionManager.generate_key()
        assert isinstance(key, bytes)

    def test_generate_key_correct_length(self):
        """Test that generated key is 32 bytes."""
        key = EncryptionManager.generate_key()
        assert len(key) == 32

    def test_generate_key_unique(self):
        """Test that each generated key is unique."""
        key1 = EncryptionManager.generate_key()
        key2 = EncryptionManager.generate_key()
        assert key1 != key2


class TestEncryptionManagerDeriveKey:
    """Tests for key derivation."""

    def test_derive_key_returns_bytes(self):
        """Test that derive_key returns bytes."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        salt = os.urandom(16)
        key = EncryptionManager.derive_key("password", salt)
        assert isinstance(key, bytes)

    def test_derive_key_correct_length(self):
        """Test that derived key is 32 bytes."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        salt = os.urandom(16)
        key = EncryptionManager.derive_key("password", salt)
        assert len(key) == 32

    def test_derive_key_deterministic(self):
        """Test that same password+salt produces same key."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        salt = os.urandom(16)
        key1 = EncryptionManager.derive_key("password", salt)
        key2 = EncryptionManager.derive_key("password", salt)
        assert key1 == key2

    def test_derive_key_different_salts(self):
        """Test that different salts produce different keys."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        salt1 = os.urandom(16)
        salt2 = os.urandom(16)
        key1 = EncryptionManager.derive_key("password", salt1)
        key2 = EncryptionManager.derive_key("password", salt2)
        assert key1 != key2


class TestEncryptionManagerEncryptDecrypt:
    """Tests for encryption/decryption roundtrip."""

    def test_encrypt_decrypt_string_roundtrip(self):
        """Test string encrypt/decrypt roundtrip."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        original = "Hello, World!"

        encrypted = manager.encrypt_string(original)
        decrypted = manager.decrypt_string(encrypted)

        assert decrypted == original

    def test_encrypt_decrypt_bytes_roundtrip(self):
        """Test bytes encrypt/decrypt roundtrip."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        original = b"Hello, World!"

        encrypted = manager.encrypt(original)
        decrypted = manager.decrypt(encrypted)

        assert decrypted == original

    def test_encrypt_decrypt_empty_string(self):
        """Test empty string encrypt/decrypt."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        original = ""

        encrypted = manager.encrypt_string(original)
        decrypted = manager.decrypt_string(encrypted)

        assert decrypted == original

    def test_encrypt_decrypt_unicode(self):
        """Test unicode string encrypt/decrypt."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        original = "Hello, \u4e16\u754c! \U0001F600"

        encrypted = manager.encrypt_string(original)
        decrypted = manager.decrypt_string(encrypted)

        assert decrypted == original

    def test_encrypt_different_values(self):
        """Test that same input produces different ciphertext."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        original = "Hello"

        encrypted1 = manager.encrypt_string(original)
        encrypted2 = manager.encrypt_string(original)

        # Fernet uses random IV, so ciphertext should differ
        assert encrypted1 != encrypted2

    def test_decrypt_wrong_key(self):
        """Test that decryption fails with wrong key."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager1 = EncryptionManager()
        manager2 = EncryptionManager()

        original = "Secret"
        encrypted = manager1.encrypt_string(original)

        with pytest.raises(EncryptionError):
            manager2.decrypt_string(encrypted)

    def test_decrypt_invalid_ciphertext(self):
        """Test that invalid ciphertext raises error."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        import binascii

        manager = EncryptionManager()

        # Invalid base64 should raise an error
        with pytest.raises((EncryptionError, binascii.Error)):
            manager.decrypt_string("invalid_base64!!!")

        # Valid base64 but invalid Fernet token should also raise
        valid_base64 = base64.b64encode(b"not a valid fernet token").decode()
        with pytest.raises(EncryptionError):
            manager.decrypt_string(valid_base64)


class TestEncryptionManagerThreadSafety:
    """Tests for thread safety of encryption operations."""

    def test_concurrent_encrypt_decrypt(self):
        """Test concurrent encryption/decryption operations."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        manager = EncryptionManager()
        results: List[str] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def encrypt_decrypt(value: int):
            try:
                original = f"Secret_{value}"
                encrypted = manager.encrypt_string(original)
                decrypted = manager.decrypt_string(encrypted)
                with lock:
                    results.append(decrypted)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(50):
            t = threading.Thread(target=encrypt_decrypt, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 50
        # Verify all expected values are present (not order-dependent)
        expected = {f"Secret_{i}" for i in range(50)}
        assert set(results) == expected


# =============================================================================
# PIIDetector Tests
# =============================================================================

class TestPIIDetectorInitialization:
    """Tests for PIIDetector initialization."""

    def test_init_default(self):
        """Test default initialization."""
        detector = PIIDetector()
        assert detector is not None
        assert len(detector._patterns) > 0

    def test_init_enables_all_types(self):
        """Test that all PII types are enabled by default."""
        detector = PIIDetector()
        assert PIIType.EMAIL in detector._enabled_types
        assert PIIType.PHONE in detector._enabled_types
        assert PIIType.SSN in detector._enabled_types
        assert PIIType.CREDIT_CARD in detector._enabled_types

    def test_enable_disable_type(self):
        """Test enabling and disabling specific types."""
        detector = PIIDetector()

        detector.disable_type(PIIType.EMAIL)
        assert PIIType.EMAIL not in detector._enabled_types

        detector.enable_type(PIIType.EMAIL)
        assert PIIType.EMAIL in detector._enabled_types

    def test_disable_all(self):
        """Test disabling all types."""
        detector = PIIDetector()
        detector.disable_all()
        assert len(detector._enabled_types) == 0

    def test_enable_all(self):
        """Test enabling all types."""
        detector = PIIDetector()
        detector.disable_all()
        detector.enable_all()
        assert len(detector._enabled_types) == len(detector._patterns)


class TestPIIDetectorEmail:
    """Tests for email detection."""

    def test_detect_email_simple(self):
        """Test detecting simple email."""
        detector = PIIDetector()
        text = "Contact: user@example.com"

        matches = detector.detect(text)

        assert len(matches) == 1
        assert matches[0].pii_type == PIIType.EMAIL
        assert matches[0].value == "user@example.com"

    def test_detect_email_multiple(self):
        """Test detecting multiple emails."""
        detector = PIIDetector()
        text = "Emails: a@b.com and c@d.org"

        matches = detector.detect(text)

        assert len(matches) == 2
        assert all(m.pii_type == PIIType.EMAIL for m in matches)

    def test_detect_email_in_sentence(self):
        """Test detecting email in natural sentence."""
        detector = PIIDetector()
        text = "Please send the report to john.doe@company.co.uk by Friday."

        matches = detector.detect(text)

        assert len(matches) >= 1
        assert any(m.value == "john.doe@company.co.uk" for m in matches)


class TestPIIDetectorPhone:
    """Tests for phone number detection."""

    def test_detect_phone_formatted(self):
        """Test detecting formatted phone number."""
        detector = PIIDetector()
        text = "Call: (555) 123-4567"

        matches = detector.detect(text)

        assert len(matches) >= 1
        phone_match = next(m for m in matches if m.pii_type == PIIType.PHONE)
        assert "555" in phone_match.value

    def test_detect_phone_dashes(self):
        """Test detecting phone with dashes."""
        detector = PIIDetector()
        text = "Phone: 555-123-4567"

        matches = detector.detect(text)

        assert len(matches) >= 1

    def test_detect_phone_dots(self):
        """Test detecting phone with dots."""
        detector = PIIDetector()
        text = "Call 555.123.4567 for info"

        matches = detector.detect(text)

        assert len(matches) >= 1


class TestPIIDetectorSSN:
    """Tests for SSN detection."""

    def test_detect_ssn(self):
        """Test detecting SSN."""
        detector = PIIDetector()
        text = "SSN: 123-45-6789"

        matches = detector.detect(text)

        ssn_matches = [m for m in matches if m.pii_type == PIIType.SSN]
        assert len(ssn_matches) == 1
        assert ssn_matches[0].value == "123-45-6789"

    def test_detect_ssn_in_context(self):
        """Test detecting SSN in context."""
        detector = PIIDetector()
        text = "Employee record: John Doe, SSN 987-65-4321, Dept: Engineering"

        matches = detector.detect(text)

        ssn_matches = [m for m in matches if m.pii_type == PIIType.SSN]
        assert len(ssn_matches) == 1
        assert ssn_matches[0].value == "987-65-4321"


class TestPIIDetectorCreditCard:
    """Tests for credit card detection."""

    def test_detect_credit_card_basic(self):
        """Test detecting credit card number."""
        detector = PIIDetector()
        # Valid test card number (passes Luhn check)
        text = "Card: 4532015112830366"

        matches = detector.detect(text)

        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(cc_matches) == 1

    def test_detect_credit_card_formatted(self):
        """Test detecting formatted credit card."""
        detector = PIIDetector()
        text = "Card: 4532-0151-1283-0366"

        matches = detector.detect(text)

        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        assert len(cc_matches) == 1

    def test_luhn_check_invalid_card(self):
        """Test that invalid card numbers fail Luhn check."""
        detector = PIIDetector()
        # Invalid card number (fails Luhn check)
        text = "Card: 1234567890123456"

        matches = detector.detect(text)

        cc_matches = [m for m in matches if m.pii_type == PIIType.CREDIT_CARD]
        # Should be filtered out by Luhn check
        assert len(cc_matches) == 0

    def test_luhn_check_valid_card(self):
        """Test Luhn algorithm with known valid number."""
        detector = PIIDetector()
        # Known valid test card number
        assert detector._luhn_check("4532015112830366") is True
        assert detector._luhn_check("5425233430109903") is True

        # Known invalid
        assert detector._luhn_check("1234567890123456") is False


class TestPIIDetectorRedaction:
    """Tests for PII redaction."""

    def test_redact_email(self):
        """Test redacting email."""
        detector = PIIDetector()
        text = "Contact user@example.com for info"

        redacted = detector.redact(text)

        assert "@" not in redacted
        assert "Contact" in redacted
        assert "for info" in redacted

    def test_redact_multiple_pii(self):
        """Test redacting multiple PII types."""
        detector = PIIDetector()
        text = "John: john@example.com, SSN: 123-45-6789"

        redacted = detector.redact(text)

        assert "@" not in redacted
        assert "123-45-6789" not in redacted

    def test_redact_with_visible_chars(self):
        """Test redaction showing last N characters."""
        detector = PIIDetector()
        text = "SSN: 123-45-6789"

        redacted = detector.redact(text, visible_chars=4)

        assert "6789" in redacted
        assert "123-45-" not in redacted

    def test_mask_pii(self):
        """Test masking PII."""
        detector = PIIDetector()
        text = "Card: 4532015112830366"

        masked = detector.mask(text, visible_chars=4)

        assert "0366" in masked
        assert masked.count("*") > 0

    def test_redact_no_pii(self):
        """Test redaction when no PII present."""
        detector = PIIDetector()
        text = "This is a normal sentence with no PII."

        redacted = detector.redact(text)

        assert redacted == text


class TestPIIDetectorStatistics:
    """Tests for PII statistics."""

    def test_get_statistics(self):
        """Test getting PII statistics."""
        detector = PIIDetector()
        text = "Email: a@b.com, Phone: 555-123-4567, SSN: 123-45-6789"

        stats = detector.get_statistics(text)

        assert stats['total'] >= 2
        assert stats.get('email', 0) >= 1

    def test_get_statistics_empty(self):
        """Test statistics for text without PII."""
        detector = PIIDetector()
        text = "No PII here"

        stats = detector.get_statistics(text)

        assert stats['total'] == 0


class TestPIIDetectorThreadSafety:
    """Tests for thread safety of PII detection."""

    def test_concurrent_detection(self):
        """Test concurrent PII detection."""
        detector = PIIDetector()
        results: List[int] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def detect_pii(value: int):
            try:
                text = f"Email: user{value}@example.com, SSN: {value:03d}-{value:02d}-{value:04d}"
                matches = detector.detect(text)
                with lock:
                    results.append(len(matches))
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(50):
            t = threading.Thread(target=detect_pii, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 50
        # Each should have found at least 1 match (email)
        assert all(r >= 1 for r in results)


# =============================================================================
# DataProtection Facade Tests
# =============================================================================

class TestDataProtectionInitialization:
    """Tests for DataProtection facade initialization."""

    def test_init_default(self):
        """Test default initialization."""
        protector = DataProtection()
        assert protector is not None

    def test_init_with_key(self):
        """Test initialization with custom key."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        key = os.urandom(32)
        protector = DataProtection(encryption_key=key)
        assert protector is not None


class TestDataProtectionEncryptDecrypt:
    """Tests for DataProtection encrypt/decrypt."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test encrypt/decrypt roundtrip."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        protector = DataProtection()
        original = "Secret message"

        encrypted = protector.encrypt(original)
        decrypted = protector.decrypt(encrypted)

        assert decrypted == original


class TestDataProtectionPII:
    """Tests for DataProtection PII methods."""

    def test_contains_pii_true(self):
        """Test contains_pii returns True for PII."""
        protector = DataProtection()

        result = protector.contains_pii("Email: test@example.com")

        assert result is True

    def test_contains_pii_false(self):
        """Test contains_pii returns False for non-PII."""
        protector = DataProtection()

        result = protector.contains_pii("This is normal text")

        assert result is False

    def test_redact_pii(self):
        """Test PII redaction."""
        protector = DataProtection()
        text = "Contact: john@example.com"

        redacted = protector.redact_pii(text)

        assert "@" not in redacted

    def test_mask_pii(self):
        """Test PII masking."""
        protector = DataProtection()
        text = "SSN: 123-45-6789"

        masked = protector.mask_pii(text, visible_chars=4)

        assert "6789" in masked

    def test_get_pii_types(self):
        """Test getting PII types."""
        protector = DataProtection()
        text = "Email: a@b.com and SSN: 123-45-6789"

        types = protector.get_pii_types(text)

        assert PIIType.EMAIL in types
        assert PIIType.SSN in types


# =============================================================================
# Integration Tests
# =============================================================================

class TestDataProtectionIntegration:
    """Integration tests for data protection components."""

    def test_full_workflow(self):
        """Test complete data protection workflow."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        protector = DataProtection()

        # Test encryption
        secret = "API_KEY=sk-12345abcde"
        encrypted = protector.encrypt(secret)
        decrypted = protector.decrypt(encrypted)
        assert decrypted == secret

        # Test PII detection
        pii_text = "User email: user@company.com with SSN 123-45-6789"
        assert protector.contains_pii(pii_text) is True

        # Test redaction
        redacted = protector.redact_pii(pii_text)
        assert "@" not in redacted

        # Encrypt redacted text
        encrypted_redacted = protector.encrypt(redacted)
        decrypted_redacted = protector.decrypt(encrypted_redacted)
        assert decrypted_redacted == redacted

    def test_encryption_available_flag(self):
        """Test encryption_available property."""
        protector = DataProtection()
        assert protector.encryption_available == CRYPTOGRAPHY_AVAILABLE


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestDataProtectionEdgeCases:
    """Edge case tests for data protection."""

    def test_empty_string_encryption(self):
        """Test encrypting empty string."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        protector = DataProtection()
        encrypted = protector.encrypt("")
        decrypted = protector.decrypt(encrypted)
        assert decrypted == ""

    def test_very_long_string_encryption(self):
        """Test encrypting very long string."""
        if not CRYPTOGRAPHY_AVAILABLE:
            pytest.skip("cryptography library not available")

        protector = DataProtection()
        long_text = "A" * 10000
        encrypted = protector.encrypt(long_text)
        decrypted = protector.decrypt(encrypted)
        assert decrypted == long_text

    def test_special_characters_in_text(self):
        """Test PII detection with special characters."""
        detector = PIIDetector()
        text = "Email: <script>alert('test@example.com')</script>"

        matches = detector.detect(text)
        email_matches = [m for m in matches if m.pii_type == PIIType.EMAIL]
        assert len(email_matches) >= 1

    def test_unicode_in_pii_text(self):
        """Test PII detection with unicode text."""
        detector = PIIDetector()
        text = "\u65e5\u672c\u8a9e: user@example.com"

        matches = detector.detect(text)
        assert len(matches) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
