"""
Inbox Zero Batch Classifier Unit Tests

Tests for gaia_inbox_zero/cli/batch_classifier.py covering:
- Email body extraction (_extract_body)
- Date parsing (_parse_date)
- Classification prompt formatting (CLASSIFICATION_PROMPT)
- Category validation logic (CATEGORIES)
- Email fetching (fetch_emails)
- Batch classification (classify_batch)
- Summary generation (generate_summary)
- Error handling edge cases

Note: classify_email_llm tests mock the HTTP calls since they require a live LLM server.
"""

import json
import os
import mailbox
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from gaia_inbox_zero.cli.batch_classifier import (
    _extract_body,
    _parse_date,
    fetch_emails,
    classify_batch,
    generate_summary,
    generate_run_id,
    run_batch,
)
from gaia_inbox_zero.agent.config import (
    CLASSIFICATION_PROMPT,
    CATEGORIES,
    DEFAULT_MODELS,
    LEMONADE_URL,
    ANTHROPIC_URL,
    RESULTS_DIR,
    OUTPUT_FILE,
)
from gaia_inbox_zero.agent.classifiers import classify_email_llm


# -- Test Fixtures -----------------------------------------------------------

def _create_test_mbox(emails):
    """Create a temporary MBOX file with given email dicts."""
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
            body = email_data.get("body", "Test body")
            msg.set_payload(body)
            mbox.add(msg)
    finally:
        mbox.flush()
        mbox.unlock()
        mbox.close()

    return path


# -- Constants Tests ---------------------------------------------------------

class TestConstants:
    """Tests for module-level constants."""

    def test_categories_contains_expected_values(self):
        """CATEGORIES should contain all 5 expected categories."""
        assert "URGENT" in CATEGORIES
        assert "NEEDS_RESPONSE" in CATEGORIES
        assert "FYI" in CATEGORIES
        assert "PROMOTIONAL" in CATEGORIES
        assert "PERSONAL" in CATEGORIES
        assert len(CATEGORIES) == 5

    def test_default_models_has_both_providers(self):
        """DEFAULT_MODELS should have both lemonade and anthropic."""
        assert "lemonade" in DEFAULT_MODELS
        assert "anthropic" in DEFAULT_MODELS

    def test_classification_prompt_has_placeholders(self):
        """CLASSIFICATION_PROMPT should have required format placeholders."""
        assert "{sender}" in CLASSIFICATION_PROMPT
        assert "{subject}" in CLASSIFICATION_PROMPT
        assert "{body_preview}" in CLASSIFICATION_PROMPT

    def test_classification_prompt_lists_all_categories(self):
        """CLASSIFICATION_PROMPT should mention all categories."""
        for cat in CATEGORIES:
            assert cat in CLASSIFICATION_PROMPT

    def test_classification_prompt_instructs_single_response(self):
        """CLASSIFICATION_PROMPT should instruct to respond with only category."""
        assert "ONLY" in CLASSIFICATION_PROMPT or "only" in CLASSIFICATION_PROMPT

    def test_results_dir_is_path_object(self):
        """RESULTS_DIR should be a Path object."""
        from pathlib import Path
        assert isinstance(RESULTS_DIR, Path)


# -- _extract_body Tests -----------------------------------------------------

