"""Unit tests for payload-x402. Zero network calls — httpx.MockTransport throughout."""

import base64
import json

import httpx
import pytest

from payload_x402.client import X402Client, parse_challenge, truncate_address
from payload_x402.readiness import check_readiness
from payload_x402.tools import (
    X402InspectTool,
    X402PayableCallTool,
    X402PaymentRequirementsTool,
    X402ReadinessTool,
)

V2_CHALLENGE = {
    "x402Version": 2,
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:8453",
            "amount": "10000",
            "asset": "USDC",
            "payTo": "0xb3ecD1c309bEe30744D28f4B8957aA26D755B36A",
        }
    ],
}
V2_HEADER = base64.urlsafe_b64encode(json.dumps(V2_CHALLENGE).encode()).decode()

V1_BODY = {
    "x402Version": 1,
    "payTo": "0xb3ecD1c309bEe30744D28f4B8957aA26D755B36A",
    "asset": "USDC",
    "network": "base",
    "maxAmountRequired": "0.01",
}

MANIFEST = {"name": "demo", "endpoints": [{"path": "/api/joke", "price": "0.01"}]}


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return X402Client(client=httpx.Client(transport=transport))


def v2_handler(request):
    if request.url.path == "/.well-known/x402":
        return httpx.Response(200, json=MANIFEST)
    return httpx.Response(402, headers={"payment-required": V2_HEADER}, json=V2_CHALLENGE)


def v1_handler(request):
    if request.url.path == "/.well-known/x402":
        return httpx.Response(404)
    return httpx.Response(402, json=V1_BODY)


def free_handler(request):
    return httpx.Response(200, json={"ok": True})


def error_handler(request):
    raise httpx.ConnectError("connection refused")


# ---- client.parse_challenge ----

def test_parse_v2_challenge():
    resp = httpx.Response(402, headers={"payment-required": V2_HEADER}, json=V2_CHALLENGE)
    c = parse_challenge(resp)
    assert c.version == "v2"
    assert c.network == "base"  # CAIP-2 normalized
    assert c.asset == "USDC"
    assert c.amount == "10000"
    assert c.pay_to == "0xb3ecD1c309bEe30744D28f4B8957aA26D755B36A"
    assert c.required_headers == ["payment-signature"]
    assert c.parseable


def test_parse_v1_challenge():
    resp = httpx.Response(402, json=V1_BODY)
    c = parse_challenge(resp)
    assert c.version == "v1"
    assert c.network == "base"
    assert c.asset == "USDC"
    assert c.amount == "0.01"
    assert c.required_headers == ["x-payment"]


def test_parse_non_402_returns_none():
    resp = httpx.Response(200, json={"ok": True})
    assert parse_challenge(resp) is None


def test_parse_malformed_never_raises():
    resp = httpx.Response(402, content=b"not json {{{")
    c = parse_challenge(resp)
    assert c is not None
    assert c.parseable is False


def test_truncate_address():
    assert truncate_address("0xb3ecD1c309bEe30744D28f4B8957aA26D755B36A") == "0xb3ecD1c3...B36A"
    assert truncate_address(None) is None
    assert truncate_address("short") == "short"


# ---- client.inspect / payment_requirements / payable_call ----

def test_inspect_v2():
    client = make_client(v2_handler)
    r = client.inspect("https://api.example.com/joke")
    assert r["reachable"] is True
    assert r["status"] == 402
    assert r["x402_version"] == "v2"
    assert r["challenge_parseable"] is True


def test_inspect_free_endpoint():
    client = make_client(free_handler)
    r = client.inspect("https://api.example.com/free")
    assert r["reachable"] is True
    assert r["status"] == 200
    assert r["x402_version"] == "none"


def test_inspect_unreachable():
    client = make_client(error_handler)
    r = client.inspect("https://down.example.com/")
    assert r["reachable"] is False
    assert "error" in r


def test_payment_requirements_v2():
    client = make_client(v2_handler)
    r = client.payment_requirements("https://api.example.com/joke")
    assert r["is_402"] is True
    assert r["network"] == "base"
    assert r["asset"] == "USDC"
    assert r["amount"] == "10000"
    assert r["pay_to_truncated"] == "0xb3ecD1c3...B36A"
    assert "payment-signature" in r["required_headers"]


def test_payment_requirements_non_402():
    client = make_client(free_handler)
    r = client.payment_requirements("https://api.example.com/free")
    assert r["is_402"] is False


