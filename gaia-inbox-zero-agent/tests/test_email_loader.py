"""
Email Data Loader Unit Tests

Tests for gaia_inbox_zero/data/email_loader.py covering:
- Path validation (_validate_path)
- Body extraction (_extract_body)
- Label parsing (_parse_labels)
- Category classification (_classify_category)
- Date parsing (_parse_date)
- MBOX loading (load_mbox)
- MBOX counting (count_mbox)
- Error handling edge cases
"""

import mailbox
import os
import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

from gaia_inbox_zero.data.email_loader import (
    _validate_path,
    _extract_body,
    _parse_labels,
    _classify_category,
    _parse_date,
    load_mbox,
    count_mbox,
    DEFAULT_MBOX_PATH,
)


# -- Test Fixtures -----------------------------------------------------------

def _create_test_mbox(emails, path=None):
    """Create a temporary MBOX file with the given list of email dicts."""
    if path is None:
        fd, path = tempfile.mkstemp(suffix=".mbox")
        os.close(fd)

    mbox = mailbox.mbox(path)
    mbox.lock()
    try:
        for email_data in emails:
            msg = mailbox.mboxMessage()
            msg["From"] = email_data.get("from", "test@example.com")
            msg["To"] = email_data.get("to", "recipient@example.com")
            msg["Subject"] = email_data.get("subject", "Test Subject")
            msg["Date"] = email_data.get("date", "Mon, 01 Jan 2024 00:00:00 +0000")
            if "labels" in email_data:
                msg["X-Gmail-Labels"] = ", ".join(email_data["labels"])

            body = email_data.get("body", "Test body content")
            msg.set_payload(body)

            mbox.add(msg)
    finally:
        mbox.flush()
        mbox.unlock()
        mbox.close()

    return path


# -- _validate_path Tests ----------------------------------------------------

