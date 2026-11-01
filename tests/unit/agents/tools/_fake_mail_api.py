# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""In-process Gmail v1 and Graph v1.0 mail APIs for the thread-tool tests.

The real backends talk to these over ``httpx.MockTransport``, so a test drives
the same parsing the tools ship with and can assert every outgoing request.
Each fake refuses a request a real provider would refuse (an unknown path, a
Graph ``$filter`` combined with ``$orderby``) instead of answering it.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import httpx

from gaia.agents.tools._email.gmail import GmailReadBackend
from gaia.agents.tools._email.graph import OutlookReadBackend

USER = "me@example.com"
NOW_MS = 1_790_000_000_000
DAY_MS = 24 * 60 * 60 * 1000


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _iso(ms: int) -> str:
    stamp = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


class Mail:
    """One provider-agnostic message the fakes render in their own wire shape."""

    def __init__(
        self,
        mid: str,
        thread: str,
        *,
        sender: str,
        to: str = USER,
        cc: str = "",
        subject: str = "Subject",
        body: str = "",
        age_days: float = 1,
        labels: Optional[List[str]] = None,
        html: bool = False,
        draft: bool = False,
    ):
        self.mid = mid
        self.thread = thread
        self.sender = sender
        self.to = to
        self.cc = cc
        self.subject = subject
        self.body = body
        self.ms = int(NOW_MS - age_days * DAY_MS)
        self.labels = list(labels or ["INBOX"])
        self.html = html
        self.draft = draft


def outbound(mid, thread, *, to, body, age_days, subject="Re:"):
    return Mail(
        mid,
        thread,
        sender=f"Me <{USER}>",
        to=to,
        subject=subject,
        body=body,
        age_days=age_days,
        labels=["SENT"],
    )


