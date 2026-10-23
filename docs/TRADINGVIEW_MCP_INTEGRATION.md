# TradingView Unified MCP — SAS PRO integration plan

## Current change

This branch adds an opt-in read-only adapter for TradingView Unified MCP as a shadow discovery source. It is disabled by default (`TV_MCP_RADAR_ENABLED=0`) and makes no chart/layout changes. MCP-only symbols are measured for overlap and logged, but are deliberately not added to the active candidate universe.

## Integration approach

TradingView Unified MCP is to be evaluated as an optional, read-only research source, not as a replacement for the current scanner. The Python application must continue to run when the MCP process or TradingView browser is unavailable.

Proposed stages:

1. Run the upstream project's source checks, source audit, test suite, and security review in an isolated workspace.
2. Run its public screener/read tools in read-only mode and record response shape, symbol/exchange identity, quote timestamp, delay metadata, and errors.
3. The adapter calls only the upstream `screener_scan` read tool over authenticated loopback HTTP. It is behind an explicit feature flag and remains shadow-only; it must not expose browser mutation tools to SAS PRO.
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

## Current implementation limits

- The adapter pages at most 300 screener rows, normalizes them, and logs candidate count/overlap with existing providers. It does not merge those rows into the live candidate universe.
- The adapter does not verify quote timestamps because the upstream screener schema does not currently expose a dependable per-row timestamp. Therefore it should remain in shadow mode until freshness can be independently established.
- Upstream tool metadata labels `screener_scan` as implemented but not live-verified. Successful parsing does not prove live TradingView compatibility.
- A malformed response or transport error is raised to the existing discovery gather, which records the source error and continues with other sources.

## Deployment status

The adapter and tests are isolated on a feature branch for review. The feature flag is off by default. The MCP server has not been installed or configured on the production server, and no production deployment has been performed.