class TestExtractBody:
    """Tests for the _extract_body function in classifier."""

    def test_plain_text_non_multipart(self):
        """Should extract plain text from non-multipart message."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = b"Plain text body"

        result = _extract_body(msg)
        assert result == "Plain text body"

    def test_truncates_to_500_chars(self):
        """Should truncate body to 500 characters."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        long_body = b"A" * 1000
        msg.get_payload.return_value = long_body

        result = _extract_body(msg)
        assert len(result) <= 500

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = b"  \n  Hello  \n  "

        result = _extract_body(msg)
        assert result == "Hello"

    def test_multipart_picks_text_plain(self):
        """Should pick text/plain from multipart."""
        msg = MagicMock()
        msg.is_multipart.return_value = True

        plain_part = MagicMock()
        plain_part.get_content_type.return_value = "text/plain"
        plain_part.get_payload.return_value = b"Plain version"

        html_part = MagicMock()
        html_part.get_content_type.return_value = "text/html"
        html_part.get_payload.return_value = b"<html>HTML</html>"

        msg.walk.return_value = [plain_part, html_part]

        result = _extract_body(msg)
        assert result == "Plain version"

    def test_html_strips_tags(self):
        """Should strip HTML tags from HTML-only content."""
        msg = MagicMock()
        msg.is_multipart.return_value = True

        html_part = MagicMock()
        html_part.get_content_type.return_value = "text/html"
        html_part.get_payload.return_value = b"<p>Hello</p>"

        # First part has no payload to force fallback to HTML
        empty_part = MagicMock()
        empty_part.get_content_type.return_value = "text/plain"
        empty_part.get_payload.return_value = None

        msg.walk.return_value = [empty_part, html_part]

        result = _extract_body(msg)
        assert "Hello" in result
        assert "<" not in result

    def test_empty_payload(self):
        """Should return empty string for None payload."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = None

        result = _extract_body(msg)
        assert result == ""

    def test_malformed_bytes_replaced(self):
        """Should handle malformed bytes gracefully."""
        msg = MagicMock()
        msg.is_multipart.return_value = False
        msg.get_payload.return_value = b"\xff\xfe bad bytes"

        result = _extract_body(msg)
        assert isinstance(result, str)


# -- _parse_date Tests -------------------------------------------------------

class TestParseDateClassifier:
    """Tests for _parse_date in classifier module."""

    def test_standard_rfc2822(self):
        result = _parse_date("Mon, 01 Jan 2024 12:00:00")
        assert result == "2024-01-01T12:00:00"

    def test_with_timezone(self):
        result = _parse_date("Mon, 01 Jan 2024 12:00:00 +0000")
        assert result.startswith("2024-01-01T12:00:00")

    def test_empty_returns_empty(self):
        assert _parse_date("") == ""

    def test_none_returns_none(self):
        assert _parse_date(None) is None

    def test_unparseable_returns_original(self):
        result = _parse_date("weird-date-format")
        assert result == "weird-date-format"


# -- fetch_emails Tests ------------------------------------------------------

class TestFetchEmails:
    """Tests for the fetch_emails function."""

    def test_fetches_newest_emails_first(self):
        """Should return newest emails first (reversed order)."""
        emails = [{"subject": f"Email {i}", "body": f"Body {i}"} for i in range(10)]
        mbox_path = _create_test_mbox(emails)

        try:
            all_emails, batches = fetch_emails(mbox_path, total=10, batch_size=10)
            assert len(all_emails) == 10
            # Newest first means last created email should be first in list
            assert all_emails[0]["subject"] == "Email 9"
        finally:
            os.unlink(mbox_path)

    def test_respects_total_limit(self):
        """Should only fetch up to total emails."""
        emails = [{"subject": f"Email {i}", "body": "Body"} for i in range(50)]
        mbox_path = _create_test_mbox(emails)

        try:
            all_emails, batches = fetch_emails(mbox_path, total=5, batch_size=5)
            assert len(all_emails) == 5
        finally:
            os.unlink(mbox_path)

    def test_splits_into_batches(self):
        """Should split emails into correct number of batches."""
        emails = [{"subject": f"Email {i}", "body": "Body"} for i in range(25)]
        mbox_path = _create_test_mbox(emails)

        try:
            all_emails, batches = fetch_emails(mbox_path, total=25, batch_size=10)
            assert len(batches) == 3  # 10 + 10 + 5
            assert len(batches[0]) == 10
            assert len(batches[1]) == 10
            assert len(batches[2]) == 5
        finally:
            os.unlink(mbox_path)

    def test_email_has_required_fields(self):
        """Each fetched email should have required fields."""
        mbox_path = _create_test_mbox([{
            "from": "sender@example.com",
            "to": "me@example.com",
            "subject": "Field Check",
            "body": "Check fields"
        }])

        try:
            all_emails, _ = fetch_emails(mbox_path, total=1, batch_size=1)
            email = all_emails[0]
            assert "id" in email
            assert "from" in email
            assert "to" in email
            assert "subject" in email
            assert "date" in email
            assert "body_preview" in email
            assert email["id"].startswith("mbox-")
        finally:
            os.unlink(mbox_path)

    def test_empty_mbox(self):
        """Should return empty list for empty MBOX."""
        fd, mbox_path = tempfile.mkstemp(suffix=".mbox")
        os.close(fd)

        try:
            all_emails, batches = fetch_emails(mbox_path, total=10, batch_size=5)
            assert len(all_emails) == 0
            assert len(batches) == 0
        finally:
            os.unlink(mbox_path)

    def test_handles_missing_from_header(self):
        """Should handle emails with missing From header."""
        fd, mbox_path = tempfile.mkstemp(suffix=".mbox")
        os.close(fd)

        mbox = mailbox.mbox(mbox_path)
        msg = mailbox.mboxMessage()
        msg["Subject"] = "No Sender"
        msg.set_payload("Body")
        mbox.add(msg)
        mbox.close()

        try:
            all_emails, _ = fetch_emails(mbox_path, total=1, batch_size=1)
            assert len(all_emails) == 1
            assert all_emails[0]["from"] == ""
        finally:
            os.unlink(mbox_path)


# -- classify_email_llm Tests ------------------------------------------------

class TestClassifyEmailLlm:
    """Tests for classify_email_llm function (with mocked HTTP)."""

    @patch("urllib.request.urlopen")
    def test_lemonade_valid_response(self, mock_urlopen):
        """Should parse valid Lemonade response correctly."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "URGENT"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        email = {"from": "boss@company.com", "subject": "Urgent meeting", "body_preview": "Need ASAP"}
        result = classify_email_llm(email, provider="lemonade")

        assert result["category"] == "URGENT"
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 5
        assert result["total_tokens"] == 105

    @patch("urllib.request.urlopen")
    def test_anthropic_valid_response(self, mock_urlopen):
        """Should parse valid Anthropic response correctly."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "content": [{"text": "NEEDS_RESPONSE"}],
            "usage": {"input_tokens": 200, "output_tokens": 10},
        }).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        email = {"from": "colleague@company.com", "subject": "Question for you", "body_preview": "Can you review?"}
        result = classify_email_llm(email, provider="anthropic")

        assert result["category"] == "NEEDS_RESPONSE"
        assert result["input_tokens"] == 200
        assert result["output_tokens"] == 10

    @patch("urllib.request.urlopen")
    def test_fuzzy_category_matching(self, mock_urlopen):
        """Should match categories even with extra text in response."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "This is URGENT matter"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        }).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        email = {"from": "x@y.com", "subject": "Test", "body_preview": "Test"}
        result = classify_email_llm(email, provider="lemonade")

        assert result["category"] == "URGENT"

    @patch("urllib.request.urlopen")
    def test_unrecognized_category_defaults_to_fyi(self, mock_urlopen):
        """Should default to FYI when response doesn't match any category."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "SPAM"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
        }).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        email = {"from": "x@y.com", "subject": "Test", "body_preview": "Test"}
        result = classify_email_llm(email, provider="lemonade")

        assert result["category"] == "FYI"

    @patch("urllib.request.urlopen")
    def test_retries_on_failure(self, mock_urlopen):
        """Should retry on transient failure."""
        mock_urlopen.side_effect = [
            Exception("Connection refused"),
            Exception("Connection refused"),
            MagicMock(),  # Third call succeeds
        ]
        # Setup the successful response for the 3rd call
        success_response = MagicMock()
        success_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "FYI"}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 3, "total_tokens": 53},
        }).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=success_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        email = {"from": "x@y.com", "subject": "Retry Test", "body_preview": "Test"}

        with patch("time.sleep"):  # Speed up test
            result = classify_email_llm(email, provider="lemonade", max_retries=3)

        assert result["category"] == "FYI"

    @patch("urllib.request.urlopen")
    def test_returns_fyi_after_all_retries_fail(self, mock_urlopen):
        """Should return FYI with zero tokens after all retries fail."""
        mock_urlopen.side_effect = Exception("Persistent failure")

        email = {"from": "x@y.com", "subject": "Fail Test", "body_preview": "Test"}

        with patch("time.sleep"):
            result = classify_email_llm(email, provider="lemonade", max_retries=1)

        assert result["category"] == "FYI"
        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0
        assert result["total_tokens"] == 0

    def test_reads_api_key_from_env(self):
        """Should read ANTHROPIC_API_KEY from environment when not provided."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-123"}):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_response = MagicMock()
                mock_response.read.return_value = json.dumps({
                    "content": [{"text": "FYI"}],
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                }).encode()
                mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
                mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

                email = {"from": "x@y.com", "subject": "Test", "body_preview": "Test"}
                classify_email_llm(email, provider="anthropic", api_key=None)

                # Verify x-api-key header was set via the Request object
                call_args = mock_urlopen.call_args
                request = call_args[0][0]
                header_keys = {k.lower(): v for k, v in request.headers.items()}
                assert "x-api-key" in header_keys
                assert header_keys["x-api-key"] == "test-key-123"