class FakeGmailApi:
    def __init__(self, mails: List[Mail], *, user: str = USER):
        self.mails = list(mails)
        self.user = user
        self.requests: List[httpx.Request] = []

    def _raw(self, m: Mail) -> Dict[str, Any]:
        labels = list(m.labels) + (["DRAFT"] if m.draft else [])
        return {
            "id": m.mid,
            "threadId": m.thread,
            "labelIds": labels,
            "snippet": m.body[:80],
            "internalDate": str(m.ms),
            "payload": {
                "mimeType": "text/html" if m.html else "text/plain",
                "filename": "",
                "headers": [
                    {"name": "Subject", "value": m.subject},
                    {"name": "From", "value": m.sender},
                    {"name": "To", "value": m.to},
                    {"name": "Cc", "value": m.cc},
                    *(
                        [{"name": "Auto-Submitted", "value": "auto-replied"}]
                        if "AUTO_REPLY" in m.labels
                        else []
                    ),
                    *(
                        [{"name": "X-Autoreply", "value": "yes"}]
                        if "X_AUTOREPLY" in m.labels
                        else []
                    ),
                ],
                "body": {"size": len(m.body), "data": _b64(m.body)},
            },
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        prefix = "/gmail/v1/users/me"
        path = request.url.path
        if not path.startswith(prefix):
            return httpx.Response(404, json={"error": {"code": 404}})
        rest = path[len(prefix) :].strip("/").split("/")
        query = parse_qs(request.url.query.decode("ascii"))
        if rest == ["profile"]:
            return httpx.Response(200, json={"emailAddress": self.user})
        if rest == ["labels"]:
            used = sorted({lid for m in self.mails for lid in m.labels})
            return httpx.Response(
                200, json={"labels": [{"id": lid, "name": lid} for lid in used]}
            )
        if rest == ["messages"]:
            wanted = set(query.get("labelIds") or [])
            limit = int((query.get("maxResults") or ["100"])[0])
            hits = [m for m in self.mails if wanted <= set(m.labels)]
            hits.sort(key=lambda m: m.ms, reverse=True)
            page = [{"id": m.mid, "threadId": m.thread} for m in hits[:limit]]
            body: Dict[str, Any] = {"messages": page} if page else {}
            if len(hits) > limit:
                body["nextPageToken"] = "next"
            return httpx.Response(200, json=body)
        if len(rest) == 2 and rest[0] == "threads":
            msgs = [self._raw(m) for m in self.mails if m.thread == rest[1]]
            if not msgs:
                return httpx.Response(404, json={"error": {"code": 404}})
            # Gmail does not promise order; the backend must sort.
            return httpx.Response(200, json={"id": rest[1], "messages": msgs[::-1]})
        return httpx.Response(404, json={"error": {"code": 404}})

    def backend(self) -> GmailReadBackend:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return GmailReadBackend(lambda: "tok", http_client=client)


class FakeGraphApi:
    def __init__(self, mails: List[Mail], *, user: str = USER, page: int = 100):
        self.mails = list(mails)
        self.user = user
        self.page = page
        self.requests: List[httpx.Request] = []

    def _raw(self, m: Mail) -> Dict[str, Any]:
        def addr(rendered: str) -> Dict[str, Any]:
            name, _, rest = rendered.partition("<")
            if rest:
                return {
                    "emailAddress": {
                        "name": name.strip(),
                        "address": rest.rstrip(">").strip(),
                    }
                }
            return {"emailAddress": {"address": rendered.strip()}}

        return {
            "id": m.mid,
            "conversationId": m.thread,
            "subject": m.subject,
            "from": addr(m.sender),
            "toRecipients": [addr(t) for t in m.to.split(",") if t.strip()],
            "ccRecipients": [addr(t) for t in m.cc.split(",") if t.strip()],
            "receivedDateTime": _iso(m.ms),
            "isRead": True,
            "isDraft": m.draft,
            "flag": {"flagStatus": "notFlagged"},
            "categories": [],
            "bodyPreview": m.body[:80],
            "internetMessageHeaders": (
                (
                    [{"name": "Auto-Submitted", "value": "auto-replied"}]
                    if "AUTO_REPLY" in m.labels
                    else []
                )
                + (
                    [{"name": "X-Autoreply", "value": "yes"}]
                    if "X_AUTOREPLY" in m.labels
                    else []
                )
            ),
            "body": {"contentType": "html" if m.html else "text", "content": m.body},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = request.url.params
        if path == "/v1.0/me":
            return httpx.Response(200, json={"mail": self.user})
        folders = {
            "/v1.0/me/mailFolders/inbox/messages": "INBOX",
            "/v1.0/me/mailFolders/sentitems/messages": "SENT",
        }
        if path in folders:
            top = int(params["$top"])
            hits = [m for m in self.mails if folders[path] in m.labels]
            hits.sort(key=lambda m: m.ms, reverse=True)
            body: Dict[str, Any] = {
                "value": [{"id": m.mid, "conversationId": m.thread} for m in hits[:top]]
            }
            if len(hits) > top:
                body["@odata.nextLink"] = "https://graph.microsoft.com/next"
            return httpx.Response(200, json=body)
        if path == "/v1.0/me/messages" and "$filter" in params:
            if "$orderby" in params:
                # What real Graph says to this combination.
                return httpx.Response(
                    400, json={"error": {"code": "InefficientFilter"}}
                )
            prefix, quote = "conversationId eq '", "'"
            flt = params["$filter"]
            if not (flt.startswith(prefix) and flt.endswith(quote)):
                return httpx.Response(400, json={"error": {"code": "BadRequest"}})
            conv = flt[len(prefix) : -len(quote)]
            hits = [m for m in self.mails if m.thread == conv]
            top = int(params["$top"])
            body = {"value": [self._raw(m) for m in hits[:top]][::-1]}
            if len(hits) > top:
                body["@odata.nextLink"] = "https://graph.microsoft.com/next"
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})

    def backend(self) -> OutlookReadBackend:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OutlookReadBackend(lambda: "tok", http_client=client)


__all__ = [
    "DAY_MS",
    "FakeGmailApi",
    "FakeGraphApi",
    "Mail",
    "NOW_MS",
    "USER",
    "outbound",
]
