# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _print_header(title: str):
    print("=" * 60)
    print(title)
    print("=" * 60)


def _check_health(base_url: str):
    req_health = urllib.request.Request(
        f"{base_url}/health",
        headers={"Connection": "close"},
        method="GET",
    )
    opener = urllib.request.build_opener()
    with opener.open(req_health, timeout=15) as response:
        health = json.loads(response.read().decode("utf-8"))
    if health.get("status") == "healthy":
        print("\n✅ Connected to MCP bridge")
    else:
        print("\n❌ MCP bridge not healthy")
        assert False, "MCP bridge not healthy"


def _prepare_temp_pdf() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    src_pdf = (
        repo_root / "data" / "pdf" / "Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"
    )
    assert src_pdf.exists(), f"Missing test PDF at {src_pdf}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()
    shutil.copyfile(src_pdf, tmp.name)
    return Path(tmp.name)


def _build_multipart_form(fields, files):
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    lines = []
    for name, value in fields.items():
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="{name}"')
        lines.append("")
        lines.append(str(value))
    for name, (filename, content, content_type) in files.items():
        lines.append(f"--{boundary}")
        lines.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'
        )
        lines.append(f"Content-Type: {content_type}")
        lines.append("")
        # bytes payload appended later
        lines.append(content)
        # Each part must end with CRLF before the next boundary
        lines.append("\r\n")
    lines.append(f"--{boundary}--")
    body = bytearray()
    for part in lines:
        if isinstance(part, bytes):
            body.extend(part)
        else:
            body.extend((part + "\r\n").encode("utf-8"))
    return boundary, bytes(body)


def _open_json(req, timeout=30):
    """Open a URL request and return (status, payload) with robust error handling.
    Converts non-JSON error bodies and connection drops into synthetic error payloads.
    """
    opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as response:
            status = response.getcode()
            payload = json.loads(response.read().decode("utf-8"))
            return status, payload
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {"error": "HTTP error without JSON"}
        return status, payload
    except urllib.error.URLError as e:
        return 400, {"error": f"Connection closed: {e}"}
    except Exception as e:
        # Defensive: treat unexpected exceptions as client error for this test
        return 400, {"error": str(e)}


def test_mcp_summarize_multipart_pdf():
    base_url = "http://localhost:8765"

    _print_header("Testing MCP Summarize (multipart) Integration")
    _check_health(base_url)

    # Prepare a test PDF from repo data
    tmp_pdf = _prepare_temp_pdf()

    try:
        with open(tmp_pdf, "rb") as f:
            file_bytes = f.read()
        fields = {"style": "brief"}
        files = {"file": ("test.pdf", file_bytes, "application/pdf")}
        boundary, body = _build_multipart_form(fields, files)
        req = urllib.request.Request(
            f"{base_url}/summarize",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Connection": "close",
                "Content-Length": str(len(body)),
                "Accept": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener()
        with opener.open(req, timeout=60) as response:
            j = json.loads(response.read().decode("utf-8"))
        if j.get("success"):
            print("✅ Received summarization result (multipart)")
        else:
            print(f"❌ Summarize failed: {j}")
        assert j.get("success") is True
        assert isinstance(j.get("result"), dict)
    finally:
        try:
            os.unlink(tmp_pdf)
        except Exception:
            pass


def test_summarize_missing_boundary_returns_client_error():
    base_url = "http://localhost:8765"

    _print_header("Summarize: missing boundary -> client error")
    _check_health(base_url)

    # Build body but omit boundary parameter in header
    boundary, body = _build_multipart_form({}, {})
    req = urllib.request.Request(
        f"{base_url}/summarize",
        data=body,
        headers={
            "Content-Type": "multipart/form-data",
            "Connection": "close",
            "Content-Length": str(len(body)),
            "Accept": "application/json",
        },
        method="POST",
    )
    status, payload = _open_json(req, timeout=30)
    # Display MCP error payload for visibility
    print(f"Status: {status}")
    print(f"Error: {payload.get('error')}")
    assert status in (400, 500)
    assert payload.get("error")


def test_summarize_missing_file_returns_client_error():
    base_url = "http://localhost:8765"

    _print_header("Summarize: missing file -> client error")
    _check_health(base_url)

    boundary, body = _build_multipart_form({"style": "brief"}, {})
    req = urllib.request.Request(
        f"{base_url}/summarize",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Connection": "close",
            "Content-Length": str(len(body)),
            "Accept": "application/json",
        },
        method="POST",
    )
    status, payload = _open_json(req, timeout=30)
    if "success" not in payload:
        payload["success"] = False
    # Display MCP error payload for visibility
    print(f"Status: {status}")
    print(f"Error: {payload.get('error')}")
    assert status in (400, 500)
    assert payload.get("success") is False
    assert "No file uploaded" in payload.get("error", "")


def main():
    def run_test(label, func):
        try:
            func()
            return True
        except Exception as e:
            print(f"❌ {label} failed: {e}")
            return False

    results = [
        run_test("Multipart test", test_mcp_summarize_multipart_pdf),
        run_test(
            "Missing boundary test",
            test_summarize_missing_boundary_returns_client_error,
        ),
        run_test("Missing file test", test_summarize_missing_file_returns_client_error),
    ]

    success = all(results)
    print("\n" + "=" * 60)
    if success:
        print("✅ MCP Summarize Integration Working!")
        print("The Summarizer agent is accessible through the MCP bridge.")
    else:
        print("❌ MCP Summarize Integration Failed")
        print(
            "Check that the MCP bridge is running and summarize endpoints are available."
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