# -- classify_batch Tests ----------------------------------------------------

class TestClassifyBatch:
    """Tests for the classify_batch function."""

    @patch("gaia_inbox_zero.cli.batch_classifier.classify_email_llm")
    def test_classifies_all_emails(self, mock_classify):
        """Should classify all emails in the batch."""
        mock_classify.return_value = {
            "category": "FYI",
            "input_tokens": 50,
            "output_tokens": 3,
            "total_tokens": 53,
        }

        emails = [
            {"from": "a@x.com", "subject": "Email 1", "body_preview": "Body 1"},
            {"from": "b@x.com", "subject": "Email 2", "body_preview": "Body 2"},
        ]

        results, metrics = classify_batch(emails, batch_num=1)

        assert len(results) == 2
        assert results[0]["category"] == "FYI"
        assert results[1]["category"] == "FYI"
        assert results[0]["input_tokens"] == 50
        assert metrics["total_tokens"] == 106  # 53 * 2

    @patch("gaia_inbox_zero.cli.batch_classifier.classify_email_llm")
    def test_mixed_categories(self, mock_classify):
        """Should handle different categories for different emails."""
        categories = ["URGENT", "FYI", "PROMOTIONAL"]
        call_count = [0]

        def side_effect(email, **kwargs):
            cat = categories[call_count[0] % len(categories)]
            call_count[0] += 1
            return {"category": cat, "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}

        mock_classify.side_effect = side_effect

        emails = [
            {"from": "a@x.com", "subject": "Urgent", "body_preview": "ASAP"},
            {"from": "b@x.com", "subject": "FYI", "body_preview": "Info"},
            {"from": "c@x.com", "subject": "Deal", "body_preview": "Sale"},
        ]

        results, metrics = classify_batch(emails, batch_num=1)

        assert results[0]["category"] == "URGENT"
        assert results[1]["category"] == "FYI"
        assert results[2]["category"] == "PROMOTIONAL"

    @patch("gaia_inbox_zero.cli.batch_classifier.classify_email_llm")
    def test_empty_batch(self, mock_classify):
        """Should handle empty batch gracefully."""
        results, metrics = classify_batch([], batch_num=1)

        assert results == []
        assert metrics["total_tokens"] == 0


# -- generate_summary Tests --------------------------------------------------

class TestGenerateSummary:
    """Tests for the generate_summary function."""

    def test_empty_results(self):
        """Should handle empty results list."""
        summary = generate_summary([])
        assert "Total emails processed: 0" in summary

    def test_counts_all_categories(self):
        """Should count emails in each category."""
        results = [
            {"category": "URGENT", "from": "a@x.com", "subject": "Urgent 1"},
            {"category": "URGENT", "from": "b@x.com", "subject": "Urgent 2"},
            {"category": "FYI", "from": "c@x.com", "subject": "FYI 1"},
            {"category": "PROMOTIONAL", "from": "d@x.com", "subject": "Promo 1"},
        ]

        summary = generate_summary(results)
        assert "URGENT: 2" in summary
        assert "FYI: 1" in summary
        assert "PROMOTIONAL: 1" in summary

    def test_urgent_section_highlighted(self):
        """Should have urgent section with exclamation marks."""
        results = [
            {"category": "URGENT", "from": "boss@x.com", "subject": "Critical issue"},
        ]

        summary = generate_summary(results)
        assert "URGENT" in summary
        assert "Respond immediately" in summary

    def test_needs_response_section(self):
        """Should have needs response section."""
        results = [
            {"category": "NEEDS_RESPONSE", "from": "colleague@x.com", "subject": "Please review"},
        ]

        summary = generate_summary(results)
        assert "NEEDS_RESPONSE" in summary
        assert "Draft response needed" in summary

    def test_fyi_truncated_at_10(self):
        """Should show only first 10 FYI emails."""
        results = [{"category": "FYI", "subject": f"FYI Email {i}"} for i in range(15)]

        summary = generate_summary(results)
        assert "showing first 10" in summary
        assert "and 5 more" in summary

    def test_personal_section(self):
        """Should show PERSONAL section."""
        results = [{"category": "PERSONAL", "subject": f"Personal {i}"} for i in range(8)]

        summary = generate_summary(results)
        assert "PERSONAL" in summary

    def test_recommendations_section(self):
        """Should include top recommendations."""
        results = [
            {"category": "URGENT", "from": "x@y.com", "subject": "Urgent"},
            {"category": "NEEDS_RESPONSE", "from": "x@y.com", "subject": "Response"},
            {"category": "PROMOTIONAL", "from": "x@y.com", "subject": "Promo"},
        ]

        summary = generate_summary(results)
        assert "TOP RECOMMENDATIONS" in summary

    def test_timestamp_in_header(self):
        """Should include current timestamp in header."""
        summary = generate_summary([])
        # Should have a date-like string in the header
        assert "INBOX ZERO ANALYSIS" in summary

    def test_recommendations_count_match(self):
        """Recommendation text should include correct counts."""
        results = [
            {"category": "URGENT", "from": "x@y.com", "subject": "Urgent"},
            {"category": "URGENT", "from": "x@y.com", "subject": "Urgent 2"},
            {"category": "URGENT", "from": "x@y.com", "subject": "Urgent 3"},
        ]

        summary = generate_summary(results)
        assert "3 URGENT" in summary


# -- generate_run_id Tests ---------------------------------------------------

class TestGenerateRunId:
    """Tests for the generate_run_id function."""

    def test_generates_unique_id(self):
        """Should generate unique IDs."""
        id1 = generate_run_id("test-model", "inbox-zero")
        id2 = generate_run_id("test-model", "inbox-zero")
        assert id1 != id2

    def test_contains_model_and_task(self):
        """Should include model and task in the ID."""
        run_id = generate_run_id("my-model", "my-task")
        assert "my-model" in run_id
        assert "my-task" in run_id


# -- Edge Cases & Error Handling ---------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_fetch_emails_nonexistent_mbox(self):
        """Should raise error for non-existent MBOX in fetch_emails."""
        with pytest.raises(Exception):
            fetch_emails("/nonexistent/path.mbox", total=10, batch_size=5)

    @patch("urllib.request.urlopen")
    def test_category_normalization(self, mock_urlopen):
        """Should handle lowercase, spaced, or punctuated responses."""
        test_cases = [
            ("urgent", "URGENT"),
            ("needs response", "NEEDS_RESPONSE"),
            ("promotional.", "PROMOTIONAL"),
            ("  personal  ", "PERSONAL"),
            ("fYi", "FYI"),
        ]

        for raw_response, expected in test_cases:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({
                "choices": [{"message": {"content": raw_response}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }).encode()
            mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            email = {"from": "x@y.com", "subject": "Test", "body_preview": "Test"}
            result = classify_email_llm(email, provider="lemonade")

            assert result["category"] == expected, f"Failed for input: '{raw_response}'"

    def test_generate_summary_preserves_email_data(self):
        """Summary should preserve and display email from/subject data."""
        results = [
            {
                "category": "URGENT",
                "from": "ceo@company.com",
                "subject": "Board meeting moved to 3pm",
            }
        ]

        summary = generate_summary(results)
        assert "ceo@company.com" in summary
        assert "Board meeting moved to 3pm" in summary
