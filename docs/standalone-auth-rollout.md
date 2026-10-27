# SAS PRO standalone authentication rollout

## Current state

The standalone account and email-delivery services are intentionally not mounted in `app/main.py`. Telegram login, subscriptions, Stars payments, and the current Mini App remain the active path.

## Required before exposing endpoints

- [ ] Run the branch CI workflow and inspect the full test output; a missing status is not a passing result.
- [ ] Add HTTP routes in a separate router and keep them unmounted until the route tests pass.
- [ ] Use HTTPS-only, HttpOnly, Secure, SameSite cookies for browser sessions; define a CSRF strategy before enabling state-changing cookie-authenticated requests.
- [ ] Add per-IP and per-account rate limits for login, registration, verification, and password-reset requests.
- [ ] Return generic public responses for password-reset requests and avoid account enumeration.
- [ ] Send verification/reset emails before committing any public success response. Never log raw tokens.
- [ ] Configure `STANDALONE_SMTP_HOST`, `STANDALONE_SMTP_PORT`, `STANDALONE_EMAIL_FROM`, `STANDALONE_SMTP_USERNAME`, `STANDALONE_SMTP_PASSWORD`, `STANDALONE_SMTP_USE_TLS`, and `STANDALONE_SMTP_USE_SSL` in the deployment environment. Do not commit credentials.
- [ ] Configure the public HTTPS base URL and verify generated links point only to the intended SAS domain.
- [ ] Add security headers, request-size limits, and production-safe error handling.
- [ ] Test registration, email verification, login/logout, expired/replayed tokens, password reset, session revocation, rate limits, and Telegram regression.
- [ ] Review database migration/backup and rollback steps before any production rollout.

## Rollout sequence

1. Keep this branch isolated; do not merge or deploy automatically.
2. Get CI green and review failures, not just the workflow badge.
3. Configure SMTP secrets in the target environment without exposing them in logs.
4. Mount the standalone router behind a feature flag only after route tests and security review pass.
5. Test on staging using a separate database and email recipient allowlist.
6. Verify Telegram bot, current Mini App, subscription checks, and payment callbacks still work.
7. Enable standalone sign-up gradually; keep a rollback switch that disables standalone routes without affecting Telegram.
8. Only then consider a production merge/deploy with explicit approval.

## Safety invariants

- Existing Telegram `users`, `subscriptions`, and `payments` remain unchanged.
- Standalone tokens are stored as digests and must be single-use where applicable.
- An unverified or inactive account cannot log in.
- Password reset revokes active sessions.
- No production readiness claim is made until CI and staging checks are verified.
