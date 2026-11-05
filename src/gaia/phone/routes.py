# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's phone routes.

``/daemon/v1/phone/*`` takes the client token, like every front-end route.
``/host/v1/alerts`` takes a sidecar's own custody secret, so an agent can raise
an alert, scoped to itself, and do nothing else here.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from gaia.phone.errors import PhoneError
from gaia.phone.service import PhoneService

API_PREFIX = "/daemon/v1/phone"
HOST_ALERTS = "/host/v1/alerts"

_STATUS = {
    "bad_request": 422,
    "bad_number": 422,
    "bad_credentials": 422,  # never 401: that reads as the client token going stale
    "not_found": 404,
    "not_in_phone_book": 403,
    "not_configured": 409,
    "phone_off": 409,
    "last_number": 409,
    "duplicate_number": 409,
    "verification_failed": 422,
    "verification_expired": 410,
    "rate_limited": 429,
    "limit_reached": 429,
    "trial_unverified": 502,
    "provider_error": 502,
    "provider_unreachable": 502,
}

#: Sources a front end may raise. ``agent`` comes only through /host/v1/alerts.
CLIENT_SOURCES = ("schedule", "approval", "health", "test")


class Keys(BaseModel):
    account_sid: str = Field(min_length=1, max_length=64)
    auth_token: str = Field(min_length=1, max_length=128)
    from_number: str = Field(min_length=1, max_length=32)


class AddNumber(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    label: str = Field(default="", max_length=40)
    via: str = "call"


class ConfirmAdd(BaseModel):
    verification_id: str = Field(min_length=32, max_length=32)
    code: str = Field(min_length=6, max_length=6)
    confirm_code: Optional[str] = Field(default=None, min_length=6, max_length=6)


class ConfirmRemove(BaseModel):
    verification_id: str = Field(min_length=32, max_length=32)
    code: str = Field(min_length=6, max_length=6)


class RaiseAlert(BaseModel):
    source: str
    reason: str = Field(default="", max_length=500)
    rule_key: Optional[str] = Field(default=None, max_length=120)


class ClearRule(BaseModel):
    rule_key: str = Field(min_length=1, max_length=120)


class AgentAlert(BaseModel):
    reason: str = Field(default="", max_length=500)


def _fail(error: PhoneError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS.get(error.code, 400), detail=error.to_dict()
    )


def build_phone_router(
    token_dependency, service: PhoneService, custody_auth=None
) -> APIRouter:
    router = APIRouter()
    auth = [Depends(token_dependency)]

    @router.get(f"{API_PREFIX}/status", dependencies=auth)
    def status() -> dict:
        return service.status()

    @router.get(f"{API_PREFIX}/settings", dependencies=auth)
    def get_settings() -> dict:
        return service.settings()

    @router.put(f"{API_PREFIX}/settings", dependencies=auth)
    def put_settings(patch: dict = Body(...)) -> dict:
        try:
            return service.update_settings(patch)
        except PhoneError as e:
            raise _fail(e) from e

    @router.post(f"{API_PREFIX}/keys", dependencies=auth)
    def set_keys(body: Keys) -> dict:
        try:
            return service.set_credentials(
                body.account_sid, body.auth_token, body.from_number
            )
        except PhoneError as e:
            raise _fail(e) from e

    @router.delete(f"{API_PREFIX}/keys", dependencies=auth)
    def forget_keys() -> dict:
        service.forget_credentials()
        return service.status()

    @router.post(f"{API_PREFIX}/simulated", dependencies=auth)
    def use_simulated() -> dict:
        return service.use_simulated()

    @router.get(f"{API_PREFIX}/simulated/log", dependencies=auth)
    def simulated_log() -> dict:
        sim = service.simulated
        return {"calls": sim.calls[-50:], "texts": sim.texts[-50:]}

    @router.post(f"{API_PREFIX}/contacts", dependencies=auth)
    async def add_number(body: AddNumber) -> dict:
        try:
            return await service.start_add(body.number, body.label, body.via)
        except PhoneError as e:
            raise _fail(e) from e

    @router.post(f"{API_PREFIX}/contacts/confirm", dependencies=auth)
    async def confirm_number(body: ConfirmAdd) -> dict:
        try:
            return await service.confirm_add(
                body.verification_id, body.code, body.confirm_code
            )
        except PhoneError as e:
            raise _fail(e) from e

    @router.post(f"{API_PREFIX}/contacts/{{contact_id}}/remove", dependencies=auth)
    async def remove_number(contact_id: int) -> dict:
        try:
            return await service.start_remove(contact_id)
        except PhoneError as e:
            raise _fail(e) from e

    @router.post(f"{API_PREFIX}/contacts/remove/confirm", dependencies=auth)
    async def confirm_remove(body: ConfirmRemove) -> dict:
        try:
            await service.confirm_remove(body.verification_id, body.code)
        except PhoneError as e:
            raise _fail(e) from e
        return service.status()

    @router.get(f"{API_PREFIX}/alerts", dependencies=auth)
    def list_alerts(open: bool = False) -> dict:
        return {"alerts": service.alerts(open_only=open)}

    @router.post(f"{API_PREFIX}/alerts", dependencies=auth)
    def raise_alert(body: RaiseAlert) -> dict:
        if body.source not in CLIENT_SOURCES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "bad_request",
                    "message": f"source must be one of {', '.join(CLIENT_SOURCES)}",
                },
            )
        try:
            alert = service.raise_alert(
                body.source, reason=body.reason, rule_key=body.rule_key
            )
        except PhoneError as e:
            raise _fail(e) from e
        return {"alert": service.public_alert(alert) if alert else None}

    @router.post(f"{API_PREFIX}/alerts/{{alert_id}}/handled", dependencies=auth)
    def handled(alert_id: int) -> dict:
        try:
            return service.handle_alert(alert_id)
        except PhoneError as e:
            raise _fail(e) from e

    @router.post(f"{API_PREFIX}/alerts/clear", dependencies=auth)
    def clear(body: ClearRule) -> dict:
        service.clear_rule(body.rule_key)
        return {"cleared": body.rule_key}

    @router.post(f"{API_PREFIX}/test", dependencies=auth)
    def test_alert() -> dict:
        settings = service.settings()
        if not settings["enabled"]:
            raise _fail(PhoneError("phone_off", "Turn phone on to send a test alert."))
        alert = service.raise_alert("test")
        return {"alert": service.public_alert(alert)}

    if custody_auth is not None:
        from gaia.daemon.custody.routes import _resolve_agent

        @router.get(HOST_ALERTS)
        def agent_alerts_allowed(
            authorization: Optional[str] = Header(default=None),
        ) -> dict:
            _resolve_agent(custody_auth, authorization)
            settings = service.settings()
            return {
                "allowed": bool(
                    settings["enabled"] and settings["rules"]["agent"]["on"]
                )
            }

        @router.post(HOST_ALERTS)
        def agent_alert(
            body: AgentAlert, authorization: Optional[str] = Header(default=None)
        ) -> dict:
            agent_id = _resolve_agent(custody_auth, authorization)
            alert = service.raise_alert(
                "agent", reason=body.reason, rule_key=f"agent:{agent_id}"
            )
            if alert is None:
                return {
                    "raised": False,
                    "message": "Agents may not alert the user: that setting is off.",
                }
            return {"raised": True, "alert_id": alert["id"]}

    return router
