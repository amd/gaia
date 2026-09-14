# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.vlm.structured_extraction.StructuredVLMExtractor``.

Covers the VLM-output parsers (table/key-value/schema/chart extraction), the
pure helpers (``_parse_page_range``, ``_parse_time_to_hours``), and the
top-level ``extract()`` orchestration — with the VLM/LLM boundary
(``VLMClient.extract_from_image``) mocked so no real Lemonade server is
required. Malformed-input branches are exercised explicitly since silent
misparsing here would corrupt every downstream consumer (e.g. the EMR flow).
"""

import pytest

from gaia.llm import VLMExtractionError
from gaia.vlm.structured_extraction import StructuredVLMExtractor


@pytest.fixture
def extractor(mocker):
    """A StructuredVLMExtractor with VLMClient replaced by a mock.

    Patches the VLM boundary at construction time so no real LemonadeClient
    is ever created; each test configures ``extractor.vlm.extract_from_image``
    directly.
    """
    mocker.patch("gaia.vlm.structured_extraction.VLMClient")
    return StructuredVLMExtractor()


# ---------------------------------------------------------------------------
# extract_table
# ---------------------------------------------------------------------------


def test_extract_table_parses_json_array(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '[{"name": "John", "age": "30"}, {"name": "Jane", "age": "25"}]'
    )

    rows = extractor.extract_table(b"fake-image")

    assert rows == [
        {"name": "John", "age": "30"},
        {"name": "Jane", "age": "25"},
    ]


def test_extract_table_strips_surrounding_chatter(extractor):
    extractor.vlm.extract_from_image.return_value = (
        "Sure! Here's the extracted table:\n"
        '[{"col1": "value1", "col2": "value2"}]\n'
        "Let me know if you need anything else."
    )

    rows = extractor.extract_table(b"fake-image", table_description="a receipt")

    assert rows == [{"col1": "value1", "col2": "value2"}]


def test_extract_table_empty_array_returns_empty_list(extractor):
    extractor.vlm.extract_from_image.return_value = "[]"

    assert extractor.extract_table(b"fake-image") == []


def test_extract_table_non_list_json_raises(extractor):
    """An object where a list was asked for is a parse failure, not zero rows.

    Returning ``[]`` reads downstream as "this page has no table" (#3555).
    """
    extractor.vlm.extract_from_image.return_value = '{"col1": "value1"}'

    with pytest.raises(VLMExtractionError, match="did not parse as a list"):
        extractor.extract_table(b"fake-image")


def test_extract_table_unparsable_text_raises(extractor):
    extractor.vlm.extract_from_image.return_value = "I could not find a table."

    with pytest.raises(VLMExtractionError):
        extractor.extract_table(b"fake-image")


def test_a_table_parse_failure_names_the_page(extractor):
    extractor.vlm.extract_from_image.return_value = "not json"

    with pytest.raises(VLMExtractionError) as excinfo:
        extractor.extract_table(b"fake-image", page_num=7)

    assert excinfo.value.page_num == 7


# ---------------------------------------------------------------------------
# extract_key_values
# ---------------------------------------------------------------------------


def test_extract_key_values_parses_json_object(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '{"invoice_number": "INV-12345", "total": 150.00, "date": "2024-01-15"}'
    )

    data = extractor.extract_key_values(
        b"fake-image", keys=["invoice_number", "total", "date"]
    )

    assert data == {
        "invoice_number": "INV-12345",
        "total": 150.00,
        "date": "2024-01-15",
    }


def test_extract_key_values_with_descriptions_builds_prompt(extractor):
    extractor.vlm.extract_from_image.return_value = '{"name": "John"}'

    extractor.extract_key_values(
        b"fake-image",
        keys=["name"],
        descriptions={"name": "the patient's full name"},
    )

    _, kwargs = extractor.vlm.extract_from_image.call_args
    assert "the patient's full name" in kwargs["prompt"]


def test_extract_key_values_malformed_output_raises(extractor):
    """A null per key is indistinguishable from fields genuinely absent."""
    extractor.vlm.extract_from_image.return_value = "not valid json at all"

    with pytest.raises(VLMExtractionError, match="genuinely absent"):
        extractor.extract_key_values(b"fake-image", keys=["a", "b", "c"])


def test_extract_key_values_non_dict_json_raises(extractor):
    # VLM returns an array instead of the requested object.
    extractor.vlm.extract_from_image.return_value = '["a", "b"]'

    with pytest.raises(VLMExtractionError):
        extractor.extract_key_values(b"fake-image", keys=["a", "b"])


# ---------------------------------------------------------------------------
# extract_structured
# ---------------------------------------------------------------------------


def test_extract_structured_parses_schema_result(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '{"patient_name": "Jane Smith", "total_cost": 1250.00}'
    )
    schema = {
        "fields": {
            "patient_name": {"type": "string", "description": "patient full name"},
            "total_cost": {
                "type": "number",
                "description": "total billing amount",
                "required": True,
            },
        }
    }

    data = extractor.extract_structured(b"fake-image", schema)

    assert data == {"patient_name": "Jane Smith", "total_cost": 1250.00}


def test_extract_structured_empty_schema_still_calls_vlm(extractor):
    extractor.vlm.extract_from_image.return_value = "{}"

    data = extractor.extract_structured(b"fake-image", schema={})

    assert data == {}
    extractor.vlm.extract_from_image.assert_called_once()


def test_extract_structured_malformed_output_raises(extractor):
    """``{}`` reads as "the page had none of these fields" (#3555)."""
    extractor.vlm.extract_from_image.return_value = "garbage response"

    with pytest.raises(VLMExtractionError, match="did not parse against the schema"):
        extractor.extract_structured(b"fake-image", schema={"fields": {}})


# ---------------------------------------------------------------------------
# _parse_time_to_hours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "time_str, expected",
    [
        ("14:46:38", 14 + 46 / 60.0 + 38 / 3600.0),
        ("01:00", 1.0),
        ("1", 1.0),  # "1" has no ":" so falls through to float("1") below
        ("5", 5.0),
        ("00:00:00", 0.0),
    ],
)
def test_parse_time_to_hours_valid_inputs(extractor, time_str, expected):
    assert extractor._parse_time_to_hours(time_str) == pytest.approx(expected)


@pytest.mark.parametrize("time_str", ["not-a-time", "", "14:"])
def test_parse_time_to_hours_malformed_raises(extractor, time_str):
    """0.0 was summed into ``timeline_totals`` and reported as measured (#3555)."""
    with pytest.raises(VLMExtractionError, match="as a duration"):
        extractor._parse_time_to_hours(time_str)


# ---------------------------------------------------------------------------
# _parse_page_range
# ---------------------------------------------------------------------------


def test_parse_page_range_all(extractor):
    assert extractor._parse_page_range("all", 5) == [1, 2, 3, 4, 5]


def test_parse_page_range_all_case_insensitive(extractor):
    assert extractor._parse_page_range("ALL", 3) == [1, 2, 3]


def test_parse_page_range_dash_range(extractor):
    assert extractor._parse_page_range("2-4", 10) == [2, 3, 4]


def test_parse_page_range_dash_range_with_whitespace(extractor):
    assert extractor._parse_page_range(" 2 - 4 ", 10) == [2, 3, 4]


def test_parse_page_range_single_page(extractor):
    assert extractor._parse_page_range("3", 10) == [3]


def test_parse_page_range_malformed_raises_value_error(extractor):
    with pytest.raises(ValueError):
        extractor._parse_page_range("not-a-page", 10)


# ---------------------------------------------------------------------------
# extract_chart_data
# ---------------------------------------------------------------------------


def test_extract_chart_data_time_hms_decimal_converts_hours(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '{"Active": "14:46:38", "Idle": "00:01:20"}'
    )

    data = extractor.extract_chart_data(
        b"fake-image", categories=["Active", "Idle"], value_format="time_hms_decimal"
    )

    assert data["Active"] == pytest.approx(14 + 46 / 60.0 + 38 / 3600.0)
    assert data["Idle"] == pytest.approx(0 + 1 / 60.0 + 20 / 3600.0)


def test_extract_chart_data_time_hms_decimal_accepts_numeric_values(extractor):
    # VLM occasionally returns numbers directly instead of HH:MM:SS strings.
    extractor.vlm.extract_from_image.return_value = '{"Active": 5, "Idle": 2.5}'

    data = extractor.extract_chart_data(
        b"fake-image", categories=["Active", "Idle"], value_format="time_hms_decimal"
    )

    assert data == {"Active": 5.0, "Idle": 2.5}


def test_extract_chart_data_time_hms_decimal_unexpected_type_raises(extractor):
    """A zero here is summed into timeline_totals and reported as measured."""
    extractor.vlm.extract_from_image.return_value = '{"Active": null, "Idle": [1, 2]}'

    with pytest.raises(VLMExtractionError, match="not a duration"):
        extractor.extract_chart_data(
            b"fake-image",
            categories=["Active", "Idle"],
            value_format="time_hms_decimal",
        )


def test_extract_chart_data_time_hms_returns_strings_as_is(extractor):
    extractor.vlm.extract_from_image.return_value = '{"Active": "14:46:38"}'

    data = extractor.extract_chart_data(
        b"fake-image", categories=["Active"], value_format="time_hms"
    )

    assert data == {"Active": "14:46:38"}


def test_extract_chart_data_number_format(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '{"Q1": 150000, "Q2": 175000, "Q3": 200000, "Q4": 225000}'
    )

    data = extractor.extract_chart_data(
        b"fake-image",
        categories=["Q1", "Q2", "Q3", "Q4"],
        value_format="number",
    )

    assert data == {"Q1": 150000, "Q2": 175000, "Q3": 200000, "Q4": 225000}


def test_extract_chart_data_percentage_format(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '{"Product A": 45.5, "Product B": 32.0, "Product C": 22.5}'
    )

    data = extractor.extract_chart_data(
        b"fake-image",
        categories=["Product A", "Product B", "Product C"],
        value_format="percentage",
    )

    assert data == {"Product A": 45.5, "Product B": 32.0, "Product C": 22.5}


def test_extract_chart_data_auto_format_passthrough(extractor):
    extractor.vlm.extract_from_image.return_value = '{"A": "yes", "B": 3}'

    data = extractor.extract_chart_data(
        b"fake-image", categories=["A", "B"], value_format="auto"
    )

    assert data == {"A": "yes", "B": 3}


@pytest.mark.parametrize("value_format", ["number", "time_hms", "time_hms_decimal"])
def test_extract_chart_data_malformed_raises_for_every_format(extractor, value_format):
    """Zero-filling every category reads as a chart that measured zero."""
    extractor.vlm.extract_from_image.return_value = "not json"

    with pytest.raises(VLMExtractionError, match="did not parse as an object"):
        extractor.extract_chart_data(
            b"fake-image", categories=["Q1", "Q2"], value_format=value_format
        )


# ---------------------------------------------------------------------------
# extract_timeline (thin wrapper over extract_chart_data)
# ---------------------------------------------------------------------------


def test_extract_timeline_delegates_to_chart_data_with_decimal_hours(extractor):
    extractor.vlm.extract_from_image.return_value = (
        '{"Active": "01:30:00", "Offline": "00:45:00"}'
    )

    data = extractor.extract_timeline(b"fake-image", status_types=["Active", "Offline"])

    assert data == {"Active": 1.5, "Offline": 0.75}


# ---------------------------------------------------------------------------
# extract() — top-level orchestration
# ---------------------------------------------------------------------------


def test_extract_raises_file_not_found(extractor, tmp_path):
    with pytest.raises(FileNotFoundError):
        extractor.extract(str(tmp_path / "does-not-exist.png"))


def test_extract_non_pdf_image_with_fields(extractor, tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"fake-png-bytes")

    extractor.vlm.extract_from_image.return_value = '{"name": "John"}'

    result = extractor.extract(str(image_path), extract_fields=["name"])

    assert result["metadata"]["source"] == "page.png"
    assert result["metadata"]["total_pages"] == 1
    assert result["metadata"]["pages_processed"] == 1
    assert result["pages"][0]["page"] == 1
    assert result["pages"][0]["fields"] == {"name": "John"}
    assert "raw_text" in result["pages"][0]
    assert "aggregated_data" not in result


def test_extract_non_pdf_image_with_schema(extractor, tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"fake-png-bytes")

    extractor.vlm.extract_from_image.return_value = '{"total_cost": 42.0}'

    result = extractor.extract(
        str(image_path), schema={"fields": {"total_cost": {"type": "number"}}}
    )

    assert result["pages"][0]["fields"] == {"total_cost": 42.0}


def test_extract_skips_page_with_no_image_bytes(extractor, tmp_path):
    image_path = tmp_path / "empty.png"
    image_path.write_bytes(b"")  # doc_path.read_bytes() -> b"" -> falsy -> skipped

    result = extractor.extract(str(image_path))

    assert result["metadata"]["pages_processed"] == 0
    assert result["pages"] == []


def test_extract_pdf_multipage_aggregates_timeline(extractor, tmp_path, mocker):
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-fake")

    fake_doc = mocker.MagicMock()
    fake_doc.__len__.return_value = 2
    mocker.patch("fitz.open", return_value=fake_doc)
    mocker.patch("gaia.utils.pdf_page_to_image", return_value=b"rendered-page-bytes")

    extractor.vlm.extract_from_image.return_value = (
        '{"Active": "02:00:00", "Idle": "01:30:00"}'
    )

    progress_calls = []
    result = extractor.extract(
        str(pdf_path),
        extract_timelines=True,
        timeline_status_types=["Active", "Idle"],
        on_progress=lambda current, total: progress_calls.append((current, total)),
    )

    assert result["metadata"]["total_pages"] == 2
    assert result["metadata"]["pages_processed"] == 2
    assert result["pages"][0]["timeline"] == {"Active": 2.0, "Idle": 1.5}
    assert result["pages"][1]["timeline"] == {"Active": 2.0, "Idle": 1.5}
    assert result["aggregated_data"]["timeline_totals"] == {
        "Active": 4.0,
        "Idle": 3.0,
    }
    assert progress_calls == [(1, 2), (2, 2)]
    fake_doc.close.assert_called_once()


# ---------------------------------------------------------------------------
# One page that cannot be read is a failed PAGE, not a failed document (#3555)
# ---------------------------------------------------------------------------


@pytest.fixture
def two_page_doc(tmp_path, mocker):
    """A two-page PDF whose paging and rendering are stubbed."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    doc = mocker.MagicMock()
    doc.__len__.return_value = 2
    mocker.patch.dict(
        "sys.modules", {"fitz": mocker.MagicMock(open=mocker.Mock(return_value=doc))}
    )
    # Imported inside extract(), so patch it at its source module.
    mocker.patch("gaia.utils.pdf_page_to_image", return_value=b"fake-image")
    return pdf


class TestAFailedPageDoesNotDiscardTheDocument:
    def test_the_good_page_still_comes_back(self, extractor, two_page_doc):
        # Page 1 parses; page 2's table answer is prose, which models do.
        extractor.vlm.extract_from_image.side_effect = [
            '[{"col": "value"}]',  # page 1 table
            "page one text",  # page 1 raw text
            "There is no table on this page.",  # page 2 table -> raises
        ]

        result = extractor.extract(str(two_page_doc), extract_tables=True)

        assert [p["page"] for p in result["pages"]] == [1, 2]
        assert result["pages"][0]["tables"] == [{"col": "value"}]

    def test_the_failed_page_is_named(self, extractor, two_page_doc):
        extractor.vlm.extract_from_image.side_effect = [
            '[{"col": "value"}]',
            "page one text",
            "There is no table on this page.",
        ]

        result = extractor.extract(str(two_page_doc), extract_tables=True)

        assert result["metadata"]["pages_failed"] == [2]
        assert "error" in result["pages"][1]

    def test_a_clean_document_reports_no_failures(self, extractor, two_page_doc):
        extractor.vlm.extract_from_image.return_value = "[]"

        result = extractor.extract(str(two_page_doc), extract_tables=True)

        assert result["metadata"]["pages_failed"] == []
        assert all("error" not in p for p in result["pages"])

    def test_a_failed_page_contributes_nothing_to_the_totals(
        self, extractor, two_page_doc
    ):
        """The zeros this whole change exists to stop reporting as measured."""
        extractor.vlm.extract_from_image.side_effect = [
            '{"Active": "02:00:00"}',  # page 1 timeline
            "page one text",
            "could not read the chart",  # page 2 timeline -> raises
        ]

        result = extractor.extract(
            str(two_page_doc),
            extract_timelines=True,
            timeline_status_types=["Active"],
        )

        assert result["metadata"]["pages_failed"] == [2]
        assert result["aggregated_data"]["timeline_totals"] == {"Active": 2.0}
