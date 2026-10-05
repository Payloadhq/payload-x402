# payload-x402

Let your LangChain agent pay for APIs — and check whether paid endpoints actually work before trusting them.

Your agent finds a paid API. Is it reachable? Does it return a proper `402`? Is the challenge parseable — network, asset, amount, payTo? `payload-x402` gives your agent four tools that answer these questions, plus one for making operator-approved payable calls.

## Install

```bash
pip install payload-x402
```

## Quickstart

```python
from payload_x402 import (
    X402InspectTool,
    X402PaymentRequirementsTool,
    X402ReadinessTool,
    X402PayableCallTool,
)

tools = [
    X402InspectTool(),
    X402PaymentRequirementsTool(),
    X402ReadinessTool(),
    X402PayableCallTool(),
]

# In a LangChain agent:
# agent = create_agent(model, tools)
```

### 1. Inspect an endpoint — is it paid, and what version?

```python
tool = X402InspectTool()
print(tool.invoke({"url": "https://api.example.com/data"}))
# {"reachable": true, "status": 402, "is_402": true,
#  "x402_version": "v2", "challenge_parseable": true}
```

### 2. Get structured payment requirements

```python
tool = X402PaymentRequirementsTool()
print(tool.invoke({"url": "https://api.example.com/data"}))
# {"x402_version": "v2", "network": "base", "asset": "USDC",
#  "amount": "10000", "pay_to_truncated": "0xb3ecD1c3...B36A",
#  "required_headers": ["payment-signature"]}
```

### 3. Production-readiness checklist (PASS / DEGRADED / FAIL)

```python
tool = X402ReadinessTool()
print(tool.invoke({"url": "https://api.example.com/data"}))
# {"verdict": "PASS", "checks": {"reachable": {...}, "returns_402": {...}, ...}}
```

This is the LangChain equivalent of Payload's [x402-manifest-check](https://github.com/Payloadhq/x402-manifest-check) GitHub Action.

### 4. Make an operator-approved payable call

```python
tool = X402PayableCallTool()
print(tool.invoke({
    "url": "https://api.example.com/data",
    "method": "GET",
    # signature obtained + authorized out-of-band by the operator
    "payment_signature": "0x...",
}))
```

## Security: no keys held, ever

`payload-x402` **never signs payments and never holds private keys.** The payable-call tool takes a pre-supplied `payment_signature` as an explicit argument — the agent operator obtains and authorizes the payment out-of-band. The tool only attaches the signature to one HTTP call and never echoes it back in full. Use it only for operator-approved calls.

## How it works

- `X402Client` (`payload_x402/client.py`): read-only x402 HTTP client. Detects v1 (`X-PAYMENT`, `maxAmountRequired`) and v2 (`PAYMENT-REQUIRED` base64 header, atomic-unit amounts, CAIP-2 networks like `eip155:8453` → `base`).
- `check_readiness` (`payload_x402/readiness.py`): runs reachable → 402 → version → challenge parseable → manifest present, and returns PASS/DEGRADED/FAIL.
- Tools (`payload_x402/tools.py`): `BaseTool` subclasses with Pydantic `args_schema`, ready for any LangChain agent.

## Learn more

- [Payload x402 Paid API Starter Kit](https://payloadtools.gumroad.com/) — put per-call USDC payments on your own API
- [RevRule](https://payload-rail.fly.dev/revrule-console/) — when money moves, decide who earns what
- Built by [Payload](https://payloadhq.github.io/)

## License

MIT
