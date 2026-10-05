"""LangChain tools for x402 paid endpoints.

Each tool is a langchain_core.tools.BaseTool subclass with name,
description, and args_schema, ready to drop into any LangChain agent.
"""

from __future__ import annotations

import json
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from .client import X402Client
from .readiness import check_readiness


class UrlInput(BaseModel):
    url: str = Field(description="Full URL of the x402 endpoint to check, e.g. https://api.example.com/data")


class X402InspectTool(BaseTool):
    """Inspect an endpoint and report reachability and x402 signals. Read-only."""

    name: str = "x402_inspect"
    description: str = (
        "Inspect a URL and report whether it is reachable, its HTTP status, "
        "and whether it exposes x402 payment signals (version v1/v2/none, "
        "challenge parseable). Read-only: makes one GET request, never pays."
    )
    args_schema: Type[BaseModel] = UrlInput

    def _run(self, url: str) -> str:
        with X402Client() as client:
            result = client.inspect(url)
        return json.dumps(result, indent=2)


class X402PaymentRequirementsTool(BaseTool):
    """Parse structured payment requirements from a 402 endpoint. Read-only."""

    name: str = "x402_payment_requirements"
    description: str = (
        "Fetch a 402 payment-required endpoint and return its structured payment "
        "requirements: network, asset, amount, payTo address (truncated), and the "
        "headers a payer must send. Read-only: makes one GET request, never pays."
    )
    args_schema: Type[BaseModel] = UrlInput

    def _run(self, url: str) -> str:
        with X402Client() as client:
            result = client.payment_requirements(url)
        return json.dumps(result, indent=2)


class X402ReadinessTool(BaseTool):
    """Run the production-readiness checklist on an x402 endpoint. Read-only."""

    name: str = "x402_readiness"
    description: str = (
        "Run a production-readiness checklist against an x402 endpoint and return "
        "a PASS/DEGRADED/FAIL verdict with per-check results: reachable, returns "
        "402, x402 version detected, challenge parseable, manifest present. "
        "Read-only: makes at most two GET requests, never pays."
    )
    args_schema: Type[BaseModel] = UrlInput

    def _run(self, url: str) -> str:
        result = check_readiness(url)
        return json.dumps(result, indent=2)


class PayableCallInput(BaseModel):
    url: str = Field(description="Full URL of the payable resource to call")
    method: str = Field(default="GET", description="HTTP method: GET, POST, PUT, PATCH or DELETE")
    payment_signature: str = Field(
        description=(
            "Pre-supplied x402 payment signature, obtained out-of-band by the agent "
            "operator. This tool never creates signatures and never holds keys."
        )
    )
    body: Optional[dict] = Field(default=None, description="Optional JSON body for POST/PUT/PATCH")


class X402PayableCallTool(BaseTool):
    """Call a payable resource with an operator-supplied payment signature.

    SECURITY: the payment signature must be supplied out-of-band by the agent
    operator. This tool never signs, never holds private keys, and never
    initiates a payment on its own — it only attaches a provided signature
    to a single HTTP call.
    """

    name: str = "x402_payable_call"
    description: str = (
        "Make an HTTP call to a payable x402 resource, attaching a payment "
        "signature supplied by the agent operator. The operator obtains and "
        "authorizes the payment out-of-band; this tool never signs, never holds "
        "keys, and never pays on its own. Use only for operator-approved calls."
    )
    args_schema: Type[BaseModel] = PayableCallInput

    def _run(self, url: str, method: str = "GET", payment_signature: str = "", body: Optional[dict] = None) -> str:
        with X402Client() as client:
            result = client.payable_call(url, method=method, payment_signature=payment_signature, body=body)
        # Never echo the signature back in full
        result["payment_signature_sent"] = bool(payment_signature)
        return json.dumps(result, indent=2)
