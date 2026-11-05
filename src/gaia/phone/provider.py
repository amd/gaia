# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Phone providers: Twilio's REST API, and a simulated one for trying things out.

Only :class:`gaia.phone.service.PhoneService` holds a provider, and only its
send path calls one, after checking the phone book.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Protocol

import requests

from gaia.phone.errors import PhoneError

TWILIO_API = "https://api.twilio.com/2010-04-01"
REQUEST_TIMEOUT = 20
RING_SECONDS = 30

CALL_FINAL = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})
TEXT_FINAL = frozenset({"delivered", "undelivered", "failed"})

#: Twilio error codes worth their own wording.
_TRIAL_UNVERIFIED = {21219, 21608}
_BAD_FROM = {21210, 21212, 21606}


@dataclass
class CallStatus:
    status: str
    answered_by: Optional[str] = None
    duration: int = 0

    @property
    def final(self) -> bool:
        return self.status in CALL_FINAL

    @property
    def reached(self) -> bool:
        return self.status == "completed" and self.answered_by == "human"


@dataclass
class TextStatus:
    status: str
    error: Optional[str] = None

    @property
    def final(self) -> bool:
        return self.status in TEXT_FINAL


class Provider(Protocol):
    name: str

    def check(self, from_number: str) -> None: ...

    def place_call(self, to: str, from_number: str, script: str) -> str: ...

    def call_status(self, sid: str) -> CallStatus: ...

    def recent_call_sids(self, to: str, since: float) -> List[str]:
        """Calls to *to* created at or after *since* (epoch), newest first."""

    def send_text(self, to: str, from_number: str, body: str) -> str: ...

    def text_status(self, sid: str) -> TextStatus: ...


class TwilioProvider:
    """Twilio over its REST API with an Account SID and Auth Token (or API key)."""

    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        *,
        api_key_sid: Optional[str] = None,
        session: Optional[requests.Session] = None,
        base_url: str = TWILIO_API,
    ):
        if not account_sid.startswith("AC") or len(account_sid) != 34:
            raise PhoneError(
                "bad_credentials",
                "A Twilio Account SID starts with AC and is 34 characters long.",
            )
        if not auth_token:
            raise PhoneError("bad_credentials", "Enter the Twilio Auth Token.")
        self.account_sid = account_sid
        self._auth = (api_key_sid or account_sid, auth_token)
        self._session = session or requests.Session()
        self._base = f"{base_url}/Accounts/{account_sid}"

    # ---- calls ----------------------------------------------------------

    def check(self, from_number: str) -> None:
        """Verify the credentials and that *from_number* belongs to the account."""
        account = self._request("GET", ".json")
        if account.get("status") not in (None, "active"):
            raise PhoneError(
                "provider_error",
                f"This Twilio account is {account.get('status')}. Reactivate it in "
                "the Twilio console.",
            )
        numbers = self._request(
            "GET", "/IncomingPhoneNumbers.json", params={"PhoneNumber": from_number}
        )
        if not numbers.get("incoming_phone_numbers"):
            raise PhoneError(
                "provider_error",
                f"{from_number} isn't a number on this Twilio account. Use the number "
                "Twilio gave you, with its country code.",
            )

    def place_call(self, to: str, from_number: str, script: str) -> str:
        data = {
            "To": to,
            "From": from_number,
            "Twiml": script,
            "MachineDetection": "Enable",
            "Timeout": str(RING_SECONDS),
        }
        return str(self._request("POST", "/Calls.json", data=data)["sid"])

    def call_status(self, sid: str) -> CallStatus:
        body = self._request("GET", f"/Calls/{sid}.json")
        duration = body.get("duration")
        return CallStatus(
            status=str(body.get("status") or "unknown"),
            answered_by=body.get("answered_by"),
            duration=int(duration) if str(duration or "").isdigit() else 0,
        )

    def recent_call_sids(self, to: str, since: float) -> List[str]:
        day = datetime.fromtimestamp(since, timezone.utc).date().isoformat()
        body = self._request("GET", "/Calls.json", params={"To": to, "StartTime>": day})
        out = []
        for call in body.get("calls") or []:
            try:
                created = parsedate_to_datetime(
                    str(call.get("date_created"))
                ).timestamp()
            except (TypeError, ValueError):
                continue
            if call.get("sid") and created >= since:
                out.append((created, str(call["sid"])))
        return [sid for _, sid in sorted(out, reverse=True)]

    # ---- texts ----------------------------------------------------------

    def send_text(self, to: str, from_number: str, body: str) -> str:
        data = {"To": to, "From": from_number, "Body": body}
        return str(self._request("POST", "/Messages.json", data=data)["sid"])

    def text_status(self, sid: str) -> TextStatus:
        body = self._request("GET", f"/Messages/{sid}.json")
        error = body.get("error_message") or (
            f"Twilio error {body['error_code']}" if body.get("error_code") else None
        )
        return TextStatus(status=str(body.get("status") or "unknown"), error=error)

    # ---- plumbing -------------------------------------------------------

    def _request(self, method: str, path: str, **kw) -> Dict:
        url = self._base + path
        try:
            response = self._session.request(
                method, url, auth=self._auth, timeout=REQUEST_TIMEOUT, **kw
            )
        except requests.RequestException as e:
            raise PhoneError(
                "provider_unreachable",
                "Couldn't reach Twilio. Check this PC's internet connection.",
                detail=str(e)[:300],
            ) from e
        if response.status_code == 401:
            raise PhoneError(
                "bad_credentials", "Twilio rejected the Account SID or Auth Token."
            )
        try:
            body = response.json()
        except ValueError as e:
            raise PhoneError(
                "provider_error",
                f"Twilio returned {response.status_code} with a body that isn't JSON.",
            ) from e
        if response.status_code >= 400:
            raise _twilio_error(response.status_code, body)
        return body


