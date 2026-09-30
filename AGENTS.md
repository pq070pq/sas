# SAS PRO — Agent Instructions

## Project role

You are working on **SAS PRO**, an Arabic Telegram stock-radar and subscription platform.

GitHub is the **source of truth for the application code**. Do not assume production-server files are the development source.

## Core rules

- Work from the repository code and configuration.
- Never ask the user to manually edit files on OVH for normal development.
- Do not put secrets, API keys, Telegram tokens, passwords, SSH private keys, or `.env` contents into Git.
- Preserve existing functionality unless the requested change requires otherwise.
- Prefer small, reviewable changes.
- Do not claim a feature is deployed unless the GitHub deployment workflow has completed successfully.
- Do not claim a test passed unless it was actually run and passed.

## SAS PRO radar rules

- The US stock radar is the primary product.
- Stock discovery should prioritize the existing primary sources and fallback hierarchy already implemented in the repository.
- Twelve Data is an **optional fallback**, never the primary discovery source.
- Protect Twelve Data aggressively: respect `app/twelve_guard.py` and never bypass it with a direct Twelve Data HTTP request.
- Never add polling that unnecessarily consumes Twelve Data credits.
- Never invent prices, volume, liquidity, news, catalysts, targets, stop losses, probabilities, or financial metrics.
- If reliable data is unavailable, explicitly use wording such as `غير متوفر`, `غير واضح`, or `غير محسوب`.
- A news item must not be presented as the confirmed cause of a price move unless the available evidence supports that relationship.
- Targets and stop losses must be derived from available market/technical data, not arbitrary fixed numbers.
- Do not fabricate success probabilities.

## Report language and formatting

The user prefers Arabic-first Telegram reports that are compact, structured, readable, and professional.

Approved disclaimer:
`لا يعد توصية شراء أو بيع ويبقى قرار التداول وإدارة المخاطر مسؤولية المتداول ⚠️`

Approved Shariah disclaimer:
`🚫 شرعية السهم مسؤوليتك — تحقق منها قبل التداول ⛔`

If Shariah screening sources disagree, the status must be:
`غير واضح / يحتاج تحقق`

## Target-hit reports

A target notification must be sent only after the actual observed market price reaches or exceeds the target.

Do not report a target as achieved from a forecast or estimated price.

If a stop is hit, report the stop event rather than a target success.

Avoid duplicate target notifications for the same signal/target.

## Market scheduling

Respect `app/market_calendar.py` and its calendar-aware behavior.

Do not hard-code market-open/close assumptions when the repository already provides calendar-aware helpers.

Holiday/weekend radar must not run during an active stock-radar session.

The pre-market brief must remain calendar-aware.

## Telegram / subscriptions

Preserve Telegram WebApp authentication, subscription checks, channel access, trial/expiry behavior, and admin authorization.

Never weaken authorization checks to make a feature easier to implement.

## Data and database

Preserve existing database compatibility unless a migration is intentionally added.

Do not delete production data through application changes.

Prefer idempotent scheduled jobs and duplicate-safe database operations.

## Security

- Treat all external input as untrusted.
- Validate Telegram WebApp data and admin identity server-side.
- Do not expose API credentials in responses, logs, frontend code, or error messages.
- Use the minimum GitHub Actions permissions required.
- Avoid adding third-party GitHub Actions unless necessary and reviewed.
- If a workflow needs a credential, use GitHub Secrets rather than plaintext configuration. GitHub documents encrypted Actions secrets and least-privilege credentials as the recommended approach.

## Development workflow

1. Inspect the current implementation before editing.
2. Make the smallest change that satisfies the request.
3. Run relevant tests/lint/type checks available in the repository.
4. Inspect the diff for accidental changes and secrets.
5. Commit the change on a feature branch.
6. Open a pull request against `main`.
7. Merge only after required checks are successful.
8. Production deployment is handled by GitHub Actions; do not instruct the user to SSH into OVH for routine deployment.

## Important files

- `app/scanner.py`: radar discovery/scanning logic.
- `app/jobs.py`: scheduled jobs and radar outcome processing.
- `app/market_calendar.py`: market/session calendar.
- `app/holiday_radar.py`: closed-market asset radar.
- `app/market_brief.py`: pre-market brief.
- `app/twelve_guard.py`: Twelve Data circuit breaker/quota protection.
- `app/news.py`: news and catalyst selection.
- `app/panwatch.py`: PanWatch technical integration.
- `app/market.py`: market data access.
- `app/main.py`: FastAPI/API and report construction.
- `.github/workflows/`: CI, deployment, and repository synchronization.
- `.env.example`: configuration names only; never put real credentials here.

## Do not

- Do not bypass `twelve_guard`.
- Do not introduce Yahoo Finance for radar data.
- Do not add fake probability percentages.
- Do not invent catalysts.
- Do not create arbitrary technical levels just to make a report look complete.
- Do not expose secrets.
- Do not modify production directly as a substitute for changing GitHub.
