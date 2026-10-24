# TradingAgents-inspired review for SAS PRO

## Decision

Use the TradingAgents repository as an architectural reference, not as a runtime dependency in the first iteration. SAS PRO already has a production radar, provider pools, an evidence-constrained AI news analyst, and explicit guardrails that AI must not add or remove radar candidates.

## Proposed isolated rollout

1. **Shadow analysis only**: add an optional review stage that runs only on candidates already accepted by the existing SAS technical gates. It must not change candidate selection, order, publication, subscription, market-data providers, or existing API keys.
2. **Analyst perspectives**: structure the review into technical/momentum, news/catalyst, and risk/bear-case perspectives. Each perspective must cite only supplied evidence and mark missing/stale data as unknown.
3. **Evidence and output validation**: reject unsupported claims, fabricated prices/targets/stops, invented news, and source IDs not present in the input. Keep original source URLs and timestamps.
4. **Bounded costs**: disabled by default; cap candidates per cycle, concurrency, timeouts, retries, and cache lifetime. Reuse existing configured LLM provider pools; add no new provider or data source in the initial phase.
5. **Shadow evaluation**: persist review output separately from the radar decision, then compare it against subsequent observed outcomes. No automatic learning or live trade execution.
6. **Promotion gate**: enable user-facing labels only after tests pass and a sufficiently large shadow sample shows the review adds useful, measurable information without increasing false positives or provider failures.

## Non-negotiable safeguards

- Existing SAS radar filters and scheduling remain authoritative.
- AI review is explanatory only and cannot add, remove, or reorder opportunities.
- No change to market-data sources, API keys, subscriptions, Telegram Stars, or user entitlements.
- If the AI provider is unavailable, the radar continues normally.
- No trading orders or buy/sell recommendations are generated.
- All data must include source and freshness metadata; unknown is preferable to an invented answer.

## Acceptance checks

- Unit tests prove disabled mode makes no provider calls.
- Unit tests prove a failed/timed-out review cannot block or alter a radar result.
- Validation tests reject unsupported source references and price/target/stop claims.
- Regression tests confirm candidate IDs, order, and publication decisions are unchanged with the feature disabled and in shadow mode.
- CI and deployment smoke tests pass before any production rollout.

## Why not install the full framework immediately?

TradingAgents is a research framework with its own data-tool and model orchestration assumptions. Pulling it into the production scanner immediately would add dependency and runtime complexity before proving value for SAS PRO's low-priced US-equity universe. An isolated, feature-flagged adaptation lets us evaluate the multi-agent idea without risking the existing service.