def _twilio_error(status: int, body: Dict) -> PhoneError:
    code = body.get("code")
    message = str(body.get("message") or f"HTTP {status}")[:300]
    if code in _TRIAL_UNVERIFIED:
        return PhoneError(
            "trial_unverified",
            "This Twilio trial account can only reach numbers verified in the Twilio "
            "console. Verify this number there too, or upgrade the account.",
            detail=message,
        )
    if code in _BAD_FROM:
        return PhoneError(
            "provider_error",
            "Twilio won't use that number to call from. Pick a number on your "
            "Twilio account that can make calls.",
            detail=message,
        )
    return PhoneError(
        "provider_error", f"Twilio refused the request: {message}", detail=str(code)
    )


@dataclass
class SimulatedProvider:
    """Records calls and texts instead of sending them, so every screen, rule
    and limit can be tried before signing up anywhere."""

    name: str = "simulated"
    #: What every simulated call ends as: human, machine_start, no-answer, busy.
    outcome: str = "human"
    calls: List[Dict] = field(default_factory=list)
    texts: List[Dict] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _polls: Dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, from_number: str) -> None:
        return None

    def place_call(self, to: str, from_number: str, script: str) -> str:
        with self._lock:
            sid = f"SIMCA{next(self._ids):06d}"
            self.calls.append(
                {"sid": sid, "to": to, "from": from_number, "script": script}
            )
        return sid

    def call_status(self, sid: str) -> CallStatus:
        with self._lock:
            polls = self._polls[sid] = self._polls.get(sid, 0) + 1
        if polls < 2:
            return CallStatus("ringing")
        if self.outcome in ("no-answer", "busy"):
            return CallStatus(self.outcome)
        return CallStatus("completed", answered_by=self.outcome, duration=9)

    def recent_call_sids(self, to: str, since: float) -> List[str]:
        return [c["sid"] for c in reversed(self.calls) if c["to"] == to]

    def send_text(self, to: str, from_number: str, body: str) -> str:
        with self._lock:
            sid = f"SIMSM{next(self._ids):06d}"
            self.texts.append({"sid": sid, "to": to, "from": from_number, "body": body})
        return sid

    def text_status(self, sid: str) -> TextStatus:
        return TextStatus("delivered")