def test_payable_call_attaches_signature():
    seen = {}

    def handler(request):
        seen["payment-signature"] = request.headers.get("payment-signature")
        seen["x-payment"] = request.headers.get("x-payment")
        return httpx.Response(200, json={"joke": "hi"})

    client = make_client(handler)
    r = client.payable_call("https://api.example.com/joke", payment_signature="sig-abc")
    assert r["ok"] is True
    assert r["status"] == 200
    assert seen["payment-signature"] == "sig-abc"
    assert r["response"] == {"joke": "hi"}


def test_payable_call_requires_signature():
    client = make_client(v2_handler)
    r = client.payable_call("https://api.example.com/joke", payment_signature="")
    assert r["ok"] is False
    assert "required" in r["error"]


def test_payable_call_rejects_bad_method():
    client = make_client(v2_handler)
    r = client.payable_call("https://api.example.com/joke", method="TRACE", payment_signature="s")
    assert r["ok"] is False


# ---- readiness ----

def test_readiness_pass():
    client = make_client(v2_handler)
    r = check_readiness("https://api.example.com/joke", client=client)
    assert r["verdict"] == "PASS"
    assert r["failed_checks"] == []


def test_readiness_degraded_no_manifest():
    client = make_client(v1_handler)
    r = check_readiness("https://api.example.com/joke", client=client)
    assert r["verdict"] == "DEGRADED"
    assert r["failed_checks"] == ["manifest_present"]


def test_readiness_fail_unreachable():
    client = make_client(error_handler)
    r = check_readiness("https://down.example.com/", client=client)
    assert r["verdict"] == "FAIL"


def test_readiness_fail_not_402():
    client = make_client(free_handler)
    r = check_readiness("https://api.example.com/free", client=client)
    assert r["verdict"] == "FAIL"
    assert "returns_402" in r["failed_checks"]


# ---- LangChain tools ----

def _patched_tool(tool_cls, handler, monkeypatch):
    """Return a tool instance whose X402Client uses a MockTransport."""
    import payload_x402.tools as tools_mod
    import payload_x402.readiness as readiness_mod

    transport = httpx.MockTransport(handler)
    orig_tools = tools_mod.X402Client
    orig_readiness = readiness_mod.X402Client

    class Patched(orig_tools):
        def __init__(self, *a, **k):
            super().__init__(*a, client=httpx.Client(transport=transport), **k)
            self._owns_client = False

    monkeypatch.setattr(tools_mod, "X402Client", Patched)
    monkeypatch.setattr(readiness_mod, "X402Client", Patched)
    return tool_cls()


def test_inspect_tool_schema():
    tool = X402InspectTool()
    assert tool.name == "x402_inspect"
    assert "url" in tool.tool_call_schema.model_json_schema()["properties"]
    assert tool.args_schema is not None


def test_inspect_tool_invoke(monkeypatch):
    tool = _patched_tool(X402InspectTool, v2_handler, monkeypatch)
    out = json.loads(tool.invoke({"url": "https://api.example.com/joke"}))
    assert out["x402_version"] == "v2"
    assert out["status"] == 402


def test_payment_requirements_tool_invoke(monkeypatch):
    tool = _patched_tool(X402PaymentRequirementsTool, v2_handler, monkeypatch)
    out = json.loads(tool.invoke({"url": "https://api.example.com/joke"}))
    assert out["asset"] == "USDC"
    assert out["network"] == "base"


def test_readiness_tool_invoke(monkeypatch):
    tool = _patched_tool(X402ReadinessTool, v2_handler, monkeypatch)
    out = json.loads(tool.invoke({"url": "https://api.example.com/joke"}))
    assert out["verdict"] == "PASS"


def test_payable_call_tool_never_echoes_signature(monkeypatch):
    seen = {}

    def handler(request):
        seen["sig"] = request.headers.get("payment-signature")
        return httpx.Response(200, json={"ok": True})

    tool = _patched_tool(X402PayableCallTool, handler, monkeypatch)
    out = json.loads(
        tool.invoke(
            {"url": "https://api.example.com/joke", "method": "GET", "payment_signature": "SECRET-SIG"}
        )
    )
    assert seen["sig"] == "SECRET-SIG"
    assert "SECRET-SIG" not in json.dumps(out)
    assert out["payment_signature_sent"] is True


def test_all_four_tool_names():
    names = {X402InspectTool().name, X402PaymentRequirementsTool().name,
             X402ReadinessTool().name, X402PayableCallTool().name}
    assert names == {"x402_inspect", "x402_payment_requirements", "x402_readiness", "x402_payable_call"}
