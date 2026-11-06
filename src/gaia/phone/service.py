# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PhoneService: the phone book, alert rules, limits, escalation and the one send path.

Every call and text goes through :meth:`PhoneService._send`. It reads the
number from the phone book by contact id, so nothing upstream (a rule, a
route, an agent) can hand it a number. Verification codes are the one other
way out, through :meth:`_send_code`, which reads the number from the pending
verification it was started for.

See docs/plans/phone-calls.mdx.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import hashlib
import hmac
import secrets as _secrets
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol

from gaia.logger import get_logger
from gaia.phone import templates
from gaia.phone.errors import PhoneError
from gaia.phone.numbers import masked, normalize
from gaia.phone.provider import (
    CallStatus,
    Provider,
    SimulatedProvider,
    TextStatus,
    TwilioProvider,
)
from gaia.phone.settings import apply_patch, with_defaults
from gaia.phone.store import PhoneStore

log = get_logger(__name__)

CODE_TTL = 600
CODE_ATTEMPTS = 3
CODE_STARTS_PER_HOUR = 3
POLL_SECONDS = 3
REDIAL_SECONDS = 120
TICK_SECONDS = 1.0
#: A placed call that never reports an end is given up on after this long.
CALL_GIVE_UP_SECONDS = 600
#: How long GAIA's model server must keep failing before it alerts.
HEALTH_ALERT_SECONDS = 300
SOURCES = ("schedule", "approval", "health", "agent", "test")
_SOURCE_RULE = {
    "schedule": "schedule_failed",
    "approval": "approval_waiting",
    "health": "gaia_down",
    "agent": "agent",
}
_SECRET_SID = "twilio_account_sid"
_SECRET_TOKEN = "twilio_auth_token"