class TestValidatePath:
    """Tests for the _validate_path function."""

    def test_valid_file_path(self, tmp_path):
        """Should return resolved Path for existing file."""
        test_file = tmp_path / "test.mbox"
        test_file.write_text("test")
        result = _validate_path(str(test_file))
        assert isinstance(result, Path)
        assert result.exists()

    def test_nonexistent_path_raises_file_not_found(self, tmp_path):
        """Should raise FileNotFoundError for non-existent path."""
        non_existent = str(tmp_path / "does_not_exist.mbox")
        with pytest.raises(FileNotFoundError, match="MBOX file not found"):
            _validate_path(non_existent)

    def test_directory_path_raises_value_error(self, tmp_path):
        """Should raise ValueError when path is a directory."""
        with pytest.raises(ValueError, match="Path is not a file"):
            _validate_path(str(tmp_path))

    def test_expands_user_home(self):
        """Should expand ~ in path."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_file", return_value=True):
                result = _validate_path("~/test.mbox")
                assert "~" not in str(result)


# -- _extract_body Tests -----------------------------------------------------

class TestExtractBody:
    """Tests for the _extract_body function."""

    def test_plain_text_body(self):
        """Should extract plain text body from non-multipart message."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = b"Hello, this is the body text."

        result = _extract_body(msg)
        assert result == "Hello, this is the body text."

    def test_plain_text_body_string_payload(self):
        """Should handle string payload directly."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = "String body content"

        result = _extract_body(msg)
        assert result == "String body content"

    def test_multipart_picks_text_plain(self):
        """Should prefer text/plain over text/html in multipart."""
        msg = MagicMock()
        msg.is_multipart.return_value = True

        text_plain = MagicMock()
        text_plain.get_content_type.return_value = "text/plain"
        text_plain.get_payload.return_value = b"Plain text version"

        text_html = MagicMock()
        text_html.get_content_type.return_value = "text/html"
        text_html.get_payload.return_value = b"<html>HTML version</html>"

        msg.walk.return_value = [text_plain, text_html]

        result = _extract_body(msg)
        assert result == "Plain text version"

    def test_html_strips_tags(self):
        """Should strip HTML tags when only HTML part available."""
        msg = MagicMock()
        msg.is_multipart.return_value = True

        html_part = MagicMock()
        html_part.get_content_type.return_value = "text/html"
        html_part.get_payload.return_value = b"<html><body><p>Hello World</p></body></html>"

        text_part = MagicMock()
        text_part.get_content_type.return_value = "text/html"
        text_part.get_payload.return_value = None

        msg.walk.return_value = [text_part, html_part]

        result = _extract_body(msg)
        assert "Hello World" in result
        assert "<" not in result

    def test_empty_body(self):
        """Should return empty string for empty body."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = None

        result = _extract_body(msg)
        assert result == ""

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace from body."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = b"  \n  Body with whitespace  \n  "

        result = _extract_body(msg)
        assert result == "Body with whitespace"

    def test_malformed_encoding_uses_replace(self):
        """Should handle malformed encoding gracefully with replace."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        # Invalid UTF-8 sequence
        msg.get_payload.return_value = b"\xff\xfe invalid bytes"

        result = _extract_body(msg)
        # Should not raise, should contain replacement characters
        assert isinstance(result, str)


# -- _parse_labels Tests -----------------------------------------------------

class TestParseLabels:
    """Tests for the _parse_labels function."""

    def test_single_label(self):
        assert _parse_labels("Important") == ["Important"]

    def test_multiple_labels(self):
        result = _parse_labels("Important,Work,Follow-up")
        assert result == ["Important", "Work", "Follow-up"]

    def test_empty_string(self):
        assert _parse_labels("") == []

    def test_none_input(self):
        assert _parse_labels(None) == []

    def test_strips_whitespace(self):
        result = _parse_labels("Label1 , Label2 , Label3")
        assert result == ["Label1", "Label2", "Label3"]

    def test_filters_empty_segments(self):
        result = _parse_labels("Label1,,Label2,")
        assert result == ["Label1", "Label2"]


# -- _classify_category Tests ------------------------------------------------

class TestClassifyCategory:
    """Tests for the _classify_category function."""

    def test_gmail_promotions_label(self):
        assert _classify_category("Any subject", "any@sender.com", ["Promotions"]) == "promotions"

    def test_gmail_purchases_label(self):
        assert _classify_category("Any subject", "any@sender.com", ["Purchases"]) == "purchases"

    def test_gmail_updates_label(self):
        assert _classify_category("Any subject", "any@sender.com", ["Updates"]) == "updates"

    def test_gmail_social_label(self):
        assert _classify_category("Any subject", "any@sender.com", ["Social"]) == "social"

    def test_gmail_forums_label(self):
        assert _classify_category("Any subject", "any@sender.com", ["Forums"]) == "forums"

    def test_invoice_keyword(self):
        assert _classify_category("Your invoice #12345", "billing@company.com", []) == "purchases"

    def test_receipt_keyword(self):
        assert _classify_category("Receipt for your purchase", "store@email.com", []) == "purchases"

    def test_order_confirmed_keyword(self):
        assert _classify_category("Order confirmed", "amazon@email.com", []) == "purchases"

    def test_payment_keyword(self):
        assert _classify_category("Payment received", "paypal@email.com", []) == "purchases"

    def test_sale_keyword(self):
        assert _classify_category("50% off everything!", "store@email.com", []) == "promotions"

    def test_discount_keyword(self):
        assert _classify_category("Exclusive discount for you", "marketing@email.com", []) == "promotions"

    def test_noreply_sender(self):
        assert _classify_category("Your statement", "noreply@bank.com", []) == "updates"

    def test_no_reply_sender(self):
        assert _classify_category("Account update", "no-reply@service.com", []) == "updates"

    def test_security_keyword(self):
        assert _classify_category("Security alert: new login", "security@google.com", []) == "security"

    def test_password_keyword(self):
        assert _classify_category("Password reset requested", "accounts@email.com", []) == "security"

    def test_default_inbox(self):
        """Should return 'inbox' for emails matching no category."""
        assert _classify_category("Meeting tomorrow", "colleague@company.com", []) == "inbox"

    def test_case_insensitive_labels(self):
        assert _classify_category("Any", "any@x.com", ["PROMOTIONS"]) == "promotions"

    def test_case_insensitive_subject(self):
        assert _classify_category("YOUR INVOICE", "billing@x.com", []) == "purchases"

    def test_case_insensitive_sender(self):
        assert _classify_category("Statement", "NOREPLY@BANK.COM", []) == "updates"

    def test_label_priority_over_keywords(self):
        """Gmail labels should take priority over keyword matching."""
        # Has "promotions" label but subject mentions "invoice"
        result = _classify_category("Your invoice", "billing@x.com", ["Promotions"])
        assert result == "promotions"


# -- _parse_date Tests -------------------------------------------------------

class TestParseDate:
    """Tests for the _parse_date function."""

    def test_rfc2822_standard(self):
        """Should parse standard RFC 2822 date."""
        result = _parse_date("Mon, 01 Jan 2024 12:00:00")
        assert result == "2024-01-01T12:00:00"

    def test_rfc2822_with_timezone(self):
        """Should parse RFC 2822 with timezone offset."""
        result = _parse_date("Mon, 01 Jan 2024 12:00:00 +0000")
        assert result.startswith("2024-01-01T12:00:00")

    def test_without_weekday(self):
        """Should parse date without weekday."""
        result = _parse_date("01 Jan 2024 12:00:00")
        assert result == "2024-01-01T12:00:00"

    def test_empty_string(self):
        """Should return empty string as-is."""
        assert _parse_date("") == ""

    def test_none_input(self):
        """Should return None as-is."""
        assert _parse_date(None) is None

    def test_unparseable_returns_original(self):
        """Should return original string if no format matches."""
        weird_date = "not-a-real-date"
        result = _parse_date(weird_date)
        assert result == weird_date

    def test_truncates_long_dates(self):
        """Should handle date strings longer than 32 chars."""
        long_date = "Mon, 01 Jan 2024 12:00:00 +0000 (some extra junk data here)"
        result = _parse_date(long_date)
        assert result.startswith("2024-01-01T12:00:00")


# -- load_mbox Tests ---------------------------------------------------------

class TestLoadMbox:
    """Tests for the load_mbox function."""

    def test_load_single_email(self, tmp_path):
        """Should load a single email from MBOX."""
        mbox_path = _create_test_mbox([{
            "from": "sender@example.com",
            "to": "me@example.com",
            "subject": "Test Email",
            "body": "Hello world",
        }])

        try:
            emails = load_mbox(path=mbox_path, limit=1)
            assert len(emails) == 1
            assert emails[0]["from"] == "sender@example.com"
            assert emails[0]["subject"] == "Test Email"
            assert emails[0]["body"] == "Hello world"
            assert emails[0]["id"].startswith("mbox-")
        finally:
            os.unlink(mbox_path)

    def test_load_multiple_emails(self, tmp_path):
        """Should load multiple emails."""
        mbox_path = _create_test_mbox([
            {"from": "a@example.com", "subject": "Email A", "body": "Body A"},
            {"from": "b@example.com", "subject": "Email B", "body": "Body B"},
            {"from": "c@example.com", "subject": "Email C", "body": "Body C"},
        ])

        try:
            emails = load_mbox(path=mbox_path)
            assert len(emails) == 3
        finally:
            os.unlink(mbox_path)

    def test_limit_parameter(self, tmp_path):
        """Should respect limit parameter."""
        mbox_path = _create_test_mbox([
            {"subject": f"Email {i}", "body": f"Body {i}"} for i in range(10)
        ])

        try:
            emails = load_mbox(path=mbox_path, limit=3)
            assert len(emails) == 3
        finally:
            os.unlink(mbox_path)

    def test_offset_parameter(self, tmp_path):
        """Should respect offset parameter."""
        mbox_path = _create_test_mbox([
            {"subject": f"Email {i}", "body": f"Body {i}"} for i in range(10)
        ])

        try:
            all_emails = load_mbox(path=mbox_path)
            offset_emails = load_mbox(path=mbox_path, offset=5)
            assert len(offset_emails) == 5
            # Should skip first 5
            assert offset_emails[0]["subject"] != all_emails[0]["subject"]
        finally:
            os.unlink(mbox_path)

    def test_reverse_order(self, tmp_path):
        """Should reverse email order when reverse=True."""
        mbox_path = _create_test_mbox([
            {"subject": f"Email {i}", "body": f"Body {i}"} for i in range(5)
        ])

        try:
            normal = load_mbox(path=mbox_path, limit=5, reverse=False)
            reversed_emails = load_mbox(path=mbox_path, limit=5, reverse=True)
            assert normal[0]["subject"] != reversed_emails[0]["subject"]
            assert normal[0]["subject"] == reversed_emails[-1]["subject"]
        finally:
            os.unlink(mbox_path)

    def test_timing_enabled_returns_tuple(self, tmp_path):
        """Should return tuple when enable_timing=True."""
        mbox_path = _create_test_mbox([
            {"subject": "Timed Email", "body": "Body"}
        ])

        try:
            result = load_mbox(path=mbox_path, enable_timing=True)
            assert isinstance(result, tuple)
            emails, timing = result
            assert len(emails) == 1
            assert timing["email_count"] == 1
            assert timing["total_load_time_ms"] >= 0
            assert len(timing["per_email_times"]) == 1
            assert "email_id" in timing["per_email_times"][0]
            assert "load_time_ms" in timing["per_email_times"][0]
        finally:
            os.unlink(mbox_path)

    def test_timing_disabled_returns_list(self, tmp_path):
        """Should return list when enable_timing=False."""
        mbox_path = _create_test_mbox([
            {"subject": "Email", "body": "Body"}
        ])

        try:
            result = load_mbox(path=mbox_path, enable_timing=False)
            assert isinstance(result, list)
        finally:
            os.unlink(mbox_path)

    def test_default_timing_disabled(self, tmp_path):
        """Should return list by default (timing off)."""
        mbox_path = _create_test_mbox([{"subject": "Email", "body": "Body"}])
        try:
            result = load_mbox(path=mbox_path)
            assert isinstance(result, list)
        finally:
            os.unlink(mbox_path)

    def test_nonexistent_file_raises_file_not_found(self):
        """Should raise FileNotFoundError for non-existent MBOX."""
        with pytest.raises(FileNotFoundError):
            load_mbox(path="/nonexistent/path/file.mbox")

    def test_negative_offset_raises_value_error(self, tmp_path):
        """Should raise ValueError for negative offset."""
        mbox_path = _create_test_mbox([{"subject": "Email", "body": "Body"}])
        try:
            with pytest.raises(ValueError, match="offset must be non-negative"):
                load_mbox(path=mbox_path, offset=-1)
        finally:
            os.unlink(mbox_path)

    def test_zero_limit_raises_value_error(self, tmp_path):
        """Should raise ValueError for zero limit."""
        mbox_path = _create_test_mbox([{"subject": "Email", "body": "Body"}])
        try:
            with pytest.raises(ValueError, match="limit must be positive"):
                load_mbox(path=mbox_path, limit=0)
        finally:
            os.unlink(mbox_path)

    def test_negative_limit_raises_value_error(self, tmp_path):
        """Should raise ValueError for negative limit."""
        mbox_path = _create_test_mbox([{"subject": "Email", "body": "Body"}])
        try:
            with pytest.raises(ValueError, match="limit must be positive"):
                load_mbox(path=mbox_path, limit=-5)
        finally:
            os.unlink(mbox_path)

    def test_email_dict_has_required_keys(self, tmp_path):
        """Should produce email dicts with all required keys."""
        mbox_path = _create_test_mbox([{
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Key Check",
            "body": "Body content",
            "labels": ["Important", "Work"],
        }])

        try:
            emails = load_mbox(path=mbox_path, limit=1)
            email = emails[0]
            required_keys = ["id", "from", "to", "subject", "date", "labels", "category", "body", "body_preview"]
            for key in required_keys:
                assert key in email, f"Missing key: {key}"
            assert email["labels"] == ["Important", "Work"]
        finally:
            os.unlink(mbox_path)

    def test_body_preview_truncated(self, tmp_path):
        """Should truncate body_preview to 200 chars."""
        long_body = "X" * 500
        mbox_path = _create_test_mbox([{"subject": "Long", "body": long_body}])

        try:
            emails = load_mbox(path=mbox_path, limit=1)
            assert len(emails[0]["body_preview"]) <= 200
            assert len(emails[0]["body"]) == 500  # full body not truncated
        finally:
            os.unlink(mbox_path)

    def test_empty_mbox_returns_empty_list(self, tmp_path):
        """Should return empty list for empty MBOX file."""
        fd, mbox_path = tempfile.mkstemp(suffix=".mbox")
        os.close(fd)

        try:
            emails = load_mbox(path=mbox_path)
            assert emails == []
        finally:
            os.unlink(mbox_path)


# -- count_mbox Tests --------------------------------------------------------

class TestCountMbox:
    """Tests for the count_mbox function."""

    def test_counts_messages(self, tmp_path):
        """Should return correct message count."""
        mbox_path = _create_test_mbox([
            {"subject": f"Email {i}", "body": f"Body {i}"} for i in range(7)
        ])

        try:
            count = count_mbox(path=mbox_path)
            assert count == 7
        finally:
            os.unlink(mbox_path)

    def test_empty_mbox_returns_zero(self, tmp_path):
        """Should return 0 for empty MBOX."""
        fd, mbox_path = tempfile.mkstemp(suffix=".mbox")
        os.close(fd)

        try:
            count = count_mbox(path=mbox_path)
            assert count == 0
        finally:
            os.unlink(mbox_path)

    def test_nonexistent_file_returns_zero(self):
        """Should return 0 for non-existent file."""
        count = count_mbox(path="/nonexistent/path/file.mbox")
        assert count == 0

    def test_uses_default_path_by_default(self):
        """Should use DEFAULT_MBOX_PATH when no path specified."""
        # Default path likely doesn't exist in test env, should return 0
        result = count_mbox()
        assert isinstance(result, int)
        assert result >= 0
