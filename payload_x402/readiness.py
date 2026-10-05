"""Production-readiness checklist for x402 endpoints.

The LangChain equivalent of the x402-manifest-check GitHub Action:
run the same checks an agent would want before trusting a paid endpoint.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .client import X402Client, parse_challenge


def check_readiness(url: str, client: Optional[X402Client] = None) -> dict[str, Any]:
    """Run the readiness checklist against an x402 endpoint.

    Returns a verdict of PASS / DEGRADED / FAIL plus per-check results.
    Read-only: makes at most two GET requests (endpoint + manifest).
    """
    own = client is None
    c = client or X402Client()
    try:
        return _run(url, c)
    finally:
        if own:
            c.close()


def _run(url: str, client: X402Client) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    # 1. reachable
    try:
        response = client._client.get(url)
        checks["reachable"] = {"pass": True, "detail": f"HTTP {response.status_code}"}
    except httpx.RequestError as exc:
        checks["reachable"] = {"pass": False, "detail": str(exc)[:200]}
        return _verdict(url, checks)

    # 2. returns 402
    is_402 = response.status_code == 402
    checks["returns_402"] = {
        "pass": is_402,
        "detail": f"status {response.status_code}" + ("" if is_402 else " (expected 402 for a paid endpoint)"),
    }
    if not is_402:
        return _verdict(url, checks)

    # 3. version detected
    challenge = parse_challenge(response)
    assert challenge is not None
    version_ok = challenge.version in ("v1", "v2")
    checks["version_detected"] = {
        "pass": version_ok,
        "detail": f"x402 {challenge.version}",
    }

    # 4. challenge parseable with payment fields
    fields_ok = bool(challenge.pay_to and challenge.asset and challenge.network and challenge.amount)
    checks["challenge_parseable"] = {
        "pass": challenge.parseable and fields_ok,
        "detail": (
            f"payTo={bool(challenge.pay_to)} asset={challenge.asset} "
            f"network={challenge.network} amount={challenge.amount}"
            if challenge.parseable
            else f"unparseable: {challenge.parse_error}"
        ),
    }

    # 5. manifest present (/.well-known/x402 on the same origin)
    manifest = _check_manifest(url, client)
    checks["manifest_present"] = manifest

    return _verdict(url, checks)


def _check_manifest(url: str, client: X402Client) -> dict[str, Any]:
    try:
        base = url.split("/", 3)
        origin = f"{base[0]}//{base[2]}" if len(base) > 2 else url
        resp = client._client.get(origin + "/.well-known/x402")
        if resp.status_code != 200:
            return {"pass": False, "detail": f"manifest HTTP {resp.status_code}"}
        try:
            data = resp.json()
        except Exception:
            return {"pass": False, "detail": "manifest is not JSON"}
        has_routes = bool(data.get("routes") or data.get("endpoints"))
        return {
            "pass": True,
            "detail": f"manifest OK ({'lists routes' if has_routes else 'no routes listed'})",
            "lists_routes": has_routes,
        }
    except httpx.RequestError as exc:
        return {"pass": False, "detail": str(exc)[:200]}


def _verdict(url: str, checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    failed = [k for k, v in checks.items() if not v["pass"]]
    hard_checks = {"reachable", "returns_402", "version_detected", "challenge_parseable"}
    hard_failed = [k for k in failed if k in hard_checks]
    if hard_failed:
        verdict = "FAIL"
    elif failed:
        verdict = "DEGRADED"  # reachable paid endpoint, minor gaps (e.g. no manifest)
    else:
        verdict = "PASS"
    return {
        "url": url,
        "verdict": verdict,
        "checks": {k: {"pass": v["pass"], "detail": v["detail"]} for k, v in checks.items()},
        "failed_checks": failed,
    }
