# TradingView Unified MCP — SAS PRO integration plan

## Current change

This branch changes the SAS PRO scanner's configured price range to $0.30–$6.00 and adds regression tests for both inclusive boundaries and out-of-range rejection.

## Integration approach

TradingView Unified MCP is to be evaluated as an optional, read-only research source, not as a replacement for the current scanner. The Python application must continue to run when the MCP process or TradingView browser is unavailable.

Proposed stages:

1. Run the upstream project's source checks, source audit, test suite, and security review in an isolated workspace.
2. Run its public screener/read tools in read-only mode and record response shape, symbol/exchange identity, quote timestamp, delay metadata, and errors.
3. Add a separate adapter behind an explicit feature flag only after the upstream tool schema and transport have been verified. Do not expose browser mutation tools to SAS PRO.
4. Compare candidate sets and prices against current SAS PRO sources in shadow mode. Do not publish MCP-only candidates during this stage.
5. Enable candidate enrichment only if source timestamps, venue identity, error handling, rate limits, and duplicate suppression pass acceptance tests.

## Safety requirements

- Keep TV_READONLY=1; do not enable generic UI actions.
- Keep the browser debugger bound to loopback only. Do not publish port 9222 or the MCP HTTP port to the internet.
- If HTTP transport is used, require a randomly generated bearer token of at least 32 characters and bind to loopback.
- Never put TradingView session data, API keys, tokens, browser profiles, or runtime exports in Git.
- Treat missing/stale prices as unavailable; do not silently fall back to stale values.
- Enforce the $0.30–$6.00 range again inside SAS PRO, even if an upstream screener already filters it.
- MCP failure must not stop the existing scanner or alter existing publish gates.
- Do not treat an MCP quote or screen result as an investment recommendation.

## Acceptance tests before integration

- Inclusive price boundaries: $0.30 and $6.00 pass; values below/above fail.
- US venue and common-equity validation.
- Source and timestamp attached to every returned price.
- Stale/missing quotes rejected.
- Duplicate symbols merged deterministically.
- Timeout, invalid JSON, and unavailable browser produce a logged adapter error and a safe empty result.
- Shadow-mode comparison creates no channel posts and does not consume additional paid API credits.
- All existing unit and deployment smoke tests pass.

## Deployment status

This document is a plan, not evidence that TradingView Unified MCP has been installed on the production server. The current change is isolated on a feature branch for review. No production deployment has been performed by this change.