class SecretStore(Protocol):
    def get(self, name: str) -> Optional[str]: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class KeyringSecrets:
    """Provider keys in the OS credential store."""

    SERVICE = "gaia-phone"

    def get(self, name: str) -> Optional[str]:
        from gaia.connectors._keyring import keyring

        return keyring.get_password(self.SERVICE, name)

    def set(self, name: str, value: str) -> None:
        from gaia.connectors._keyring import keyring

        keyring.set_password(self.SERVICE, name, value)

    def delete(self, name: str) -> None:
        from gaia.connectors._keyring import keyring

        try:
            keyring.delete_password(self.SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            log.debug("no %s stored to delete", name)


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def _new_code() -> str:
    return f"{_secrets.randbelow(10**6):06d}"


class PhoneService:
    def __init__(
        self,
        store: PhoneStore,
        secrets: SecretStore,
        *,
        clock: Callable[[], float] = time.time,
        provider_factory: Optional[Callable[[Dict[str, Any]], Provider]] = None,
        awake=None,
    ):
        self.store = store
        self.secrets = secrets
        self.clock = clock
        self._provider_factory = provider_factory or self._default_provider
        self._provider: Optional[Provider] = None
        self._provider_key: Optional[tuple] = None
        self._simulated = SimulatedProvider()
        self._polled_at: Dict[int, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self.last_error: Optional[Dict[str, Any]] = None
        self._unhealthy_since: Optional[float] = None
        self._health_reason = ""
        self._health_alerted = False
        self._awake = awake
        self._raise_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._awake_checked = -1e18

    # ---------------------------------------------------------------- settings

    def settings(self) -> Dict[str, Any]:
        return with_defaults(self.store.get_settings())

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        current = self.settings()
        updated = apply_patch(current, patch)
        if updated["enabled"] and not current["enabled"]:
            self._require_ready(updated)
        if updated["provider"] != current["provider"]:
            # Switching provider needs a fresh keys step before calls resume.
            updated["enabled"] = False
        self.store.put_settings(updated)
        self._provider = None
        # Apply keep-awake on the next tick rather than up to a minute later.
        self._awake_checked = -1e18
        self._wake_up()
        return updated

    def set_credentials(
        self, account_sid: str, auth_token: str, from_number: str
    ) -> Dict[str, Any]:
        """Check the keys and number against Twilio, then store them."""
        number = normalize(from_number)
        provider = TwilioProvider(account_sid.strip(), auth_token.strip())
        provider.check(number)
        self.secrets.set(_SECRET_SID, account_sid.strip())
        self.secrets.set(_SECRET_TOKEN, auth_token.strip())
        # A simulated code reached no phone, so those numbers were never proven.
        dropped = self.store.remove_contacts_verified_by("simulated")
        settings = self.settings()
        settings.update(provider="twilio", from_number=number)
        if dropped:
            settings["enabled"] = False
        self.store.put_settings(settings)
        self._provider = None
        return self.status()

    def use_simulated(self) -> Dict[str, Any]:
        settings = self.settings()
        settings.update(provider="simulated", from_number="+15550100000")
        self.store.put_settings(settings)
        self._provider = None
        return self.status()

    def forget_credentials(self) -> None:
        self.secrets.delete(_SECRET_SID)
        self.secrets.delete(_SECRET_TOKEN)
        settings = self.settings()
        settings.update(enabled=False, from_number="")
        self.store.put_settings(settings)
        self._provider = None

    def configured(self, settings: Optional[Dict[str, Any]] = None) -> bool:
        settings = settings or self.settings()
        if not settings.get("from_number"):
            return False
        if settings["provider"] == "simulated":
            return True
        return bool(self.secrets.get(_SECRET_SID) and self.secrets.get(_SECRET_TOKEN))

    def _require_ready(self, settings: Dict[str, Any]) -> None:
        if not self.configured(settings):
            raise PhoneError(
                "not_configured",
                "Add your provider keys and number before turning phone on.",
            )
        if not self.store.contacts():
            raise PhoneError(
                "not_configured",
                "Add your number to the phone book before turning phone on.",
            )

    def status(self) -> Dict[str, Any]:
        settings = self.settings()
        return {
            "enabled": settings["enabled"],
            "provider": settings["provider"],
            "configured": self.configured(settings),
            "from_number": settings["from_number"],
            "texts_enabled": settings["texts_enabled"],
            "contacts": [
                {
                    "id": c["id"],
                    "label": c["label"],
                    "number": masked(c["number"]),
                    "primary": i == 0,
                }
                for i, c in enumerate(self.store.contacts())
            ],
            "open_alerts": len(self.store.alerts(open_only=True)),
            "calls_today": self.store.count_deliveries(
                "call", self._day_start(), purpose="alert"
            ),
            "texts_today": self.store.count_deliveries(
                "text", self._day_start(), purpose="alert"
            ),
            "last_error": self.last_error,
            "awake": self._awake.state if self._awake is not None else None,
        }

    # ---------------------------------------------------------------- phone book

    async def start_add(
        self, number: str, label: str = "", via: str = "call"
    ) -> Dict[str, Any]:
        number = normalize(number)
        label = (label or "").strip()[:40]
        if via not in ("call", "text"):
            raise PhoneError("bad_request", "Send the code by call or text.")
        settings = self.settings()
        if not self.configured(settings):
            raise PhoneError(
                "not_configured", "Add your provider keys and number first."
            )
        if self.store.contact_by_number(number):
            raise PhoneError(
                "duplicate_number", "That number is already in the phone book."
            )
        if number == settings["from_number"]:
            raise PhoneError("bad_number", "That's GAIA's own number.")
        self._check_code_rate()
        contacts = self.store.contacts()
        salt = uuid.uuid4().hex
        code = _new_code()
        confirm = _new_code() if contacts else None
        vid = uuid.uuid4().hex
        now = self.clock()
        self.store.add_verification(
            {
                "id": vid,
                "purpose": "add",
                "number": number,
                "label": f"{salt}|{label}",
                "contact_id": contacts[0]["id"] if contacts else None,
                "code_hash": _hash(code, salt),
                "confirm_hash": _hash(confirm, salt) if confirm else None,
                "created_at": now,
                "expires_at": now + CODE_TTL,
            }
        )
        await self._send_code(vid, number, code, via)
        if confirm:
            await self._send_code(vid, contacts[0]["number"], confirm, "call")
        return {
            "verification_id": vid,
            "number": masked(number),
            "needs_confirm_code": bool(confirm),
            "confirm_sent_to": masked(contacts[0]["number"]) if contacts else None,
            "expires_in": CODE_TTL,
        }

    async def confirm_add(
        self, vid: str, code: str, confirm_code: Optional[str] = None
    ) -> Dict[str, Any]:
        row = self._live_verification(vid, "add")
        salt, label = row["label"].split("|", 1)
        ok = hmac.compare_digest(_hash(str(code).strip(), salt), row["code_hash"])
        if row["confirm_hash"]:
            ok = ok and hmac.compare_digest(
                _hash(str(confirm_code or "").strip(), salt), row["confirm_hash"]
            )
        if not ok:
            self._failed_attempt(row)
        if self.store.contact_by_number(row["number"]):
            raise PhoneError(
                "duplicate_number", "That number is already in the phone book."
            )
        self.store.update_verification(vid, state="done")
        existing = self.store.contacts()
        contact_id = self.store.add_contact(
            row["number"], label, verified_by=self.settings()["provider"]
        )
        for other in existing:
            await self._notice(other["id"], templates.ADDED_LINE)
        return {"id": contact_id, "label": label, "number": masked(row["number"])}

    async def start_remove(self, contact_id: int) -> Dict[str, Any]:
        contact = self.store.contact(contact_id)
        if contact is None:
            raise PhoneError("not_found", "That number isn't in the phone book.")
        if self.settings()["enabled"] and len(self.store.contacts()) == 1:
            raise PhoneError(
                "last_number",
                "This is the only number in the phone book. Turn phone off before "
                "removing it.",
            )
        self._check_code_rate()
        salt = uuid.uuid4().hex
        code = _new_code()
        vid = uuid.uuid4().hex
        now = self.clock()
        self.store.add_verification(
            {
                "id": vid,
                "purpose": "remove",
                "number": contact["number"],
                "label": f"{salt}|",
                "contact_id": contact_id,
                "code_hash": _hash(code, salt),
                "created_at": now,
                "expires_at": now + CODE_TTL,
            }
        )
        await self._send_code(vid, contact["number"], code, "call")
        return {"verification_id": vid, "number": masked(contact["number"])}

    async def confirm_remove(self, vid: str, code: str) -> None:
        row = self._live_verification(vid, "remove")
        salt = row["label"].split("|", 1)[0]
        if not hmac.compare_digest(_hash(str(code).strip(), salt), row["code_hash"]):
            self._failed_attempt(row)
        if self.settings()["enabled"] and len(self.store.contacts()) == 1:
            raise PhoneError(
                "last_number", "Turn phone off before removing the last number."
            )
        self.store.update_verification(vid, state="done")
        self.store.remove_contact(row["contact_id"])
        for other in self.store.contacts():
            await self._notice(other["id"], templates.REMOVED_LINE)

    def _live_verification(self, vid: str, purpose: str) -> Dict[str, Any]:
        row = self.store.verification(vid)
        if row is None or row["purpose"] != purpose:
            raise PhoneError(
                "not_found", "That code request wasn't found. Start again."
            )
        if row["state"] != "pending":
            raise PhoneError(
                "verification_failed", "That code request is finished. Start again."
            )
        if self.clock() > row["expires_at"]:
            self.store.update_verification(vid, state="failed")
            raise PhoneError("verification_expired", "The code expired. Start again.")
        return row

    def _failed_attempt(self, row: Dict[str, Any]) -> None:
        attempts = row["attempts"] + 1
        state = "failed" if attempts >= CODE_ATTEMPTS else "pending"
        self.store.update_verification(row["id"], attempts=attempts, state=state)
        left = CODE_ATTEMPTS - attempts
        raise PhoneError(
            "verification_failed",
            "That code isn't right."
            + (f" {left} tries left." if left else " Start again for a new code."),
            tries_left=left,
        )

    def _check_code_rate(self) -> None:
        if self.store.verifications_since(self.clock() - 3600) >= CODE_STARTS_PER_HOUR:
            raise PhoneError(
                "rate_limited", "Too many code requests. Try again in an hour."
            )

    # ---------------------------------------------------------------- alerts

    def raise_alert(
        self,
        source: str,
        kind: Optional[str] = None,
        *,
        reason: str = "",
        rule_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Open an alert, or join the open one for the same rule. Returns None
        when the rule for *source* is off."""
        # Check-then-insert under one lock, so two raises can't both open one.
        with self._raise_lock:
            return self._raise_alert(source, reason=reason, rule_key=rule_key)

    def _raise_alert(
        self, source: str, *, reason: str, rule_key: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        if source not in SOURCES:
            raise PhoneError(
                "bad_request", f"source must be one of {', '.join(SOURCES)}"
            )
        settings = self.settings()
        now = self.clock()
        if source == "test":
            kind, steps, urgent = "test", "notify_text_call", True
        else:
            rule = settings["rules"][_SOURCE_RULE[source]]
            if not rule["on"]:
                return None
            kind, steps, urgent = _SOURCE_RULE[source], rule["steps"], rule["urgent"]
        key = rule_key or kind
        cooldown = settings["cooldown_minutes"] * 60
        if source != "test":
            existing = self.store.open_alert_for(key, now - cooldown)
            if existing is not None:
                self.store.update_alert(
                    existing["id"], count=existing["count"] + 1, last_seen_at=now
                )
                return self.store.alert(existing["id"])
        delays = settings["delays"]
        text_at = call_at = None
        if "text" in steps and settings["texts_enabled"]:
            text_at = now if urgent else now + delays["text_after_minutes"] * 60
        if "call" in steps:
            call_at = (
                now
                if urgent
                else now
                + (delays["text_after_minutes"] if "text" in steps else 0) * 60
                + delays["call_after_minutes"] * 60
            )
        alert_id = self.store.add_alert(
            {
                "source": source,
                "kind": kind,
                "rule_key": key,
                "reason": str(reason or "")[:500],
                "urgent": int(urgent),
                "created_at": now,
                "last_seen_at": now,
                "steps": steps,
                "text_due_at": text_at,
                "call_due_at": call_at,
            }
        )
        self._wake_up()
        return self.store.alert(alert_id)

    def handle_alert(self, alert_id: int) -> Dict[str, Any]:
        alert = self.store.alert(alert_id)
        if alert is None:
            raise PhoneError("not_found", "That alert wasn't found.")
        if alert["state"] == "open":
            self.store.update_alert(alert_id, state="handled", handled_at=self.clock())
        return self.public_alert(self.store.alert(alert_id))

    def clear_rule(self, rule_key: str) -> None:
        """The condition behind a rule went away (an approval was answered)."""
        alert = self.store.open_alert_for(rule_key, 0)
        if alert is not None:
            self.store.update_alert(
                alert["id"], state="handled", handled_at=self.clock()
            )

    def note_health(self, ok: bool, reason: str = "") -> None:
        """GAIA's model server started (``ok``) or failed to. Called from any
        thread; the alert itself is raised by :meth:`tick`."""
        if ok:
            self._unhealthy_since = None
            self._health_alerted = False
            # Always: an alert left open by a run before a restart must stop too.
            self.clear_rule("gaia_down")
            return
        if self._unhealthy_since is None:
            self._unhealthy_since = self.clock()
        self._health_reason = reason[:500]

    def alerts(self, open_only: bool = False) -> List[Dict[str, Any]]:
        return [self.public_alert(a) for a in self.store.alerts(open_only=open_only)]

    def public_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        deliveries = self.store.deliveries(alert_id=alert["id"])
        return {
            "id": alert["id"],
            "source": alert["source"],
            "kind": alert["kind"],
            "title": templates.alert_title(alert["kind"]),
            "reason": alert["reason"],
            "count": alert["count"],
            "state": alert["state"],
            "urgent": bool(alert["urgent"]),
            "created_at": alert["created_at"],
            "reached": bool(alert["reached"]),
            "held": alert["held"],
            "deliveries": [
                {
                    "channel": d["channel"],
                    "number": masked(d["number"]),
                    "state": d["state"],
                    "outcome": d["outcome"],
                    "reached": bool(d["reached"]),
                    "error": d["error"],
                    "at": d["created_at"],
                }
                for d in reversed(deliveries)
            ],
        }

    # ---------------------------------------------------------------- escalation

    async def tick(self) -> None:
        """Advance every open alert and poll every live delivery once."""
        settings = self.settings()
        now = self.clock()
        unhealthy_since = self._unhealthy_since  # set from another thread
        if (
            unhealthy_since is not None
            and not self._health_alerted
            and now - unhealthy_since >= HEALTH_ALERT_SECONDS
        ):
            self._health_alerted = True
            self.raise_alert("health", reason=self._health_reason)
        if self._awake is not None and now - self._awake_checked >= 60:
            self._awake_checked = now
            self._awake.want(bool(settings["enabled"] and settings["keep_awake"]))
        for delivery in self.store.deliveries(states=["placed"]):
            if now - self._polled_at.get(delivery["id"], 0) >= POLL_SECONDS:
                self._polled_at[delivery["id"]] = now
                await self._poll(delivery, settings)
        if not settings["enabled"]:
            return
        for alert in self.store.alerts(open_only=True, limit=200):
            await self._advance(alert, settings, now)

    async def _advance(
        self, alert: Dict[str, Any], settings: Dict[str, Any], now: float
    ) -> None:
        if not alert["urgent"] and self._in_quiet_hours(settings, now):
            if alert["held"] != "quiet_hours":
                self.store.update_alert(alert["id"], held="quiet_hours")
            return
        if alert["held"] == "quiet_hours":
            self.store.update_alert(alert["id"], held="")
        contacts = self.store.contacts()
        if not contacts:
            return
        if (
            alert["text_due_at"] is not None
            and not alert["text_sent"]
            and not alert["reached"]
            and now >= alert["text_due_at"]
        ):
            self.store.update_alert(alert["id"], text_sent=1)
            sent = self.store.count_deliveries(
                "text", self._day_start(), purpose="alert"
            )
            if sent >= settings["limits"]["texts_per_day"]:
                self.store.update_alert(alert["id"], held="text_limit")
            else:
                await self._send(
                    "text",
                    contacts[0]["id"],
                    templates.alert_text(alert["kind"]),
                    purpose="alert",
                    alert_id=alert["id"],
                )
        max_calls = 2 + max(0, len(contacts) - 1)
        if (
            alert["call_due_at"] is not None
            and not alert["reached"]
            and now >= alert["call_due_at"]
            and alert["calls_made"] < max_calls
        ):
            if self.store.deliveries(
                alert_id=alert["id"], states=["placing", "placed"]
            ):
                return
            limit = self._call_limit_reason(alert, settings)
            if limit:
                # Try again once the daily limit resets at local midnight.
                self.store.update_alert(
                    alert["id"], held=limit, call_due_at=self._day_start() + 86400
                )
                return
            if alert["held"] in ("call_limit", "agent_call_limit"):
                self.store.update_alert(alert["id"], held="")
            made = alert["calls_made"]
            target = (
                contacts[0] if made < 2 else contacts[min(made - 1, len(contacts) - 1)]
            )
            self.store.update_alert(alert["id"], calls_made=made + 1, call_due_at=None)
            await self._send(
                "call",
                target["id"],
                templates.call_script(templates.alert_call_sentence(alert["kind"])),
                purpose="alert",
                alert_id=alert["id"],
            )

    def _call_limit_reason(
        self, alert: Dict[str, Any], settings: Dict[str, Any]
    ) -> str:
        day = self._day_start()
        limits = settings["limits"]
        # Codes and notices have their own hourly limit; these count alerts.
        if (
            self.store.count_deliveries("call", day, purpose="alert")
            >= limits["calls_per_day"]
        ):
            return "call_limit"
        if alert["source"] == "agent" and (
            self.store.count_deliveries("call", day, source="agent", purpose="alert")
            >= limits["agent_calls_per_day"]
        ):
            return "agent_call_limit"
        return ""

    async def _poll(self, delivery: Dict[str, Any], settings: Dict[str, Any]) -> None:
        provider = self._provider_for(settings)
        try:
            if delivery["channel"] == "call":
                status: Any = await asyncio.to_thread(
                    provider.call_status, delivery["provider_sid"]
                )
            else:
                status = await asyncio.to_thread(
                    provider.text_status, delivery["provider_sid"]
                )
        except PhoneError as e:
            self._note_error(e)
            return
        now = self.clock()
        if not status.final:
            if now - delivery["created_at"] > CALL_GIVE_UP_SECONDS:
                self.store.update_delivery(
                    delivery["id"],
                    state="failed",
                    outcome=status.status,
                    error="No final status from the provider.",
                    finished_at=now,
                )
                await self._after_call(delivery, reached=False)
            return
        if isinstance(status, CallStatus):
            outcome = (
                status.status
                if not status.answered_by
                else f"{status.status}:{status.answered_by}"
            )
            self._polled_at.pop(delivery["id"], None)
            self.store.update_delivery(
                delivery["id"],
                state="done",
                outcome=outcome,
                reached=int(status.reached),
                finished_at=now,
            )
            await self._after_call(delivery, reached=status.reached)
        else:
            assert isinstance(status, TextStatus)
            self._polled_at.pop(delivery["id"], None)
            self.store.update_delivery(
                delivery["id"],
                state="done" if status.status == "delivered" else "failed",
                outcome=status.status,
                error=status.error or "",
                finished_at=now,
            )

    async def _after_call(self, delivery: Dict[str, Any], *, reached: bool) -> None:
        # A text that ended never schedules a call: the rule may be text-only.
        if delivery.get("channel", "call") != "call":
            return
        if delivery["purpose"] != "alert" or delivery["alert_id"] is None:
            return
        alert = self.store.alert(delivery["alert_id"])
        if alert is None or alert["state"] != "open":
            return
        if reached:
            # A person heard it: no more calls or texts for this alert.
            self.store.update_alert(
                alert["id"], reached=1, text_sent=1, call_due_at=None
            )
            return
        max_calls = 2 + max(0, len(self.store.contacts()) - 1)
        if alert["calls_made"] < max_calls:
            self.store.update_alert(
                alert["id"], call_due_at=self.clock() + REDIAL_SECONDS
            )
        else:
            self.store.update_alert(alert["id"], held="no_answer")

    # ---------------------------------------------------------------- send path

    async def _send(
        self,
        channel: str,
        contact_id: int,
        payload: str,
        *,
        purpose: str,
        alert_id: Optional[int] = None,
    ) -> Optional[int]:
        """The only way an alert or notice leaves: to a phone book contact by id."""
        contact = self.store.contact(contact_id)
        if contact is None:
            raise PhoneError(
                "not_in_phone_book", "That number isn't in the phone book."
            )
        return await self._deliver(
            channel, contact["number"], payload, purpose, alert_id, contact_id
        )

    async def _send_code(self, vid: str, number: str, code: str, via: str) -> None:
        row = self.store.verification(vid)
        if row is None or row["state"] != "pending":
            raise PhoneError("not_found", "That code request wasn't found.")
        allowed = {row["number"]}
        if row["contact_id"] is not None:
            contact = self.store.contact(row["contact_id"])
            if contact:
                allowed.add(contact["number"])
        if number not in allowed:
            raise PhoneError(
                "not_in_phone_book", "A code can only go to the number it checks."
            )
        payload = (
            templates.call_script(templates.code_sentence(code), repeat=1)
            if via == "call"
            else templates.code_text(code)
        )
        await self._deliver(via, number, payload, "code", None, row["contact_id"])

    async def _notice(self, contact_id: int, line: str) -> None:
        try:
            await self._send(
                "call",
                contact_id,
                templates.call_script(templates.notice_sentence(line)),
                purpose="notice",
            )
        except PhoneError as e:
            # The change itself stands; the apps show the failed notice.
            self._note_error(e)

    async def _deliver(
        self,
        channel: str,
        number: str,
        payload: str,
        purpose: str,
        alert_id: Optional[int],
        contact_id: Optional[int],
    ) -> int:
        settings = self.settings()
        provider = self._provider_for(settings)
        delivery_id = self.store.add_delivery(
            {
                "alert_id": alert_id,
                "purpose": purpose,
                "channel": channel,
                "contact_id": contact_id,
                "number": number,
                "state": "placing",
                "created_at": self.clock(),
            }
        )
        try:
            if channel == "call":
                sid = await asyncio.to_thread(
                    provider.place_call, number, settings["from_number"], payload
                )
            else:
                sid = await asyncio.to_thread(
                    provider.send_text, number, settings["from_number"], payload
                )
        except PhoneError as e:
            self.store.update_delivery(
                delivery_id, state="failed", error=str(e), finished_at=self.clock()
            )
            self._note_error(e)
            if purpose == "alert" and channel == "call":
                await self._after_call(
                    {"purpose": "alert", "alert_id": alert_id}, reached=False
                )
            if purpose == "code":
                raise
            return delivery_id
        self.store.update_delivery(delivery_id, state="placed", provider_sid=sid)
        self.last_error = None
        return delivery_id

    def _note_error(self, error: PhoneError) -> None:
        log.warning("phone: %s (%s)", error, error.code)
        self.last_error = {**error.to_dict(), "at": self.clock()}

    def _provider_for(self, settings: Dict[str, Any]) -> Provider:
        key = (settings["provider"], settings.get("simulated_outcome"))
        if self._provider is None or self._provider_key != key:
            self._provider = self._provider_factory(settings)
            self._provider_key = key
        return self._provider

    def _default_provider(self, settings: Dict[str, Any]) -> Provider:
        if settings["provider"] == "simulated":
            self._simulated.outcome = settings.get("simulated_outcome", "human")
            return self._simulated
        sid, token = self.secrets.get(_SECRET_SID), self.secrets.get(_SECRET_TOKEN)
        if not sid or not token:
            raise PhoneError(
                "not_configured", "Add your Twilio keys in phone settings."
            )
        return TwilioProvider(sid, token)

    @property
    def simulated(self) -> SimulatedProvider:
        return self._simulated

    # ---------------------------------------------------------------- time

    def _local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts).astimezone()

    def _day_start(self) -> float:
        local = self._local(self.clock())
        return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def _in_quiet_hours(self, settings: Dict[str, Any], now: float) -> bool:
        window = settings.get("quiet_hours")
        if not window:
            return False
        local = self._local(now)
        minutes = local.hour * 60 + local.minute
        start = _minutes(window["start"])
        end = _minutes(window["end"])
        return (
            start <= minutes < end if start < end else minutes >= start or minutes < end
        )

    # ---------------------------------------------------------------- lifecycle

    async def resume(self) -> None:
        """After a restart: settle calls that were being placed when it stopped."""
        settings = self.settings()
        for delivery in self.store.deliveries(states=["placing"]):
            sid = None
            if delivery["channel"] == "call":
                try:
                    provider = self._provider_for(settings)
                    # A little before, for clock skew between this PC and the provider.
                    since = delivery["created_at"] - 60
                    sids = await asyncio.to_thread(
                        provider.recent_call_sids, delivery["number"], since
                    )
                    sid = sids[0] if sids else None
                except PhoneError as e:
                    self._note_error(e)
            if sid:
                # It went out before the restart; follow it instead of calling again.
                self.store.update_delivery(
                    delivery["id"], state="placed", provider_sid=sid
                )
            else:
                self.store.update_delivery(
                    delivery["id"],
                    state="failed",
                    error="Interrupted by a restart.",
                    finished_at=self.clock(),
                )
                await self._after_call(delivery, reached=False)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._guarded(self.resume)
        while True:
            await self._guarded(self.tick)
            self._wake.clear()
            # Woken early by a new alert, else once a second.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK_SECONDS)

    async def _guarded(self, step) -> None:
        """Run one step; a failure is reported and the loop goes on, so one bad
        poll never stops alerting for good."""
        try:
            await step()
        except PhoneError as e:
            self._note_error(e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            log.exception("phone: alert loop step failed")
            self.last_error = {
                "code": "provider_error",
                "message": f"Phone alerts hit an error: {type(e).__name__}: {e}"[:300],
                "at": self.clock(),
            }

    def _wake_up(self) -> None:
        """Wake the loop from any thread."""
        loop = getattr(self, "_loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._wake.set)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def stop(self) -> None:
        if self._awake is not None:
            self._awake.want(False)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)
