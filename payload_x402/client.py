"""payload-x402: x402 HTTP client for inspecting and calling paid endpoints.

Zero key handling. This client never signs payments and never holds
private keys — it only reads public challenge metadata and, when the
operator supplies a payment signature out-of-band, attaches it to a call.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

# Known x402 protocol markers
_V2_REQUIRED_HEADER = "payment-required"  # base64-encoded JSON challenge (v2)
_V1_PAYMENT_HEADER = "x-payment"           # v1 payment header name
_V2_SIGNATURE_HEADER = "payment-signature"  # v2 payment submission header


@dataclass
class Challenge:
    """Parsed x402 payment challenge from a 402 response."""
    version: str  # "v1" | "v2" | "unknown"
    pay_to: Optional[str] = None
    asset: Optional[str] = None
    network: Optional[str] = None
    amount: Optional[str] = None  # atomic units as string (v2) or decimal string (v1)
    amount_display: Optional[str] = None  # human-friendly, e.g. "0.01 USDC"
    required_headers: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    parseable: bool = True
    parse_error: Optional[str] = None


def _b64json(value: str) -> Optional[dict]:
    """Decode a base64 (standard or urlsafe, padded or not) JSON string."""
    try:
        padded = value + "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception:
        return None


def _detect_version(headers: httpx.Headers, body: Any) -> str:
    header_names = {k.lower() for k in headers.keys()}
    if _V2_REQUIRED_HEADER in header_names:
        return "v2"
    if isinstance(body, dict):
        v = body.get("x402Version")
        if v == 2 or v == "2":
            return "v2"
        if v == 1 or v == "1":
            return "v1"
        # v2 bodies carry `accepts`; v1 bodies carry `maxAmountRequired`-style fields
        if "accepts" in body:
            return "v2"
    if _V1_PAYMENT_HEADER in header_names:
        return "v1"
    return "unknown"


def _normalize_network(network: Any) -> Optional[str]:
    if not network:
        return None
    n = str(network).strip()
    # CAIP-2 eip155:8453 -> base ; keep anything else as-is
    return {"eip155:8453": "base", "eip155:84532": "base-sepolia"}.get(n, n)


def _first_accept(accepts: Any) -> dict:
    if isinstance(accepts, list) and accepts and isinstance(accepts[0], dict):
        return accepts[0]
    return {}


def parse_challenge(response: httpx.Response) -> Optional[Challenge]:
    """Parse an x402 challenge out of an HTTP 402 response.

    Returns None when the response is not a 402. Never raises on malformed
    input — instead returns a Challenge with parseable=False.
    """
    if response.status_code != 402:
        return None

    headers = response.headers
    try:
        body: Any = response.json()
    except Exception:
        body = None

    version = _detect_version(headers, body)
    challenge = Challenge(version=version, raw=body if isinstance(body, dict) else {})

    try:
        if version == "v2":
            payload = _b64json(headers.get(_V2_REQUIRED_HEADER, "")) or {}
            if not payload and isinstance(body, dict):
                payload = body
            accept = _first_accept(payload.get("accepts"))
            challenge.pay_to = accept.get("payTo") or payload.get("payTo")
            challenge.asset = accept.get("asset")
            challenge.network = _normalize_network(accept.get("network"))
            challenge.amount = str(accept.get("amount") or accept.get("maxAmountRequired") or "")
            challenge.required_headers = [_V2_SIGNATURE_HEADER]
        elif version == "v1":
            data = body if isinstance(body, dict) else {}
            challenge.pay_to = data.get("payTo")
            challenge.asset = data.get("asset")
            challenge.network = _normalize_network(data.get("network"))
            challenge.amount = str(data.get("maxAmountRequired") or data.get("amount") or "")
            challenge.required_headers = [_V1_PAYMENT_HEADER]
        else:
            # Unknown version: still surface whatever payment-ish fields exist
            data = body if isinstance(body, dict) else {}
            challenge.pay_to = data.get("payTo")
            challenge.asset = data.get("asset")
            challenge.network = _normalize_network(data.get("network"))
            challenge.parseable = False
            challenge.parse_error = "no recognized x402 version markers"
    except Exception as exc:  # never blow up on weird challenge shapes
        challenge.parseable = False
        challenge.parse_error = str(exc)

    if challenge.amount and challenge.asset:
        challenge.amount_display = f"{challenge.amount} {challenge.asset}"
    return challenge


def truncate_address(address: Optional[str], keep: int = 10) -> Optional[str]:
    if not address:
        return None
    a = str(address)
    return a if len(a) <= keep + 3 else f"{a[:keep]}...{a[-4:]}"


class X402Client:
    """Thin read-only x402 HTTP client. No signing, no keys, no payments."""

    def __init__(self, timeout: float = 15.0, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "payload-x402/0.1.0"}
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "X402Client":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def inspect(self, url: str) -> dict[str, Any]:
        """GET url and report reachability, status, and x402 signals."""
        try:
            response = self._client.get(url)
        except httpx.RequestError as exc:
            return {"reachable": False, "url": url, "error": str(exc)[:200]}

        try:
            body: Any = response.json()
        except Exception:
            body = None
        version = _detect_version(response.headers, body) if response.status_code == 402 else "none"
        return {
            "reachable": True,
            "url": url,
            "status": response.status_code,
            "is_402": response.status_code == 402,
            "x402_version": version,
            "challenge_parseable": parse_challenge(response).parseable
            if response.status_code == 402
            else None,
        }

    def payment_requirements(self, url: str) -> dict[str, Any]:
        """GET url (expecting 402) and return structured payment requirements."""
        try:
            response = self._client.get(url)
        except httpx.RequestError as exc:
            return {"reachable": False, "url": url, "error": str(exc)[:200]}
        if response.status_code != 402:
            return {
                "reachable": True,
                "url": url,
                "status": response.status_code,
                "is_402": False,
                "note": "endpoint did not return 402; no payment requirements to parse",
            }
        challenge = parse_challenge(response)
        assert challenge is not None
        return {
            "reachable": True,
            "url": url,
            "status": 402,
            "is_402": True,
            "x402_version": challenge.version,
            "network": challenge.network,
            "asset": challenge.asset,
            "amount": challenge.amount,
            "amount_display": challenge.amount_display,
            "pay_to": challenge.pay_to,
            "pay_to_truncated": truncate_address(challenge.pay_to),
            "required_headers": challenge.required_headers,
            "parseable": challenge.parseable,
            "parse_error": challenge.parse_error,
        }

    def payable_call(
        self,
        url: str,
        method: str = "GET",
        payment_signature: str = "",
        body: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Make one HTTP call carrying an operator-supplied payment signature.

        The signature is supplied out-of-band by the agent operator.
        This client never creates signatures and never holds keys.
        """
        if not payment_signature:
            return {"ok": False, "error": "payment_signature is required"}
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            return {"ok": False, "error": f"unsupported method: {method}"}
        headers = {_V2_SIGNATURE_HEADER: payment_signature, _V1_PAYMENT_HEADER: payment_signature}
        try:
            response = self._client.request(method, url, headers=headers, json=body)
        except httpx.RequestError as exc:
            return {"ok": False, "url": url, "error": str(exc)[:200]}
        try:
            data: Any = response.json()
        except Exception:
            data = response.text[:2000]
        return {
            "ok": response.status_code < 400,
            "url": url,
            "status": response.status_code,
            "payment_signature_sent": True,
            "response": data,
        }
