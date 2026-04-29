"""Authorization flow manager (PKCE + loopback).

This module provides a lightweight manager that constructs the authorization
URL and tracks in-flight flows. The actual loopback HTTP server will be
implemented in a follow-up; here we provide the URL construction and flow
state handling necessary for the UI to start an authorization.
"""
import uuid
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List

from . import pkce, google


@dataclass
class FlowState:
    flow_id: str
    code_verifier: str
    code_challenge: str
    state: str
    scopes: List[str]


_IN_FLIGHT: Dict[str, FlowState] = {}


def start_authorization(scopes: List[str]) -> Dict[str, str]:
    flow_id = str(uuid.uuid4())
    verifier = pkce.generate_code_verifier()
    challenge = pkce.compute_code_challenge(verifier)
    csrf = str(uuid.uuid4())
    fs = FlowState(flow_id=flow_id, code_verifier=verifier, code_challenge=challenge, state=csrf, scopes=scopes)
    _IN_FLIGHT[flow_id] = fs

    params = {
        "response_type": "code",
        "client_id": google.get_client_id(),
        "scope": " ".join(scopes),
        "redirect_uri": "http://127.0.0.1:{{PORT}}/callback",
        "state": csrf,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = google.AUTH_URL + "?" + urllib.parse.urlencode(params)
    return {"flow_id": flow_id, "authorization_url": url}


def get_flow(flow_id: str) -> FlowState | None:
    return _IN_FLIGHT.get(flow_id)


def complete_flow(flow_id: str) -> None:
    # placeholder: real implementation exchanges code using stored verifier
    _IN_FLIGHT.pop(flow_id, None)


__all__ = ["start_authorization", "get_flow", "complete_flow"]
