# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Small, stdlib-only client for testing a deployed GAIA HTTP worker."""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sys
import uuid
import warnings
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ClientError(RuntimeError):
    """An actionable HTTP or stream failure."""


class RequestRejected(ClientError):
    """A definitive HTTP rejection: this request does not own a live run."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a worker credential to another endpoint.
        return None


def events(response):
    """Decode SSE data frames, including comments and multiline JSON."""
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                event = json.loads("\n".join(data))
                if not isinstance(event, dict) or not isinstance(
                    event.get("type"), str
                ):
                    raise ClientError(
                        "Invalid GAIA event: expected an object with a type"
                    )
                yield event
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise ClientError("Stream ended in an incomplete SSE frame")


class Client:
    def __init__(self, url, token, timeout=120):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ClientError(
                "URL must be HTTP(S), without credentials, query or fragment"
            )
        if not token.strip():
            raise ClientError("Set GAIA_GAIA_SIDECAR_TOKEN or use --token-file")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ClientError("Timeout must be finite and positive")
        self.url = url.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirects())

    def open(self, path, body=None, timeout=None):
        request = Request(
            self.url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        try:
            return self.opener.open(request, timeout=timeout or self.timeout)
        except HTTPError as exc:
            detail = exc.read(8192).decode("utf-8", errors="replace")
            raise RequestRejected(f"HTTP {exc.code} from {path}: {detail}") from exc
        except URLError as exc:
            raise ClientError(f"Cannot reach GAIA: {exc.reason}") from exc
        except (OSError, HTTPException) as exc:
            raise ClientError(f"Cannot reach GAIA: {exc}") from exc

    def json(self, path, body=None, timeout=None):
        with self.open(path, body, timeout) as response:
            return json.load(response)

    def cancel(self, run_id):
        return self.json(f"/v1/gaia/query/{uuid.UUID(run_id)}/cancel", {}, timeout=10)

    def query(self, body, on_event):
        accepted = False
        try:
            with self.open("/v1/gaia/query", body) as response:
                accepted = True
                if response.headers.get_content_type() != "text/event-stream":
                    raise ClientError("Query response is not an SSE stream")
                for event in events(response):
                    on_event(event)
                    if event["type"] in {"final", "error"}:
                        return 0 if event["type"] == "final" else 1
                raise ClientError("Stream disconnected before a final/error event")
        except BaseException as failure:
            if isinstance(failure, RequestRejected) and not accepted:
                # A duplicate run ID returns 409; cancelling it would stop
                # someone else's accepted request.
                raise
            # Includes Ctrl-C and a broken stream. Do not leave uncertain work
            # running, and do not retry queries that may have executed tools.
            try:
                self.cancel(body["run_id"])
            except (ClientError, OSError, ValueError, HTTPException) as exc:
                print(f"Cancellation could not be confirmed: {exc}", file=sys.stderr)
            raise


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--url", default=os.getenv("GAIA_SERVICE_URL", "http://127.0.0.1:8080")
    )
    result.add_argument(
        "--token-file", default=os.getenv("GAIA_GAIA_SIDECAR_TOKEN_FILE")
    )
    result.add_argument(
        "--timeout", type=float, default=120, help="Socket idle timeout in seconds"
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "status", help="Check liveness, authenticated model diagnostics and readiness"
    )
    query = commands.add_parser("query", help="Run a task and stream its events")
    query.add_argument("prompt", help="Task text; '-' reads stdin")
    query.add_argument("--run-id", type=uuid.UUID, default=None)
    query.add_argument("--session-id")
    query.add_argument(
        "--context-file", type=Path, help="JSON transcript array from prior turns"
    )
    query.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Override the server step default within its ceiling",
    )
    query.add_argument(
        "--json", action="store_true", help="Emit canonical JSONL events to stdout"
    )
    query.add_argument(
        "--interactive",
        action="store_true",
        help="Answer mid-run questions at the terminal",
    )
    cancel = commands.add_parser("cancel", help="Cancel a run by its printed UUID")
    cancel.add_argument("run_id", type=uuid.UUID)
    respond = commands.add_parser(
        "respond", help="Answer a pending question on another open stream"
    )
    respond.add_argument("run_id", type=uuid.UUID)
    respond.add_argument("request_id")
    respond.add_argument("response")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        token = (
            Path(args.token_file).read_text().strip()
            if args.token_file
            else os.getenv("GAIA_GAIA_SIDECAR_TOKEN", "")
        )
        client = Client(args.url, token, args.timeout)
        if args.command == "status":
            for path in ("/health", "/version", "/v1/gaia/init", "/ready"):
                print(
                    json.dumps({"path": path, "result": client.json(path)}), flush=True
                )
            return 0
        if args.command == "cancel":
            print(json.dumps(client.cancel(str(args.run_id))))
            return 0
        if args.command == "respond":
            print(
                json.dumps(
                    client.json(
                        f"/v1/gaia/query/{args.run_id}/respond",
                        {"request_id": args.request_id, "response": args.response},
                    )
                )
            )
            return 0
        if args.max_steps is not None and args.max_steps < 1:
            raise ClientError("--max-steps must be positive")
        if args.interactive and not sys.stdin.isatty():
            raise ClientError("--interactive requires terminal input")
        prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
        if not prompt.strip():
            raise ClientError("Prompt must not be empty")
        context = json.loads(args.context_file.read_text()) if args.context_file else []
        if not isinstance(context, list):
            raise ClientError("Context file must contain a JSON array")
        run_id = str(args.run_id or uuid.uuid4())
        print(f"Run: {run_id}", file=sys.stderr, flush=True)
        body = {
            "query": prompt,
            "run_id": run_id,
            "context": context,
            "can_answer_questions": args.interactive,
            "can_confirm_tools": args.interactive,
        }
        if args.max_steps is not None:
            body["max_steps"] = args.max_steps
        if args.session_id:
            body["session_id"] = args.session_id

        def display(event):
            kind = event["type"]
            if args.json:
                print(json.dumps(event, ensure_ascii=False), flush=True)
            elif kind == "final":
                print(event.get("answer", ""), flush=True)
            elif kind == "token":
                print(event.get("delta", ""), end="", file=sys.stderr, flush=True)
            else:
                print(
                    json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True
                )
            if kind == "needs_confirmation" and args.interactive:
                print(
                    json.dumps(event.get("arguments", {}), indent=2),
                    file=sys.stderr,
                    flush=True,
                )
                print(
                    "Approve this call once? [y/N]: ",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                answer = sys.stdin.readline()
                if not answer:
                    raise ClientError("Input closed while approving a tool")
                delivered = client.json(
                    f"/v1/gaia/query/{run_id}/confirm",
                    {
                        "confirm_id": event["confirm_id"],
                        "approved": answer.strip().lower() in {"y", "yes"},
                    },
                )
                if (
                    not isinstance(delivered, dict)
                    or delivered.get("delivered") is not True
                ):
                    raise ClientError("GAIA did not acknowledge the tool decision")
            if kind == "needs_input" and args.interactive:
                if event.get("sensitive") is True:
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("error", getpass.GetPassWarning)
                            answer = getpass.getpass(
                                "Answer (hidden): ", stream=sys.stderr
                            )
                    except (getpass.GetPassWarning, EOFError) as exc:
                        raise ClientError(
                            "Cannot read a hidden answer securely; use a terminal with echo control."
                        ) from exc
                else:
                    print("Answer: ", end="", file=sys.stderr, flush=True)
                    answer = sys.stdin.readline()
                    if not answer:
                        raise ClientError("Input closed while answering a question")
                delivered = client.json(
                    f"/v1/gaia/query/{run_id}/respond",
                    {
                        "request_id": event["request_id"],
                        "response": answer.rstrip("\n"),
                    },
                )
                if (
                    not isinstance(delivered, dict)
                    or delivered.get("delivered") is not True
                ):
                    raise ClientError(
                        "GAIA did not acknowledge the answer; cancellation requested"
                    )

        return client.query(body, display)
    except KeyboardInterrupt:
        print("Interrupted; cancellation requested.", file=sys.stderr)
        return 130
    except (ClientError, OSError, ValueError, HTTPException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
